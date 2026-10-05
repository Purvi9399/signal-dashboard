"""
Interpreters

An interpreter turns precomputed metrics into a reading through one COP's
question. It never calculates a metric.

RuleInterpreter   deterministic readings for the active COPS. This is the
                  dry-run baseline: same inputs, same decision, every run.
                  It is also what an LLM interpreter is compared against.

LLMInterpreter    the constrained instruction, the metrics as JSON, and a
                  JSON reply validated against the spec: an action the COP
                  is not permitted, a label it does not have, or a context
                  key it may not ask for is rejected, not obeyed.

Both return the same dict:
    interpretation, confidence, severity, action, action_reason, rationale,
    downstream, metric_id, trace_id, evidence_refs, request_context
"""

import json, os, urllib.request

from .models import ATTENTION, ACTIONS

SEV = {"NORMAL": "NONE", "UNKNOWN": "NONE", "WARNING": "LOW", "EXCEPTION": "MEDIUM", "CRITICAL": "HIGH"}
RANK = {"NONE": 0, "LOW": 1, "MEDIUM": 2, "HIGH": 3, "CRITICAL": 4}
EVIDENCE_LEVEL = {"SUFFICIENT": 0.9, "DEGRADED": 0.6, "INSUFFICIENT": 0.3}


def _by(events):
    return {e.metric_id: e for e in events}

def _hot(events, keys=None, states=ATTENTION):
    return [e for e in events if e.state in states and (keys is None or e.metric_id in keys)]

def _ratio(e):
    if e.value is None or not e.threshold:
        return 0.0
    v, t = float(e.value), float(e.threshold)
    return v / t if e.direction != "below" else (t / v if v else 99.0)

def _out(interp, conf, sev, action, reason, rationale, downstream=(), driver=None, request=()):
    return {"interpretation": interp, "confidence": round(conf, 2), "severity": sev,
            "action": action, "action_reason": reason, "rationale": rationale,
            "downstream": list(downstream),
            "metric_id": driver.metric_id if driver else None,
            "trace_id": driver.trace_id if driver else None,
            "evidence_refs": list(driver.evidence_refs) if driver else [],
            "request_context": list(request)}


class RuleInterpreter:
    name = "rules"
    costly = False

    def interpret(self, spec, events, upstream, context, evidence_conf):
        fn = getattr(self, spec.key, None)
        if fn is None:
            raise NotImplementedError(f"no rules for {spec.key}; it is not active")
        return fn(events, upstream, context or {}, evidence_conf)

    # ---------------------------------------------------------------- Runtime Monitoring
    def runtime_monitoring(self, events, up, ctx, _):
        m = _by(events)
        cov = m.get("telemetry.collector_coverage")
        unk = m.get("telemetry.unknown_share")
        bad = _hot(events, states=("EXCEPTION", "CRITICAL"))
        soft = _hot(events, states=("WARNING",))
        reasons = [f"{e.metric_id}={e.value} ({e.state})" for e in bad + soft]
        if cov and cov.state == "UNKNOWN":
            reasons.append(f"collector coverage unknown: {cov.features.get('reason')}")

        if (cov and cov.state in ("EXCEPTION", "CRITICAL")) or (unk and unk.state in ("EXCEPTION", "CRITICAL")):
            level = "INSUFFICIENT"
        elif bad or soft or (cov and cov.state == "UNKNOWN"):
            level = "DEGRADED"
        else:
            level = "SUFFICIENT"

        driver = (bad or soft or [cov or unk])[0] if (bad or soft or cov or unk) else None
        missing = (cov.features.get("missing") if cov else None) or []
        unknown_list = (unk.features.get("unknown") if unk else None) or []
        rationale = (f"evidence {level.lower()}; " + "; ".join(reasons[:6])) if reasons else "evidence complete"
        if unknown_list:
            rationale += f"; cannot judge: {', '.join(unknown_list[:8])}"
        if "collector_breakdown" in ctx:
            rationale += "; rows by collector: " + ", ".join(
                f"{r['collector']} {r['rows']}" for r in ctx["collector_breakdown"])
        request = ("collector_breakdown",) if missing and "collector_breakdown" not in ctx else ()

        if level == "INSUFFICIENT":
            r = _out(level, 0.9, "MEDIUM", "ALERT",
                     "downstream findings on this session rest on evidence that is not there; "
                     "an empty result here means 'could not see', not 'nothing happened'",
                     rationale, ("audit_traceability", "incident_response"), driver, request)
        elif level == "DEGRADED":
            r = _out(level, 0.9, "LOW", "WATCH",
                     "findings stand but carry reduced confidence", rationale,
                     ("audit_traceability",), driver, request)
        else:
            r = _out(level, 0.9, "NONE", "NONE", "", rationale, (), driver)
        r["evidence_confidence"] = EVIDENCE_LEVEL[level]
        return r

    # ---------------------------------------------------------------- Anomaly Detection
    PRIMARY = ("insights.token_outlier", "insights.duration_outlier", "path.transition_novelty",
               "activity.action_rate_ratio", "access.first_seen_mcp")

    def anomaly_detection(self, events, up, ctx, evid):
        m = _by(events)
        primary = [m[k] for k in self.PRIMARY if k in m]
        hot = sorted(_hot(primary, states=("EXCEPTION", "CRITICAL")), key=_ratio, reverse=True)
        support = _hot(events, ("insights.retry", "insights.repetition"), ("EXCEPTION", "CRITICAL"))
        judged = [e for e in primary if e.state != "UNKNOWN"]
        scale = min(1.0, evid / 0.9)

        if not judged:
            return _out("INDETERMINATE", 0.2 * scale, "NONE", "WATCH",
                        "no baseline or no basis for any deviation metric; unusualness cannot be judged",
                        "unknown: " + ", ".join(f"{e.metric_id} ({e.features.get('reason')})" for e in primary))
        if not hot:
            return _out("NORMAL", 0.8 * scale, "NONE", "NONE", "",
                        f"{len(judged)} deviation metrics within bounds" +
                        (f"; {len(primary) - len(judged)} could not be judged" if len(judged) < len(primary) else ""))

        driver = hot[0]
        novel = next((e for e in hot if e.metric_id == "path.transition_novelty"), None)
        if novel and "lane_path" not in ctx:
            return _out("ANOMALOUS", 0.5 * scale, SEV[novel.state], "REQUEST_CONTEXT",
                        "need the affected lane's path to read the novel transitions",
                        "", (), novel, ("lane_path",))

        mag = round(_ratio(driver), 2)
        parts = [f"{e.metric_id}={e.value} vs {e.threshold} ({e.state})" for e in hot]
        if support:
            parts.append("alongside " + ", ".join(f"{e.metric_id}={e.value}" for e in support))
        if novel:
            parts.append("novel: " + ", ".join(novel.features.get("novel", [])[:5]))
        if "lane_path" in ctx:
            steps = [x["step"] for x in ctx["lane_path"]]
            parts.append(f"lane has {len(steps)} steps, {sum(1 for x in ctx['lane_path'] if x.get('failed'))} failed")

        down = ["incident_response", "classification_tagging"]
        if any(e.metric_id == "access.first_seen_mcp" for e in hot):
            down.append("policy_enforcement")           # new reach is a permission question, not ours
        loud = len(hot) >= 2 or any(e.state == "CRITICAL" for e in hot)
        sev = max((SEV[e.state] for e in hot), key=RANK.get)
        return _out("ANOMALOUS", min(0.95, 0.6 + 0.1 * len(hot)) * scale, sev,
                    "ALERT" if loud else "WATCH",
                    f"deviation x{mag} on {driver.metric_id}; unusual is not prohibited, "
                    "permission is Policy Enforcement's question",
                    "; ".join(parts), down, driver)

    # ---------------------------------------------------------------- Incident Response
    def incident_response(self, events, up, ctx, evid):
        m = _by(events)
        rm, ad = up.get("runtime_monitoring"), up.get("anomaly_detection")
        pe, idd = up.get("policy_enforcement"), up.get("interaction_discovery")
        exposure = _hot(events, ("insights.secrets", "insights.sensitive_unasked"), ("EXCEPTION", "CRITICAL"))
        blocked = m.get("insights.blocked")
        anomalous = ad is not None and ad.interpretation == "ANOMALOUS"
        used = [d for d in (rm, ad, pe, idd) if d is not None]
        # upstream confidences already carry the evidence discount; don't apply it twice
        base = min([d.confidence for d in used if d.confidence] or [0.8 * min(1.0, evid / 0.9)])

        def done(interp, sev, action, reason, driver=None):
            return _out(interp, base, sev, action, reason,
                        "inputs: " + ", ".join(f"{d.cop}={d.interpretation}/{d.action}" for d in used) +
                        ("; " + ", ".join(f"{e.metric_id}={e.value}" for e in exposure) if exposure else ""),
                        (), driver)

        if pe is not None and pe.interpretation == "VIOLATION":
            return done("INCIDENT", "HIGH", "ALERT",
                        "explicit policy violation; on a live session this would be REQUEST_APPROVAL or BLOCK")
        if anomalous and exposure:
            return done("INCIDENT", "HIGH", "ESCALATE",
                        "unusual behaviour in a session that also reached credentials or secrets",
                        exposure[0])
        if exposure:
            return done("INCIDENT", "MEDIUM", "ALERT",
                        "credential or secret exposure without an anomaly; expected behaviour can still leak",
                        exposure[0])
        if anomalous and ad.action == "ALERT":
            return done("WATCH", "MEDIUM", "ALERT", "several deviations at once, no exposure or violation")
        if anomalous:
            return done("WATCH", "LOW", "WATCH", "single deviation, no exposure or violation")
        if blocked is not None and blocked.state in ATTENTION:
            return done("WATCH", "LOW", "WATCH", "a deny rule fired; the control worked, the attempt is worth noting",
                        blocked)
        if rm is not None and rm.interpretation == "INSUFFICIENT":
            return done("WATCH", "LOW", "WATCH",
                        "no findings, but evidence is insufficient: absence of findings is not absence of risk")
        return done("NO_INCIDENT", "NONE", "NONE", "")


INSTRUCTION = """You are the {name} COP.

Your behavioural question is:
"{question}"

You are NOT responsible for calculating metrics. They have already been calculated deterministically.

You receive metric names, values, thresholds, normal/warning/exception state, evidence references, decisions from upstream COPS, and limited context where you requested it.

Your job is only:
1. inspect the supplied metrics;
2. interpret what they mean through your behavioural question;
3. state confidence (0-1). Evidence confidence from Runtime Monitoring is {evid}; do not exceed it by much;
4. choose one allowed action;
5. name another COP only if another governance lens is required.

Do not recalculate metrics. Do not invent unsupported facts. A metric in state UNKNOWN means it could not be observed, not that nothing happened. Unusual is not prohibited.

Allowed interpretations: {labels}
Allowed actions: {actions}
COPS you may pass to: {downstream}
Context you may request (at most once): {context}

Reply with JSON only:
{{"interpretation": "...", "confidence": 0.0, "severity": "NONE|LOW|MEDIUM|HIGH|CRITICAL",
  "action": "...", "action_reason": "...", "rationale": "...", "downstream": [],
  "metric_id": "the metric that drove this or null", "request_context": []}}"""


class LLMInterpreter:
    costly = True

    def __init__(self, model=None, key=None):
        self.model = model or os.environ.get("COPS_MODEL")
        self.key = key or os.environ.get("ANTHROPIC_API_KEY")
        if not (self.model and self.key):
            raise RuntimeError("set COPS_MODEL and ANTHROPIC_API_KEY")
        self.name = f"llm:{self.model}"

    def interpret(self, spec, events, upstream, context, evidence_conf):
        system = INSTRUCTION.format(
            name=spec.name, question=spec.behavioural_question, evid=evidence_conf,
            labels=", ".join(spec.interpretations), actions=", ".join(spec.permitted_actions),
            downstream=", ".join(spec.downstream_cops) or "none",
            context=", ".join(spec.context_keys) if not context else "none (already provided)")
        user = json.dumps({
            "metrics": [e.brief() for e in events],
            "upstream": {k: {"interpretation": d.interpretation, "action": d.action,
                             "confidence": d.confidence, "rationale": d.rationale[:300]}
                         for k, d in upstream.items()},
            "context": context or {}}, default=str)
        req = urllib.request.Request(
            "https://api.anthropic.com/v1/messages",
            data=json.dumps({"model": self.model, "max_tokens": 600, "system": system,
                             "messages": [{"role": "user", "content": user}]}).encode(),
            headers={"x-api-key": self.key, "anthropic-version": "2023-06-01",
                     "content-type": "application/json"})
        with urllib.request.urlopen(req, timeout=60) as r:
            text = "".join(b.get("text", "") for b in json.load(r)["content"])
        out = json.loads(text.strip().removeprefix("```json").removesuffix("```").strip())
        return self.validate(spec, events, out)

    @staticmethod
    def validate(spec, events, out):
        if out.get("interpretation") not in spec.interpretations:
            raise ValueError(f"interpretation {out.get('interpretation')!r} not allowed")
        if out.get("action") not in spec.permitted_actions or out["action"] not in ACTIONS:
            raise ValueError(f"action {out.get('action')!r} not permitted")
        bad = [c for c in out.get("downstream", []) if c not in spec.downstream_cops]
        if bad:
            raise ValueError(f"downstream {bad} not permitted")
        bad = [c for c in out.get("request_context", []) if c not in spec.context_keys]
        if bad:
            raise ValueError(f"context {bad} not permitted")
        ids = {e.metric_id: e for e in events}
        driver = ids.get(out.get("metric_id"))
        if out.get("metric_id") and not driver:
            raise ValueError(f"cites metric {out['metric_id']!r} it was not given")
        out["confidence"] = max(0.0, min(1.0, float(out.get("confidence", 0))))
        out["trace_id"] = driver.trace_id if driver else None
        out["evidence_refs"] = list(driver.evidence_refs) if driver else []
        return out
