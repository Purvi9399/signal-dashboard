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

    # ---------------------------------------------------------------- Resource Management
    # Two different complaints, kept apart. Burn is consumption that is large.
    # Waste is time and effort spent without producing anything. A session can
    # be one without the other, and they call for different responses.
    BURN = ("insights.token_outlier", "activity.action_rate_ratio")
    WASTE = ("insights.duration_outlier", "insights.stalled")

    def resource_management(self, events, up, ctx, evid):
        watched = [e for e in events if e.metric_id in self.BURN + self.WASTE]
        judged = [e for e in watched if e.state not in ("UNKNOWN", "NOT_APPLICABLE")]
        hot = sorted(_hot(watched, states=("EXCEPTION", "CRITICAL")), key=_ratio, reverse=True)
        soft = _hot(watched, states=("WARNING",))
        scale = min(1.0, evid / 0.9)

        if not judged:
            return _out("INDETERMINATE", 0.2 * scale, "NONE", "WATCH",
                        "no consumption metric could be judged; a missing meter is not "
                        "evidence of thrift",
                        "unknown: " + ", ".join(
                            f"{e.metric_id} ({e.features.get('reason')})" for e in watched))

        if not hot and not soft:
            return _out("NORMAL", 0.8 * scale, "NONE", "NONE", "",
                        f"{len(judged)} consumption metrics within bounds" +
                        (f"; {len(watched) - len(judged)} could not be judged"
                         if len(judged) < len(watched) else ""))

        burn = [e for e in hot if e.metric_id in self.BURN]
        waste = [e for e in hot if e.metric_id in self.WASTE]
        driver = (burn or waste or soft)[0]

        # Consumption outweighs waste: spending too much matters more than
        # spending slowly, and a session doing both reads as excessive.
        reading = "EXCESSIVE" if burn else ("INEFFICIENT" if waste else "INEFFICIENT")

        parts = [f"{e.metric_id}={e.value} vs {e.threshold} ({e.state})" for e in hot + soft]
        if burn and waste:
            parts.append("consuming heavily and stalling in the same session")
        mult = driver.features.get("multiple") or driver.features.get("ratio")
        if mult:
            parts.append(f"{mult}x the usual for this tool")
        # Entitlement is not knowable yet, and the reading should not imply it is.
        parts.append("no quota configured, so this is consumption above the estate's "
                     "own normal, not consumption above an allowance")

        sev = max((SEV[e.state] for e in hot or soft), key=RANK.get)
        loud = len(hot) >= 2 or any(e.state == "CRITICAL" for e in hot)
        down = ["risk_mitigation"] if burn else []
        if waste:
            down.append("workload_optimization")

        return _out(reading, min(0.9, 0.6 + 0.1 * len(hot + soft)) * scale, sev,
                    "ALERT" if loud else "WATCH",
                    "cost and waste are governance concerns in themselves; whether the work "
                    "was permitted is Policy Enforcement's question",
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



# ======================================================================
# Interpreters added for the remaining active COPS.
#
# Attached to the class rather than nested in it so the four that already
# work are untouched. RuleInterpreter.interpret dispatches on spec.key, so
# an attached method is found the same way a nested one is.
# ======================================================================

def _policy_enforcement(self, events, up, ctx, evid):
    """Was it permitted? Not: was it unusual, and not: was it harmful.

    There is no policy store yet, so this reads conditions rather than rules:
    a matched deny rule, a boundary crossing, a credential reached with nobody
    asked. Those are policy-relevant facts, and the rationale says plainly
    that they are not the same as a rule having been evaluated.
    """
    m = _by(events)
    watched = ("insights.blocked", "insights.outside", "insights.sensitive_unasked",
               "insights.secrets", "oversight.declined_inferred")
    present = [m[k] for k in watched if k in m]
    judged = [e for e in present if e.state not in ("UNKNOWN", "NOT_APPLICABLE")]
    hot = sorted(_hot(present, states=("EXCEPTION", "CRITICAL")), key=_ratio, reverse=True)
    scale = min(1.0, evid / 0.9)

    if not judged:
        return _out("UNCERTAIN", 0.2 * scale, "NONE", "WATCH",
                    "nothing here can be read as permitted or prohibited; "
                    "an unjudgeable action is not an allowed one",
                    "unknown: " + ", ".join(
                        f"{e.metric_id} ({e.features.get('reason')})" for e in present))

    unknown = [e for e in present if e.state == "UNKNOWN"]
    if not hot:
        if unknown:
            # Some conditions could not be evaluated at all. Saying ALLOWED here
            # would be reading silence as permission, which is the one thing
            # this COP must never do.
            return _out("UNCERTAIN", 0.3 * scale, "LOW", "WATCH",
                        "the conditions that could be read are within bounds, but "
                        "others could not be read at all; this is not a finding "
                        "that the action was permitted",
                        f"{len(judged)} within bounds; cannot judge " +
                        ", ".join(f"{e.metric_id} ({e.features.get('reason')})"
                                  for e in unknown),
                        ["audit_traceability"])
        return _out("ALLOWED", 0.75 * scale, "NONE", "NONE", "",
                    f"all {len(judged)} policy-relevant conditions within bounds; "
                    f"no deny rule matched, no boundary crossed")

    by_id = {e.metric_id: e for e in hot}
    driver = hot[0]
    parts = [f"{e.metric_id}={e.value} vs {e.threshold} ({e.state})" for e in hot]
    down = ["incident_response", "audit_traceability"]

    # A rule that fired, or a line that was crossed, is a violation. Sensitive
    # access with nobody asked is a missing approval, which is a different
    # failure and calls for a different response.
    violation = ("insights.blocked" in by_id or "insights.outside" in by_id
                 or "insights.secrets" in by_id)
    if "insights.secrets" in by_id:
        down.append("risk_mitigation")

    if violation:
        reading, action = "VIOLATION", "ALERT"
        reason = ("a deny rule matched or a boundary was crossed; this is a "
                  "permission finding, not a statistical one")
    else:
        reading, action = "APPROVAL_REQUIRED", "REQUEST_APPROVAL"
        reason = ("sensitive access with no approval recorded anywhere in the "
                  "session; the access may be legitimate and nobody was asked")

    parts.append("read from detected conditions rather than from an evaluated "
                 "rule set, because no policy store exists yet")
    sev = max((SEV[e.state] for e in hot), key=RANK.get)
    return _out(reading, min(0.9, 0.6 + 0.1 * len(hot)) * scale, sev, action, reason,
                "; ".join(parts), down, driver)


def _interaction_discovery(self, events, up, ctx, evid):
    """Who reached what, by which route, and how far did it travel?

    Structure only. Whether a route was permitted belongs to Policy
    Enforcement, which is why a new one is handed there rather than judged
    here.
    """
    m = _by(events)
    linkage = m.get("telemetry.lane_linkage")
    novel = m.get("access.first_seen_mcp")
    lanes = m.get("interaction.subagent_lanes")
    outside = m.get("insights.outside")
    present = [e for e in (linkage, novel, lanes, outside, m.get("interaction.mcp_servers"))
               if e is not None]
    judged = [e for e in present if e.state not in ("UNKNOWN", "NOT_APPLICABLE")]
    scale = min(1.0, evid / 0.9)

    if not judged:
        return _out("PATH_PARTIAL", 0.2 * scale, "NONE", "WATCH",
                    "no route can be reconstructed from what arrived",
                    "unknown: " + ", ".join(
                        f"{e.metric_id} ({e.features.get('reason')})" for e in present))

    # Lanes that cannot be attributed mean the edges between agents are lost,
    # even though the agents themselves are known to have run.
    if linkage is not None and linkage.state in ("EXCEPTION", "CRITICAL"):
        seen = (linkage.features or {}).get("lanes_seen")
        return _out("PATH_PARTIAL", 0.6 * scale, SEV[linkage.state], "WATCH",
                    "the agents are known; the handoffs between them are not",
                    f"lane linkage {linkage.value} vs {linkage.threshold}" +
                    (f"; {seen} lanes started and cannot be attributed" if seen else ""),
                    ["audit_traceability"], linkage)

    if novel is not None and novel.state in ("EXCEPTION", "CRITICAL"):
        server = (novel.features or {}).get("server", "a server not seen before")
        return _out("NEW_ROUTE", 0.8 * scale, SEV[novel.state], "PASS_TO_COP",
                    "a route that did not exist before now does; whether it is "
                    "permitted is Policy Enforcement's question",
                    f"first contact with {server}",
                    ["policy_enforcement", "audit_traceability"], novel)

    spreading = [e for e in (outside, lanes)
                 if e is not None and e.state in ("EXCEPTION", "CRITICAL")]
    if spreading:
        driver = spreading[0]
        return _out("PROPAGATING", 0.7 * scale, SEV[driver.state], "PASS_TO_COP",
                    "activity reached beyond where it started",
                    "; ".join(f"{e.metric_id}={e.value} vs {e.threshold}" for e in spreading),
                    ["policy_enforcement", "risk_mitigation"], driver)

    return _out("CONTAINED", 0.8 * scale, "NONE", "NONE", "",
                f"{len(judged)} route measures within bounds" +
                (f"; {len(present) - len(judged)} could not be judged"
                 if len(judged) < len(present) else ""))


def _audit_traceability(self, events, up, ctx, evid):
    """Can what happened be attributed and reconstructed?

    This COP also owns the case nobody else does: an action another COP could
    not judge is still an action in the record, and leaving it unowned would
    let a refusal evaporate.
    """
    m = _by(events)
    unattributed = m.get("insights.unattributed")
    turn = m.get("telemetry.turn_linkage")
    lane = m.get("telemetry.lane_linkage")
    agree = m.get("telemetry.source_agreement")
    present = [e for e in (unattributed, turn, lane, agree) if e is not None]
    judged = [e for e in present if e.state not in ("UNKNOWN", "NOT_APPLICABLE")]
    hot = sorted(_hot(present, states=("EXCEPTION", "CRITICAL")), key=_ratio, reverse=True)
    soft = _hot(present, states=("WARNING",))
    scale = min(1.0, evid / 0.9)

    # An upstream COP that could not decide leaves something in the record
    # that nobody has accounted for.
    blocked_up = [k for k, v in (up or {}).items()
                  if getattr(v, "decision_status", None) == "BLOCKED_BY_EVIDENCE"]

    if not judged and not blocked_up:
        return _out("UNATTRIBUTABLE", 0.3 * scale, "MEDIUM", "ALERT",
                    "nothing here can be attributed or reconstructed",
                    "unknown: " + ", ".join(
                        f"{e.metric_id} ({e.features.get('reason')})" for e in present))

    parts = [f"{e.metric_id}={e.value} vs {e.threshold} ({e.state})" for e in hot + soft]
    if blocked_up:
        parts.append(f"{', '.join(blocked_up)} could not reach a determination, so a "
                     f"consequential action sits in the record unjudged")

    severe = [e for e in hot if e.state == "CRITICAL"]
    if severe or (unattributed is not None and unattributed.state == "CRITICAL"):
        return _out("UNATTRIBUTABLE", 0.85 * scale, "HIGH", "ALERT",
                    "actions in this session cannot be tied to whoever took them",
                    "; ".join(parts), ["governance_architecture"],
                    (severe or hot)[0])
    if hot or blocked_up:
        return _out("BROKEN_TRACE", 0.8 * scale,
                    max((SEV[e.state] for e in hot), key=RANK.get) if hot else "MEDIUM",
                    "ALERT",
                    "the record has holes where a governance-critical action should be",
                    "; ".join(parts), ["governance_architecture"],
                    hot[0] if hot else None)
    if soft:
        return _out("PARTIAL", 0.75 * scale, "LOW", "WATCH",
                    "reconstructable, with gaps that do not yet break attribution",
                    "; ".join(parts), (), soft[0])
    return _out("COMPLETE", 0.85 * scale, "NONE", "NONE", "",
                f"{len(judged)} attribution measures intact")


def _classification_tagging(self, events, up, ctx, evid):
    """What kind of thing is this?

    Kind only. Not how serious, not what to do. It records and marks; its spec
    permits no other action, which is the point of having it separate.
    """
    m = _by(events)
    # Which metric fired decides the family. Boundary before oversight, since
    # a crossing describes the event better than the approval that was missing.
    FAMILY = (("insights.outside", "BOUNDARY"),
              ("insights.sensitive_unasked", "BOUNDARY"),
              ("insights.secrets", "INTEGRITY"),
              ("insights.blocked", "OVERSIGHT"))
    present = [m[k] for k, _ in FAMILY if k in m]
    judged = [e for e in present if e.state not in ("UNKNOWN", "NOT_APPLICABLE")]
    hot = _hot(present, states=("EXCEPTION", "CRITICAL"))
    scale = min(1.0, evid / 0.9)

    if not hot:
        return _out("UNCLASSIFIED", (0.7 if judged else 0.2) * scale, "NONE", "NONE", "",
                    f"{len(judged)} classifiable conditions, none above bounds"
                    if judged else "nothing classifiable could be judged")

    ranked = [(fam, m[k]) for k, fam in FAMILY
              if k in m and m[k] in hot]
    family, driver = ranked[0]
    others = sorted({f for f, _ in ranked[1:]})
    return _out(family, min(0.9, 0.65 + 0.1 * len(ranked)) * scale,
                max((SEV[e.state] for _, e in ranked), key=RANK.get),
                "NONE", "",
                f"classified as {family} on {driver.metric_id}={driver.value}" +
                (f"; also carries {', '.join(others)}" if others else "") +
                "; kind only, severity and response belong elsewhere",
                ["governance_intelligence"], driver)


RuleInterpreter.policy_enforcement = _policy_enforcement
RuleInterpreter.interaction_discovery = _interaction_discovery
RuleInterpreter.audit_traceability = _audit_traceability
RuleInterpreter.classification_tagging = _classification_tagging

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
