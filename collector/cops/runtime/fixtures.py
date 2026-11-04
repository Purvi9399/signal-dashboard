"""
Scripted model replies for repeatable tests. Every reply here is SIMULATED.

A script is a list of steps per COP. Step i answers that COP's i-th model call
in a case. A step is a function of the payload the gateway actually sent, so a
scripted model can only cite tool calls and metric rows it was shown. Nothing
here reads the computational baseline or the expected outcome.

The point of scripting is repeatability, not intelligence: these replies
stand in for a model so the harness, the tool policy and the verifiers can be
exercised deterministically. They demonstrate the control flow of an agentic
investigation, not the quality of any real model.
"""

from .verifiers import _get_path

ATTN = ("WARNING", "EXCEPTION", "CRITICAL")


# ---------------------------------------------------------------- payload readers

def calls(p, tool=None, ok=True):
    out = [c for c in p.get("tool_results", []) if (tool is None or c["tool"] == tool)]
    return [c for c in out if c["status"] == "OK"] if ok else out


def tid(p, tool):
    c = calls(p, tool)
    return c[-1]["tool_call_id"] if c else None


def res(p, tool):
    c = calls(p, tool)
    return (c[-1]["result"] or {}) if c else {}


def row(p, metric):
    return next((r for r in p["evidence"] if r["metric"] == metric), None)


def attention(p):
    return [r for r in p["evidence"] if r["evidence"] == "OBSERVED" and r["state"] in ATTN]


# ---------------------------------------------------------------- claim builders

def cm(p, metric, statement=None):
    """A metric_state claim stating the row's actual state."""
    r = row(p, metric)
    return {"statement": statement or f"{metric} is {r['state']}", "kind": "metric_state",
            "metric_id": metric, "asserted_state": r["state"],
            "evidence_refs": [f"metric:{metric}"]}


def cm_false(metric, state, statement):
    """A metric claim asserting a state the data does not have (for verifier tests)."""
    return {"statement": statement, "kind": "metric_state", "metric_id": metric,
            "asserted_state": state, "evidence_refs": [f"metric:{metric}"]}


def ct(p, tool, field, statement=None, value=None):
    """A tool_observation claim. value defaults to what the tool returned."""
    got = _get_path(res(p, tool), field) if value is None else value
    return {"statement": statement or f"{tool}.{field} = {got}", "kind": "tool_observation",
            "tool_call_id": tid(p, tool), "result_field": field, "asserted_value": got,
            "evidence_refs": [f"tool:{tid(p, tool)}"]}


def cu(p, cop, statement=None):
    u = next(x for x in p["upstream"] if x["cop"] == cop)
    return {"statement": statement or f"{cop} concluded {u['label']}", "kind": "upstream_finding",
            "upstream_cop": cop, "upstream_label": u["label"],
            "evidence_refs": [f"finding:{u['finding_id']}"]}


def cmem(record_id, statement):
    return {"statement": statement, "kind": "memory_precedent", "record_id": record_id,
            "evidence_refs": [f"memory:{record_id}"]}


# ---------------------------------------------------------------- reply builders

def req(tool, purpose, **args):
    return {"tool": tool, "args": args, "purpose": purpose}


def plan(hypothesis, known, unknown, uncertain, *requests):
    return {"known": known, "unknown": unknown, "uncertain": uncertain, "hypothesis": hypothesis,
            "tool_requests": list(requests)}


def more(hypothesis, changed, reason, *requests):
    return {"hypothesis": hypothesis, "hypothesis_changed": changed, "change_reason": reason,
            "decision": "more_evidence", "tool_requests": list(requests), "missing_evidence": []}


def conclude(hypothesis, label, confidence, claims, rationale, action="NONE", action_reason="",
             detail=None, alternatives=(), missing=(), changed=False, change_reason=""):
    return {"hypothesis": hypothesis, "hypothesis_changed": changed, "change_reason": change_reason,
            "decision": "conclude", "tool_requests": [], "missing_evidence": [],
            "finding": {"label": label, "confidence": confidence, "claims": claims,
                        "rationale": rationale, "alternative_explanations": list(alternatives),
                        "missing_evidence": list(missing), "proposed_action": action,
                        "action_reason": action_reason, "detail": detail or {}}}


def inconclusive(hypothesis, missing, reason=""):
    return {"hypothesis": hypothesis, "hypothesis_changed": False, "change_reason": reason,
            "decision": "inconclusive", "tool_requests": [], "missing_evidence": list(missing)}


# ---------------------------------------------------------------- scripts

class Script:
    """{cop: [step(payload) -> reply]}. Unscripted COPs use `default`."""

    def __init__(self, steps=None, default=None, label="scripted"):
        self.steps = dict(steps or {})
        self.default = default
        self.label = label

    def __call__(self, cop, purpose, payload, n):
        seq = self.steps.get(cop)
        if seq is None:
            if self.default is None:
                return inconclusive("no script for this COP", ["scripted reply"])
            seq = self.default(cop)
        if n >= len(seq):
            return inconclusive("script exhausted", ["scripted reply"])
        step = seq[n]
        return step(payload) if callable(step) else step


def generic_careful(cop):
    """For COPs a scenario does not focus on. Looks at up to two argument-free
    tools; concludes the quiet label only when nothing is in an attention state
    and some evidence was actually observed; otherwise says inconclusive."""
    from ..specs import BY_KEY
    from .tools import TOOLS
    spec = BY_KEY[cop]

    def p0(p):
        free = [t["tool"] for t in p["tools_available"]
                if not any(a.get("required") for a in t["args"].values())][:2]
        return plan(f"{cop}: nothing specific suspected", [f"{len(p['evidence'])} metric rows supplied"],
                    ["whether the case's sources support a reading"], [],
                    *[req(t, "establish the baseline facts") for t in free])

    def p1(p):
        if attention(p):
            return inconclusive("attention states present; no scripted interpretation for them",
                                [r["metric"] for r in attention(p)])
        ok = calls(p)
        missing = [c["tool"] for c in calls(p, ok=False) if c["status"] == "MISSING_DEPENDENCY"]
        normal = [r for r in p["evidence"] if r["evidence"] == "OBSERVED" and r["state"] == "NORMAL"]
        if not ok and not normal:
            return inconclusive("no source could be read", missing or ["observed evidence"])
        claims = [cm(p, r["metric"]) for r in normal[:5]]
        if ok:
            key = next((k for k, v in (ok[0]["result"] or {}).items()
                        if isinstance(v, (int, float, str, bool)) and not isinstance(v, bool)), None)
            if key:
                claims.append(ct(p, ok[0]["tool"], key))
        if not claims:
            return inconclusive("nothing citable", missing)
        return conclude(f"{cop}: no deviation in what could be observed", spec.interpretations[0], 0.6,
                        claims, "quiet reading from the observed rows and tool results "
                                 "(generic simulated fixture)", missing=missing)
    return [p0, p1]
