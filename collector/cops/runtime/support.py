"""Graded verification support: how far valid evidence supports a candidate finding.

Verification runs in two layers.

1. Hard checks (HARD_CHECKS). These are pass/fail conditions on validity: references
   resolve, provenance holds, and the facts the finding asserts match the data. A
   failure rejects the candidate whatever the rest of the evidence says. Mandatory
   policy and authority conditions (ACTION_HARD_CHECKS) are also hard, but they apply
   to the action, at authority_check, and are unchanged.
2. The graded evidential assessment (this module). Each COP has an explicit list of
   evidence components (PROFILES). Each component reads real evidence: metric rows,
   authoritative records returned by tools, verified upstream findings, the rule
   baseline and validated memory. It then says whether that evidence points to the
   candidate's proposition, points to its opposite, says nothing, or is unknown.

For candidate label L, with component weight w (relevance), reliability r and
strength s, all between 0 and 1:

    support  V = sum(w*r*s over components that support L) / sum(w)
    against  D = sum(w*r*s over components that oppose L)  / sum(w)
    unknown  U = 1 - V - D

U holds missing evidence, unreliable evidence and evidence that bears on nothing.
Missing evidence is never counted as evidence against. So low support for ANOMALOUS
is not evidence for NORMAL: NORMAL needs its own support, from metrics observed
quiet across the board.

The five graded dimensions are reported for every assessment:

- relevance: w, set per component;
- reliability: r;
- completeness: the weighted share of components that are known;
- consistency: V / (V + D);
- corroboration: the number of independent source kinds that support L.

The decision, in order:

1. Any hard check failed: REJECTED.
2. No proposition, because the label is INCONCLUSIVE or states uncertainty:
   INSUFFICIENT_EVIDENCE.
3. D >= reject_min and D > V: REJECTED. The evidence supports the opposite.
4. A component the COP requires for L is unknown, a graded floor check failed
   (SC-02, DS-01 or DS-02), or no direct component supports L:
   INSUFFICIENT_EVIDENCE.
5. V >= supported_min, corroboration >= 2 and D <= 1 - RHO_SUPPORTED: SUPPORTED.
6. V >= provisional_min and D < V: PROVISIONALLY_SUPPORTED. Its limitations are
   listed.
7. Otherwise INSUFFICIENT_EVIDENCE: the finding is undecided, not disproved.

The thresholds are derived from each COP's own component list (see thresholds()).
None of them was fitted to the test bench.
"""
import json

from ..interpret import EVIDENCE_LEVEL, RANK, SEV
from ..specs import BY_KEY, CONSUMES
from .contracts import ATTENTION_STATES, SUBSTANTIVE, INCONCLUSIVE as INCONCLUSIVE_LABEL
from .requirements import (REQUIREMENTS, REQUIRED, NOT_APPLICABLE, GATES_ALL_LABELS,
                           ARTIFACT_TOOLS, assess_requirements)

SUPPORT_VERSION = "support-1"

SUPPORTED = "SUPPORTED"
PROVISIONAL = "PROVISIONALLY_SUPPORTED"
REJECTED = "REJECTED"
INSUFFICIENT = "INSUFFICIENT_EVIDENCE"
DECISIONS = (SUPPORTED, PROVISIONAL, REJECTED, INSUFFICIENT)

# ---------------------------------------------------------------- hard vs graded checks
HARD_CHECKS = {
    "EV-01": "reference: the finding is well formed",
    "EV-02": "reference: every cited reference resolves",
    "ID-01": "provenance: cited metric rows carry provenance",
    "ID-02": "provenance: cited calls and records belong to this case and tenant",
    "MC-01": "factual: cited metric values recompute from raw observables",
    "SC-01": "factual: each claim matches the data it cites",
}
ACTION_HARD_CHECKS = {
    "PE-01": "mandatory policy basis for an intervening action",
    "AU-01": "the action is within the COP's authority",
    "AU-02": "approval is present where required",
    "EX-01": "an executed action's outcome was observed",
}
# These existing checks measure evidential sufficiency, not validity. In the graded
# layer they are minimum evidential requirements. Failing one leaves the evidence
# insufficient: it does not make the opposite true.
GRADED_FLOOR_CHECKS = {
    "SC-02": "corroboration: a definite label has independent support",
    "DS-01": "completeness: the label is one the evidence state permits",
    "DS-02": "corroboration: no correlated upstream counted as independent",
}
GRADED_CONSISTENCY_CHECKS = {
    "DS-03": "consistency: a quiet model reading is not contradicted by the rule baseline",
    "RP-01": "consistency: rule re-run or baseline agreement (diagnostic)",
}

# ---------------------------------------------------------------- constants (existing, not new)
# Reliability floors come from the runtime's existing telemetry levels (interpret.EVIDENCE_LEVEL):
# SUFFICIENT telemetry is 0.9 reliable, DEGRADED 0.6.
RHO_SUPPORTED = EVIDENCE_LEVEL["SUFFICIENT"]
RHO_PROVISIONAL = EVIDENCE_LEVEL["DEGRADED"]
# Relevance of a component to the proposition: direct evidence of it, supporting
# evidence (bears on it through another reading) or context.
RELEVANCE = {"direct": 1.0, "supporting": 0.5, "contextual": 0.25}


def state_strength(state):
    """How far an attention state goes, from the existing severity ranks:
    WARNING 1/3, EXCEPTION 2/3, CRITICAL 1."""
    return RANK[SEV.get(state, "NONE")] / RANK["HIGH"]


# ---------------------------------------------------------------- label classes
def quiet_label(cop):
    spec = BY_KEY[cop]
    return spec.interpretations[0] if spec.interpretations else None


INDETERMINATE = {"anomaly_detection": ("INDETERMINATE",), "policy_enforcement": ("UNCERTAIN",),
                 "resource_management": ("INDETERMINATE",), "interaction_discovery": ("PATH_PARTIAL",)}


def label_class(cop, label):
    if label in (None, INCONCLUSIVE_LABEL) or label in INDETERMINATE.get(cop, ()):
        return None
    return "quiet" if label == quiet_label(cop) else "attention"


# ---------------------------------------------------------------- components
def C(name, kind, relevance, required=None, source=None, **params):
    """One evidence component. required: 'all', 'quiet', 'attention' or None."""
    return {"name": name, "kind": kind, "relevance": relevance, "weight": RELEVANCE[relevance],
            "required": required, "source": source or kind, **params}


# Metric evidence is directional only where the metrics are necessary for the
# proposition. A quiet label ("nothing to see") needs every one of its COP's metrics
# observed quiet, so an attention state anywhere opposes it. An attention label
# usually has metrics that are sufficient but not necessary for it: insights.stalled
# in an attention state shows STALLED, but a quiet insights.stalled does not show the
# task is progressing. Those metrics can support an attention label but never oppose
# it. Only a component marked defines_attention (the COP's attention labels are
# defined by these metrics) can oppose an attention label.


def _tools_reading(cop, art):
    from .profiles import build_profiles
    mine = set(build_profiles()[cop].tools) | set(_det_tools(cop))
    return tuple(t for t in ARTIFACT_TOOLS.get(art, ()) if t in mine) or ARTIFACT_TOOLS.get(art, ())


def _det_tools(cop):
    from .rules import DETERMINISTIC_TOOLS
    return DETERMINISTIC_TOOLS.get(cop, ())


def _classification(cop):
    from .profiles import build_profiles
    return build_profiles()[cop].classification


def _generic(cop):
    spec = BY_KEY[cop]
    comps = []
    if spec.metric_keys:
        comps.append(C("signal_metrics", "metrics", "direct", "quiet", "telemetry",
                       metrics=tuple(spec.metric_keys)))
    readers = set()
    for art, kind, backs, applies in REQUIREMENTS.get(cop, ()):
        req = None
        if kind == REQUIRED:
            req = "all" if (cop, art) in GATES_ALL_LABELS else "quiet"
        tools = _tools_reading(cop, art)
        readers |= set(tools)
        comps.append(C(f"record:{art}", "record", "direct" if kind == REQUIRED else "supporting", req,
                       "authoritative_record", artifact=art, tools=tools, backs=backs))
    for t in _det_tools(cop):
        if t not in readers:
            comps.append(C(f"tool:{t}", "record", "direct", None, "measurement_tool", tools=(t,)))
    for u in CONSUMES[cop]:
        if u != "runtime_monitoring":
            comps.append(C(f"upstream:{u}", "upstream", "supporting", None, f"upstream:{u}", upstream=u))
    return comps


def _tail(cop):
    out = []
    if _classification(cop) == "llm":
        out.append(C("rule_baseline", "baseline", "supporting", None, "rule_baseline"))
    out.append(C("validated_memory", "memory", "contextual", None, "validated_memory"))
    return out


def _anomaly():
    m = tuple(BY_KEY["anomaly_detection"].metric_keys)
    # explainable: a deviation that checkable context explains is the premise of either reading, not evidence
    # for it (cop_package.ad_legitimacy). warning_is_partial: for the quiet label a WARNING is within the
    # deviation limit, so it counts as partial support and is recorded (U-AD3). Both are Anomaly Detection's.
    return [C("deviation_metrics", "metrics", "direct", "all", "telemetry", metrics=m, defines_attention=True,
              explainable=True, warning_is_partial=True),
            C("legitimacy_context", "legitimacy", "direct", None, "legitimacy_context"),
            C("versioned_baseline", "baseline_history", "supporting", None, "baseline_history",
              tools=("compare_versioned_baseline",)),
            C("upstream:behavior_analytics", "upstream", "supporting", None, "upstream:behavior_analytics",
              upstream="behavior_analytics")]


def _policy():
    m = tuple(BY_KEY["policy_enforcement"].metric_keys)
    return [C("policy_evaluation", "policy_evaluation", "direct", "all", "authoritative_record",
              artifact="policy_store", tools=("evaluate_session_operations",)),
            C("entitlements", "entitlements", "direct", "quiet", "authoritative_record",
              artifact="entitlements", tools=("check_entitlements",)),
            C("policy_signal_metrics", "metrics", "supporting", None, "telemetry", metrics=m),
            C("upstream:interaction_discovery", "upstream", "contextual", None,
              "upstream:interaction_discovery", upstream="interaction_discovery"),
            C("upstream:agent_lifecycle_management", "upstream", "contextual", None,
              "upstream:agent_lifecycle_management", upstream="agent_lifecycle_management"),
            C("upstream:anomaly_detection", "upstream", "contextual", None,
              "upstream:anomaly_detection", upstream="anomaly_detection")]


def _workflow():
    m = tuple(BY_KEY["workflow_orchestration"].metric_keys)
    return [C("task_milestones", "milestones", "direct", "quiet", "authoritative_record",
              artifact="task_plan", tools=("get_task_milestones",)),
            C("execution_transitions", "transitions", "direct", None, "execution_trace",
              tools=("analyse_execution_transitions",)),
            C("progress_metrics", "metrics", "direct", None, "telemetry", metrics=m)]


def _risk():
    m = tuple(BY_KEY["risk_mitigation"].metric_keys)
    comps = [C("register_score", "risk_recomputation", "direct", "all", "authoritative_record",
               artifact="risk_register", tools=("read_risk_register", "calculate_risk_components")),
             C("exposure_metrics", "metrics", "supporting", None, "telemetry", metrics=m)]
    from . import evidence_assessment as EA
    pkg_up = EA.load_package("risk_mitigation")["upstream"]
    for u in CONSUMES["risk_mitigation"]:
        # purvi, 2026-11-27 (P-RK3): a context-only input (Classification & Tagging) is not evidence for or against
        # a Risk Mitigation reading; every other component, and every other COP, is unchanged
        extra = {"context_only": True} if (pkg_up.get(u) or {}).get("context_only") else {}
        comps.append(C(f"upstream:{u}", "upstream", "supporting", None, f"upstream:{u}", upstream=u, **extra))
    return comps


def _incident():
    """Incident Response: the generic profile, with its context producers (METRICS.yaml upstream: no incident,
    finding or abnormal labels) not applicable evidence. purvi, 2026-11-27 (U-IR3, A-IR1): a context producer is
    neutral in VS-01 for this COP only; the grounding producers (Policy Enforcement, Recovery & Resilience, Anomaly
    Detection), the metrics, the tools, the baseline and memory are unchanged."""
    from . import evidence_assessment as EA
    pkg_up = EA.load_package("incident_response")["upstream"]
    ground = ("incident", "finding", "abnormal", "evidence_quality")
    out = []
    for c in _generic("incident_response"):
        u = c.get("upstream")
        if u and not any((pkg_up.get(u) or {}).get(k) for k in ground):
            c = {**c, "context_only": True}
        out.append(c)
    return out


def _runtime_monitoring():
    m = tuple(BY_KEY["runtime_monitoring"].metric_keys)
    comps = [C("telemetry_measures", "metrics", "direct", "all", "telemetry", metrics=m,
               defines_attention=True, unknown_is_observation=True)]
    return comps + [c for c in _generic("runtime_monitoring") if c["kind"] != "metrics"]


def _recovery():
    m = tuple(BY_KEY["recovery_resilience"].metric_keys)
    # failure metrics are the precondition of a recovery judgement, not evidence
    # either way: RECOVERED presupposes a failure.
    comps = [C("failure_metrics", "metrics", "contextual", None, "telemetry", metrics=m, precondition=True)]
    return comps + [c for c in _generic("recovery_resilience") if c["kind"] != "metrics"]


CUSTOM = {"runtime_monitoring": _runtime_monitoring, "recovery_resilience": _recovery,
          "anomaly_detection": _anomaly, "policy_enforcement": _policy,
          "workflow_orchestration": _workflow, "risk_mitigation": _risk, "incident_response": _incident}


def profile(cop):
    """The COP's evidence components, in order."""
    return (CUSTOM[cop]() if cop in CUSTOM else _generic(cop)) + _tail(cop)


# ---------------------------------------------------------------- evaluation helpers
def _item(direction, strength=0.0, reliability=1.0, basis="", refs=(), applicable=True, retrievable=()):
    return {"direction": direction, "strength": round(float(strength), 4), "reliability": round(float(reliability), 4),
            "basis": basis, "refs": list(refs)[:8], "applicable": applicable, "retrievable_with": list(retrievable)}


NA = lambda why: _item("neutral", applicable=False, basis=why)                       # noqa: E731
UNKNOWN = lambda why, retr=(): _item("unknown", basis=why, retrievable=retr)          # noqa: E731


def _result(c):
    r = c.get("result")
    if isinstance(r, str):
        try:
            r = json.loads(r)
        except ValueError:
            return {}
    return r or {}


def _calls(ctx, tools):
    return [c for c in ctx["tool_calls"] if c.get("tool") in tools]


def _ok(calls):
    return [c for c in calls if c.get("status") == "OK" and "missing_dependency" not in _result(c)]


def _cited(ctx, calls):
    ids = {c.get("tool_call_id") for c in calls}
    return [cl for cl in ctx["claims"] if cl.get("kind") == "tool_observation" and cl.get("tool_call_id") in ids]


def _requirement_applies(ctx, art):
    for r in assess_requirements(ctx["cop"], ctx["case"]) if ctx.get("case") is not None else ():
        if r["artifact"] == art:
            return r["kind"] != NOT_APPLICABLE
    return True


def _strength(ctx, state):
    """Strength of an attention state for the candidate. Against a quiet label it is
    absolute (state_strength). For an attention label it is relative to the
    severity the finding claims, so a WARNING fully supports a LOW-severity finding
    such as DEGRADED, and gives a third of the support for a HIGH one."""
    claimed = RANK.get(ctx.get("severity") or "NONE", 0)
    if not claimed:
        return state_strength(state)
    return min(1.0, RANK[SEV.get(state, "NONE")] / claimed)


def _metrics(ctx, comp, cls, label):
    rows = [r for r in ctx["rows"] if r["metric"] in comp["metrics"]]
    if not rows:
        return NA("no metric rows for this component")
    if comp.get("precondition"):
        hot = [r for r in rows if r["evidence"] == "OBSERVED" and r["state"] in ATTENTION_STATES]
        return _item("neutral", basis=("the event to be judged occurred: " + ", ".join(
            f"{r['metric']}={r['state']}" for r in hot)) if hot else "no failure metric in an attention "
            "state", refs=[f"metric:{r['metric']}" for r in hot])
    rel = ctx["telemetry_reliability"]
    premise = _premise(ctx, cls) if comp.get("explainable") else set()
    if premise:
        rows = [r for r in rows if r["metric"] not in premise]
        if not rows:
            return _item("neutral", basis="every metric deviates and checkable context explains each: the "
                                          "premise, not evidence either way")
    obs = [r for r in rows if r["evidence"] == "OBSERVED"]
    na = [r for r in rows if r["evidence"] == "NOT_APPLICABLE"]
    if comp.get("unknown_is_observation"):
        # Runtime Monitoring: a telemetry measure that could not be computed is itself
        # an observation of degraded telemetry (the rule reads it so), at WARNING level.
        obs = obs + [{**r, "state": "WARNING", "value": f"{r['evidence']} (could not be computed)"}
                     for r in rows if r["evidence"] in ("UNKNOWN", "MISSING")]
    hot = [r for r in obs if r["state"] in ATTENTION_STATES]
    quiet = [r for r in obs if r["state"] not in ATTENTION_STATES]
    unknown = [r["metric"] for r in rows if r["evidence"] not in ("OBSERVED", "NOT_APPLICABLE")
               and not comp.get("unknown_is_observation")]
    warned = []
    if cls == "quiet" and comp.get("warning_is_partial"):
        warned = [r for r in hot if r["state"] == "WARNING"]
        hot = [r for r in hot if r["state"] != "WARNING"]
    quiet_share = (len(quiet) + (1 - state_strength("WARNING")) * len(warned) + 0.5 * len(na)) / len(rows)
    tail = f"; unknown: {', '.join(unknown)}" if unknown else ""
    if premise:
        tail += "; explained by checkable context (premise, not evidence): " + ", ".join(sorted(premise))
    if warned:
        tail += "; WARNING, within the deviation limit (partial support, recorded): " + ", ".join(
            f"{r['metric']}={r['value']}" for r in warned)
    if cls == "attention":
        if hot:
            top = max(hot, key=lambda r: _strength(ctx, r["state"]))
            return _item("supports", _strength(ctx, top["state"]), rel,
                         f"attention state observed (claimed severity {ctx.get('severity')}): "
                         + ", ".join(f"{r['metric']}={r['state']} ({r['value']})" for r in hot) + tail,
                         [f"metric:{r['metric']}" for r in hot])
        if quiet_share > 0 and not comp.get("defines_attention"):
            return _item("neutral", basis=f"{len(quiet)} of {len(rows)} metrics quiet; these metrics are "
                                          f"sufficient, not necessary, for {label}, so quiet ones do not "
                                          f"oppose it" + tail)
        if quiet_share > 0:
            return _item("opposes", quiet_share, rel,
                         f"{len(quiet)} of {len(rows)} metrics observed within threshold, "
                         f"{len(na)} not applicable, none in an attention state" + tail,
                         [f"metric:{r['metric']}" for r in quiet])
        return UNKNOWN("no metric observed" + tail)
    # quiet: every dimension has to be observed quiet; NOT_APPLICABLE counts half
    # ("the condition did not arise" is weaker than "measured within threshold").
    if hot:
        top = max(hot, key=lambda r: state_strength(r["state"]))
        return _item("opposes", state_strength(top["state"]), rel,
                     "attention state observed: " + ", ".join(f"{r['metric']}={r['state']} ({r['value']})"
                                                            for r in hot) + tail,
                     [f"metric:{r['metric']}" for r in hot])
    if quiet_share > 0:
        return _item("supports", quiet_share, rel,
                     f"{len(quiet)} of {len(rows)} metrics observed within threshold, "
                     f"{len(na)} not applicable (half weight)" + tail,
                     [f"metric:{r['metric']}" for r in quiet])
    return UNKNOWN("no metric observed" + tail)


def _record(ctx, comp, cls, label):
    art = comp.get("artifact")
    if art and not _requirement_applies(ctx, art):
        return NA(f"{art} not applicable to this case")
    calls = _calls(ctx, comp["tools"])
    if not calls:
        return UNKNOWN(f"{art or comp['tools'][0]} not read in this case", comp["tools"])
    ok = _ok(calls)
    if not ok:
        return UNKNOWN(f"{art or comp['tools'][0]} unavailable (missing dependency)")
    cited = _cited(ctx, ok)
    if cited and ctx["sc01_pass"]:
        return _item("supports", 1.0, 1.0, f"cited and checked by SC-01: {cited[0].get('statement', '')[:140]}",
                     [f"tool:{c.get('tool_call_id')}" for c in cited])
    if cls == "quiet":
        return _item("supports", 0.5, 1.0, f"{art or comp['tools'][0]} read; nothing in it was cited against "
                     f"the quiet reading (absence of contrary record, half strength)",
                     [f"tool:{c.get('tool_call_id')}" for c in ok])
    return _item("neutral", basis=f"{art or comp['tools'][0]} read but not cited for {label}",
                 refs=[f"tool:{c.get('tool_call_id')}" for c in ok])


def _upstream(ctx, comp, cls, label):
    u = comp["upstream"]
    f = (ctx["upstream"] or {}).get(u)
    if comp.get("context_only"):
        return NA(f"{u} is a context-only input for {ctx['cop']}: listed, not evidence "
                  f"({'U-IR3' if ctx['cop'] == 'incident_response' else 'P-RK3'})")
    if f is None:
        return UNKNOWN(f"{u} finding absent")
    if not (f.get("verified") and f.get("status") == SUBSTANTIVE):
        return UNKNOWN(f"{u} finding present but not verified ({f.get('label')}): supports nothing")
    own = set(BY_KEY[ctx["cop"]].metric_keys)
    lin = set(f.get("lineage") or [])
    if lin & own:
        return _item("neutral", basis=f"{u} {f['label']} shares {sorted(lin & own)} with this COP's metrics: "
                                      f"the same signal, not corroboration")
    vs = (f.get("verification_support") or {}).get("decision")
    rel = {SUPPORTED: 1.0, PROVISIONAL: RHO_PROVISIONAL}.get(vs, RHO_SUPPORTED)
    ucls = label_class(u, f["label"])
    cited = [c for c in ctx["claims"] if c.get("kind") == "upstream_finding" and c.get("upstream_cop") == u
             and c.get("upstream_label") == f["label"]]
    if cited and ctx["sc01_pass"]:
        # the candidate states this finding as its basis and SC-01 checked it: direct evidence,
        # like a cited record (the COP's rule derives the label from it)
        return {**_item("supports", 1.0, rel, f"cited as the basis and checked by SC-01: verified {u} reads "
                                              f"{f['label']} ({vs or 'no support record'})",
                        [f"finding:{f.get('finding_id')}"]), "cited_as_basis": True}
    if ucls is None:
        return _item("neutral", basis=f"{u} reads {f['label']}: no definite reading")
    if ucls == cls:
        return _item("supports", 1.0, rel, f"verified {u} reads {f['label']} ({vs or 'no support record'})",
                     [f"finding:{f.get('finding_id')}"])
    # another COP's quiet reading does not disprove this COP's attention reading (a
    # different question); its attention reading does weigh against a quiet one.
    if cls == "quiet":
        return _item("opposes", 1.0, rel, f"verified {u} reads attention label {f['label']}",
                     [f"finding:{f.get('finding_id')}"])
    return _item("neutral", basis=f"verified {u} reads {f['label']}: a quiet upstream reading does not "
                                  f"disprove {label}")


def _baseline(ctx, comp, cls, label):
    b = ctx.get("baseline")
    if not b:
        return NA("no separate rule baseline (deterministic COP: the rule is the finding)")
    if _premise(ctx, cls):
        return _item("neutral", basis=f"rule baseline reads {b.get('label')} from deviations that checkable "
                                      f"context explains: the premise, not evidence")
    bl = b.get("label")
    bcls = label_class(ctx["cop"], bl) if b.get("status", SUBSTANTIVE) == SUBSTANTIVE else None
    if bcls is None:
        return UNKNOWN(f"rule baseline has no definite reading ({bl})")
    if bl == label:
        return _item("supports", 1.0, 1.0, f"rule baseline reads {bl} too")
    if bcls == cls:
        return _item("supports", 0.5, 1.0, f"rule baseline reads {bl}: same class, another label")
    return _item("opposes", 1.0, 1.0, f"rule baseline reads {bl}, the opposite class")


def _memory(ctx, comp, cls, label):
    mv = ctx.get("memory_view") or {}
    est = mv.get("established") or []
    if not est:
        return UNKNOWN("no validated precedent")
    # U-CM9 (purvi 2026-11-28, packages/context_memory/DECISIONS.md A-CM1): a COP's own historical output never
    # independently corroborates its present finding. A record without owner_cop is treated as the COP's own.
    own = [r for r in est if r.get("owner_cop", ctx.get("cop")) == ctx.get("cop")]
    est = [r for r in est if r not in own]
    if not est:
        return _item("neutral", basis=f"{len(own)} validated record(s), all the COP's own earlier output: not "
                                      f"independent corroboration", refs=[f"memory:{r['record_id']}" for r in own])
    same = [r for r in est if (r.get("payload") or {}).get("label") == label]
    if same:
        return _item("supports", 1.0, 1.0, f"{len(same)} validated record(s) with the same reading",
                     [f"memory:{r['record_id']}" for r in same])
    return _item("neutral", basis=f"{len(est)} validated record(s), none with {label}: precedent does not "
                                  f"decide this case")


# ---- COP-specific evaluators

def _premise(ctx, cls):
    """Deviations that checkable context explains, for Anomaly Detection: for the quiet label only when every
    deviation is explained and the finding cites the context; for the attention label whenever every deviation
    is explained (contrary evidence needs no citation). One explained deviation never covers another."""
    lg = ctx.get("legitimacy")
    if not lg or not lg["all_explained"] or (cls == "quiet" and not lg["cited"]):
        return set()
    return set(lg["explained"])


def _legitimacy(ctx, comp, cls, label):
    lg = ctx.get("legitimacy")
    if not lg or not lg["deviating"]:
        return NA("no deviation to explain")
    ref = [f"context:{m}" for m in lg["explained"]]
    if cls == "quiet":
        if lg["all_explained"] and lg["cited"]:
            return _item("supports", 1.0, 1.0, "every deviation explained by re-checked context: " + "; ".join(
                f"{x['metric']} by {', '.join(x['by'])}" for x in lg["context"]), ref)
        if lg["all_explained"]:
            return _item("neutral", basis="context explains every deviation but the finding does not cite it")
        return _item("opposes", 1.0, 1.0, "not explained by checkable context: " + "; ".join(
            f"{x['metric']} ({x.get('gap')})" for x in lg["context"] if not x["explained"]), ref)
    if lg["all_explained"]:
        return _item("opposes", 1.0, 1.0, "every deviation explained by re-checked context: " + "; ".join(
            f"{x['metric']} by {', '.join(x['by'])}" for x in lg["context"]), ref)
    return NA("not every deviation has checkable context: nothing explains the anomaly away")


def _baseline_history(ctx, comp, cls, label):
    ok = _ok(_calls(ctx, comp["tools"]))
    premise = _premise(ctx, cls)
    cmp_ = [(_result(c), c) for c in ok if (_result(c).get("sessions_in_baseline") or 0) > 0
            and _result(c).get("current_value") is not None and _result(c).get("metric_id") not in premise]
    if premise and not cmp_ and ok:
        return _item("neutral", basis="the versioned baseline restates deviations that context explains")
    if not cmp_:
        return UNKNOWN("no versioned baseline compared", comp["tools"])
    outside = [(r, c) for r, c in cmp_ if not (r["min"] <= float(r["current_value"]) <= r["max"])]
    refs = [f"tool:{c.get('tool_call_id')}" for _, c in cmp_]
    desc = lambda xs: ", ".join(f"{r['metric_id']}={r['current_value']} vs [{r['min']}, {r['max']}] over "  # noqa: E731
                                f"{r['sessions_in_baseline']} sessions" for r, _ in xs)
    if cls == "attention":
        if outside:
            return _item("supports", 1.0, 1.0, "outside every value in the versioned baseline: " + desc(outside), refs)
        return _item("opposes", 1.0, 1.0, "within the versioned baseline range: " + desc(cmp_), refs)
    if outside:
        return _item("opposes", 1.0, 1.0, "outside every value in the versioned baseline: " + desc(outside), refs)
    return _item("supports", 1.0, 1.0, "within the versioned baseline range: " + desc(cmp_), refs)


POLICY_SUPPORTS = {   # which decisions in the policy evaluation support which label
    "VIOLATION": ("deny_executed", "deny_proposed"),
    "APPROVAL_REQUIRED": ("approval_missing",),
}


def _policy_evaluation(ctx, comp, cls, label):
    calls = _calls(ctx, comp["tools"])
    ok = _ok(calls)
    if not ok:
        return UNKNOWN("no approved policy version: no rule was evaluated" if calls else "policy not evaluated",
                       comp["tools"] if not calls else ())
    ev = _result(ok[-1])
    obs, prop = ev.get("observed") or [], ev.get("proposed") or []
    facts = {"deny_executed": [o for o in obs if o["decision"] == "DENY" and not o.get("blocked_by_hook")],
             "deny_proposed": [o for o in prop if o["decision"] == "DENY"],
             "approval_missing": [o for o in obs + prop if o["decision"] == "REQUIRE_APPROVAL"]
             if not ev.get("approval_recorded") else [],
             "no_rule": [o for o in obs + prop if o["decision"] == "NO_RULE"]}
    allowed = [o for o in obs if o["decision"] == "ALLOW"]
    ref = [f"tool:{ok[-1].get('tool_call_id')}"]
    pv = ev.get("policy_version")
    against_quiet = {k: v for k, v in facts.items() if v}
    if cls == "quiet":
        if against_quiet:
            return _item("opposes", 1.0, 1.0, f"policy {pv}: " + ", ".join(f"{len(v)} {k}" for k, v in
                                                                         against_quiet.items()), ref)
        return _item("supports", 1.0, 1.0, f"policy {pv}: all {len(allowed)} observed operations ALLOW, "
                                           f"none denied, none needing approval, none unmatched", ref)
    hits = [x for k in POLICY_SUPPORTS.get(label, ()) for x in facts[k]]
    if hits:
        rules = sorted({r for o in hits for r in o.get("rules", [])})
        return _item("supports", 1.0, 1.0, f"policy {pv}: {len(hits)} operation(s) match {label}: "
                                           f"rules {', '.join(rules)}", ref)
    if not against_quiet:
        return _item("opposes", 1.0, 1.0, f"policy {pv}: every operation ALLOW; nothing matches {label}", ref)
    return _item("neutral", basis=f"policy {pv}: findings exist ({', '.join(against_quiet)}) but none "
                                  f"matches {label}", refs=ref)


def _entitlements(ctx, comp, cls, label):
    if not _requirement_applies(ctx, "entitlements"):
        return NA("no MCP server used: entitlements do not apply")
    calls = _calls(ctx, comp["tools"])
    ok = _ok(calls)
    if not ok:
        return UNKNOWN("entitlement record unavailable" if calls else "entitlements not checked",
                       comp["tools"] if not calls else ())
    r = _result(ok[-1])
    bad = r.get("used_not_entitled") or []
    ref = [f"tool:{ok[-1].get('tool_call_id')}"]
    if bad:
        return _item("supports" if cls == "attention" else "opposes", 1.0, 1.0,
                     f"used without entitlement: {', '.join(bad)}", ref)
    return _item("supports" if cls == "quiet" else "neutral", 1.0 if cls == "quiet" else 0.0, 1.0,
                 "every MCP server used is entitled", ref)


def _milestones(ctx, comp, cls, label):
    calls = _calls(ctx, comp["tools"])
    ok = _ok(calls)
    if not ok:
        return UNKNOWN("no task plan: progress toward the declared objective cannot be measured"
                       if calls else "task plan not read", comp["tools"] if not calls else ())
    r = _result(ok[-1])
    ref = [f"tool:{ok[-1].get('tool_call_id')}"]
    reached, total = r.get("reached", 0), r.get("total", 0)
    if not total:
        return UNKNOWN("task plan declares no milestones")
    if reached == total:
        return _item("supports" if cls == "quiet" else "opposes", 1.0, 1.0,
                     f"{reached} of {total} milestones reached", ref)
    # unreached milestones: unfinished and stalled look alike from counts alone, so
    # they weigh fully against NORMAL but only half for an attention label.
    return _item("opposes" if cls == "quiet" else "supports", 1.0 if cls == "quiet" else 0.5, 1.0,
                 f"{reached} of {total} milestones reached", ref)


def _transitions(ctx, comp, cls, label):
    ok = _ok(_calls(ctx, comp["tools"]))
    if not ok:
        return UNKNOWN("execution transitions not analysed", comp["tools"])
    r = _result(ok[-1])
    ref = [f"tool:{ok[-1].get('tool_call_id')}"]
    stalls = r.get("stalls") or []
    if stalls:
        if cls == "quiet":
            return _item("opposes", 1.0, 1.0, f"{len(stalls)} stalled lane(s) in the execution trace", ref)
        return _item("supports" if label == "STALLED" else "neutral", 1.0 if label == "STALLED" else 0.0, 1.0,
                     f"{len(stalls)} stalled lane(s) in the execution trace", ref)
    if cls == "quiet":
        return _item("supports", 0.5, 1.0, "no stalled lane in the execution trace (half strength: absence "
                                           "of a stall is not proof of progress)", ref)
    return _item("neutral", basis=f"no stalled lane; stall detection is sufficient, not necessary, for "
                                  f"{label}, so its absence does not oppose it", refs=ref)


def _risk_recomputation(ctx, comp, cls, label):
    reg_c = _ok(_calls(ctx, ("read_risk_register",)))
    comp_c = _ok(_calls(ctx, ("calculate_risk_components",)))
    if not reg_c:
        return UNKNOWN("no risk register: impact and thresholds are undefined")
    reg = _result(reg_c[-1])
    th = reg.get("thresholds") or {}
    if "EMERGING" not in th or "MATERIAL" not in th:
        return UNKNOWN("risk register defines no EMERGING/MATERIAL thresholds")
    if not comp_c:
        return UNKNOWN("risk components not calculated", ("calculate_risk_components",))
    cp = _result(comp_c[-1])
    signals = len(cp.get("independent_verified_signals") or [])
    irrev = 1 if cp.get("irreversible_operations") else 0
    if "supporting_sensitivity_tier" in cp:
        # P-RK1 (purvi, 2026-11-27): impact from the supporting evidence's sensitivity, recomputed here from the
        # tool's inputs; an unrecorded tier is a range, never a safe value
        from .cop_package import rk_band_range
        sup = {"supporting_tier": cp["supporting_sensitivity_tier"],
               "unestablished": cp.get("supporting_sensitivity_unestablished") or []}
        got, _, decided, b = rk_band_range(reg, sup, signals + irrev)
        if not decided:
            return UNKNOWN(f"register {reg.get('version')}: supporting sensitivity not recorded; impact {b['low']} to "
                           f"{b['high']}, the band is not determined")
        impact = b["low"]
    else:
        impact = max([r.get("impact", 0) for r in reg.get("risks", [])
                      if r.get("min_sensitivity_tier", 0) <= cp.get("max_sensitivity_tier", 0)] or [0])
        got = None
    score = impact * (signals + irrev)
    got = got or ("MATERIAL" if score >= th["MATERIAL"] else "EMERGING" if score >= th["EMERGING"] else "LOW")
    basis = (f"register {reg.get('version')}: impact {impact} x (independent signals {signals} + irreversible "
             f"{irrev}) = {score}; thresholds EMERGING {th['EMERGING']}, MATERIAL {th['MATERIAL']} -> {got}")
    refs = [f"tool:{reg_c[-1].get('tool_call_id')}", f"tool:{comp_c[-1].get('tool_call_id')}"]
    if got == label:
        return _item("supports", 1.0, 1.0, basis, refs)
    if label_class("risk_mitigation", got) == cls:
        return _item("supports", 0.5, 1.0, basis + f" (same class as {label})", refs)
    return _item("opposes", 1.0, 1.0, basis, refs)


EVALUATORS = {"metrics": _metrics, "record": _record, "upstream": _upstream, "baseline": _baseline,
              "legitimacy": _legitimacy,
              "memory": _memory, "baseline_history": _baseline_history,
              "policy_evaluation": _policy_evaluation, "entitlements": _entitlements,
              "milestones": _milestones, "transitions": _transitions,
              "risk_recomputation": _risk_recomputation}


# ---------------------------------------------------------------- thresholds
def thresholds(cop, cls, comps):
    """Derived from the COP's own components that apply to this case.

    - supported_min = RHO_SUPPORTED x (required weight + the smallest corroborating
      weight) / total weight. This is the support the required evidence plus one
      corroborating component give at SUFFICIENT reliability and full strength.
    - provisional_min = RHO_PROVISIONAL x required weight / total weight. This is the
      required evidence alone at DEGRADED reliability. When the label class has no
      required component, the primary (heaviest direct) component is used instead.
    - reject_min = RHO_PROVISIONAL x primary weight / total weight. The primary
      evidence points the other way at DEGRADED reliability or better.
    - max_against_supported = 1 - RHO_SUPPORTED. The contrary evidence must stay
      within the unreliability that SUFFICIENT telemetry allows.
    """
    app = [c for c in comps if c["item"]["applicable"]]
    W = sum(c["weight"] for c in app) or 1.0
    req = [c for c in app if c["required"] in ("all", cls)]
    direct = [c for c in app if c["relevance"] == "direct"]
    primary = max(direct or app, key=lambda c: c["weight"])["weight"] if app else 0.0
    R = sum(c["weight"] for c in req) or primary
    corr = [c["weight"] for c in app if c not in req and c["kind"] != "baseline"]
    w_corr = min(corr) if corr else 0.0
    return {"total_weight": round(W, 4), "required_weight": round(R, 4), "primary_weight": primary,
            "smallest_corroborating_weight": w_corr,
            "supported_min": round(RHO_SUPPORTED * (R + w_corr) / W, 4),
            "provisional_min": round(RHO_PROVISIONAL * R / W, 4),
            "reject_min": round(RHO_PROVISIONAL * primary / W, 4),
            "max_against_supported": round(1 - RHO_SUPPORTED, 4),
            "min_corroboration_supported": 2,
            "derivation": {"RHO_SUPPORTED": RHO_SUPPORTED, "RHO_PROVISIONAL": RHO_PROVISIONAL,
                           "source": "interpret.EVIDENCE_LEVEL (SUFFICIENT, DEGRADED)"}}


# ---------------------------------------------------------------- the assessment
def assess(cop, finding, results, rows, tool_calls, upstream, memory_view, baseline, case,
           telemetry_reliability):
    """Graded verification support for the candidate finding. Reads evidence only."""
    cand = finding.get("rejected_candidate") or finding
    label = cand.get("label")
    cls = label_class(cop, label)
    by = {r["check_id"]: r for r in results}
    hard_fail = [r for r in results if r["check_id"] in HARD_CHECKS and r["result"] == "FAIL"]
    floor_fail = [r for r in results if r["check_id"] in GRADED_FLOOR_CHECKS and r["result"] == "FAIL"]
    cons_fail = [r for r in results if r["check_id"] in GRADED_CONSISTENCY_CHECKS and r["result"] in ("FAIL", "WARN")]
    ctx = {"cop": cop, "rows": rows or [], "tool_calls": tool_calls or [], "upstream": upstream or {},
           "memory_view": memory_view or {}, "baseline": baseline, "case": case,
           "claims": cand.get("claims") or [], "telemetry_reliability": telemetry_reliability,
           "severity": cand.get("severity"),
           "sc01_pass": (by.get("SC-01") or {}).get("result") == "PASS"}
    from .cop_package import LEGITIMACY
    if cop in LEGITIMACY and cls is not None:
        ctx["legitimacy"] = LEGITIMACY[cop](case, memory_view, ctx["claims"], ctx["tool_calls"])
    comps = []
    for comp in profile(cop):
        item = EVALUATORS[comp["kind"]](ctx, comp, cls or "attention", label) if cls else \
            _item("unknown", basis="no proposition to assess")
        comp = dict(comp)
        if item.get("cited_as_basis") and comp["relevance"] != "direct":
            comp.update(relevance="direct", weight=RELEVANCE["direct"],
                        relevance_note="raised to direct: the candidate cites this finding as its basis")
        comps.append({**{k: v for k, v in comp.items() if k not in ("tools", "metrics")},
                      "tools": list(comp.get("tools", ())), "metrics": list(comp.get("metrics", ())),
                      "item": item})
    th = thresholds(cop, cls or "attention", comps)
    W = th["total_weight"]
    app = [c for c in comps if c["item"]["applicable"]]
    for c in app:
        it = c["item"]
        c["contribution"] = round(c["weight"] * it["reliability"] * it["strength"] / W, 4) \
            if it["direction"] in ("supports", "opposes") else 0.0
    V = round(sum(c["contribution"] for c in app if c["item"]["direction"] == "supports"), 4)
    D = round(sum(c["contribution"] for c in app if c["item"]["direction"] == "opposes"), 4)
    U = round(max(0.0, 1.0 - V - D), 4)
    known = [c for c in app if c["item"]["direction"] != "unknown"]
    sup = [c for c in app if c["item"]["direction"] == "supports"]
    corr_sources = sorted({c["source"] for c in sup if c["kind"] != "baseline"})
    dims = {
        "relevance": round(sum(c["weight"] for c in known if c["item"]["direction"] != "neutral")
                           / (sum(c["weight"] for c in known) or 1.0), 4) if known else None,
        "reliability": round(sum(c["weight"] * c["item"]["reliability"] for c in known)
                             / (sum(c["weight"] for c in known) or 1.0), 4) if known else None,
        "completeness": round(sum(c["weight"] for c in known) / W, 4),
        "consistency": round(V / (V + D), 4) if V + D > 0 else None,
        "corroboration": len(corr_sources),
    }
    req = [c for c in app if c["required"] in ("all", cls)]
    req_unknown = [c["name"] for c in req if c["item"]["direction"] == "unknown"]
    direct_support = [c["name"] for c in sup if c["relevance"] == "direct"]
    limitations = []
    if hard_fail:
        decision, basis = REJECTED, "hard check failed: " + ", ".join(
            f"{r['check_id']} {r['reason_code']}" for r in hard_fail)
    elif cls is None:
        decision, basis = INSUFFICIENT, (f"label {label} states uncertainty: no proposition to support"
                                         if label not in (None, INCONCLUSIVE_LABEL) else
                                         "no definite candidate label")
    elif D >= th["reject_min"] and D > V:
        decision, basis = REJECTED, f"evidence supports the opposite: against {D} >= reject_min {th['reject_min']} " \
                                    f"and above support {V}"
    elif req_unknown:
        decision, basis = INSUFFICIENT, "required evidence unknown: " + ", ".join(req_unknown)
    elif floor_fail:
        decision, basis = INSUFFICIENT, "minimum evidential requirement unmet: " + ", ".join(
            f"{r['check_id']} {r['reason_code']}" for r in floor_fail)
    elif not direct_support:
        decision, basis = INSUFFICIENT, "no direct evidence supports " + str(label)
    elif V >= th["supported_min"] and len(corr_sources) >= 2 and D <= th["max_against_supported"]:
        decision, basis = SUPPORTED, f"support {V} >= supported_min {th['supported_min']}, corroborated by " \
                                     f"{len(corr_sources)} sources, against {D} <= {th['max_against_supported']}"
    elif V >= th["provisional_min"] and D < V:
        decision, basis = PROVISIONAL, f"support {V} >= provisional_min {th['provisional_min']} and above " \
                                       f"against {D}"
        if V < th["supported_min"]:
            limitations.append(f"support {V} below supported_min {th['supported_min']}")
        if len(corr_sources) < 2:
            limitations.append(f"only {len(corr_sources)} independent source(s): {', '.join(corr_sources)}")
        if D > th["max_against_supported"]:
            limitations.append(f"contrary evidence {D} above {th['max_against_supported']}")
    else:
        decision, basis = INSUFFICIENT, f"support {V} below provisional_min {th['provisional_min']} (or not above " \
                                        f"against {D}); contrary evidence below reject_min {th['reject_min']}: " \
                                        f"undecided, not disproved"
    unknown_items = [{"component": c["name"], "basis": c["item"]["basis"],
                      "retrievable_with": c["item"]["retrievable_with"]}
                     for c in app if c["item"]["direction"] == "unknown"]
    return {
        "version": SUPPORT_VERSION, "cop": cop, "label": label, "label_class": cls,
        "assessed": "rejected_candidate" if finding.get("rejected_candidate") else "finding",
        "decision": decision, "basis": basis, "limitations": limitations,
        "score": V, "against": D, "unknown": U,
        "dimensions": dims, "corroborating_sources": corr_sources,
        "thresholds": th,
        "hard_checks": [{"check": r["check_id"], "result": r["result"], "reason": r["reason_code"],
                         "kind": HARD_CHECKS[r["check_id"]]} for r in results if r["check_id"] in HARD_CHECKS],
        "graded_checks": [{"check": r["check_id"], "result": r["result"], "reason": r["reason_code"],
                           "kind": {**GRADED_FLOOR_CHECKS, **GRADED_CONSISTENCY_CHECKS}[r["check_id"]]}
                          for r in results if r["check_id"] in GRADED_FLOOR_CHECKS or
                          r["check_id"] in GRADED_CONSISTENCY_CHECKS],
        "consistency_flags": [f"{r['check_id']} {r['reason_code']}" for r in cons_fail],
        "required_unknown": req_unknown, "unknown_items": unknown_items,
        "components": comps,
        "telemetry_reliability": telemetry_reliability,
        "assumptions": _assumptions(cop, upstream or {}, telemetry_reliability),
        "note": "computed from evidence only; the model's self-reported confidence is not an input",
    }


def _assumptions(cop, upstream, telemetry_reliability):
    """Inputs the score used without evidence for them. Recorded, never scored: a
    referral (runtime/referrals.py) may replace an assumption with a verified reading."""
    out = []
    rm = upstream.get("runtime_monitoring")
    if cop != "runtime_monitoring" and "runtime_monitoring" in CONSUMES[cop] and not (
            rm and rm.get("verified") and rm.get("status") == SUBSTANTIVE):
        out.append({"item": "telemetry_reliability", "value": telemetry_reliability,
                    "owner": "runtime_monitoring",
                    "basis": "no verified Runtime Monitoring finding: the default evidence ceiling "
                             f"{telemetry_reliability} is assumed, not observed"})
    return out


def verifier_result(sup, binary_blocked):
    """VS-01: the graded verdict as a verifier result (result, reason_code, detail)."""
    if binary_blocked:
        return "NOT_RUN", "CANDIDATE_ALREADY_REJECTED", f"{sup['decision']} for the rejected candidate: {sup['basis']}"
    if sup["label_class"] is None:
        return "NOT_RUN", "NO_PROPOSITION", sup["basis"]
    return {SUPPORTED: ("PASS", "SUPPORTED"), PROVISIONAL: ("WARN", "PROVISIONALLY_SUPPORTED"),
            REJECTED: ("FAIL", "CONTRARY_EVIDENCE"),
            INSUFFICIENT: ("FAIL", "INSUFFICIENT_SUPPORT")}[sup["decision"]] + (sup["basis"][:400],)


def static_profiles():
    """Every COP's components and default thresholds (all components applicable), for documentation."""
    out = {}
    for cop in BY_KEY:
        comps = [{**c, "item": {"applicable": not (c["kind"] == "baseline" and _classification(cop) != "llm")}}
                 for c in profile(cop)]
        out[cop] = {"components": [{k: c[k] for k in ("name", "kind", "relevance", "weight", "required", "source")}
                                   for c in comps],
                    "thresholds": {cls: {k: v for k, v in thresholds(cop, cls, comps).items() if k != "derivation"}
                                   for cls in ("quiet", "attention")},
                    "quiet_label": quiet_label(cop)}
    return out
