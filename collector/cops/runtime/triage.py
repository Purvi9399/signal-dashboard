"""
Graded verification and triage: what the verified finding means, how well the
COP can handle it, who handles it, and which action follows.

Runs as the graph node `grade_and_route` (graph.py), after independent
verification (and its bounded recovery) and before authority_check. It reads
the verifier results, the evidence sufficiency, the tool calls, the upstream
findings and memory. It never edits the finding's label, status or claims and
never relaxes a verifier: a candidate the verifiers rejected stays rejected.

Four fields are stored separately on the finding, under `triage`:

    outcome            PASS | FAIL | INCONCLUSIVE  (finding validity, governance reading)
    evidence_support   score 1-5 with supporting and limiting evidence
    confidence         0-1, with its basis (raw source, cap, calibration status)
    routing            tier, reason, thresholds, next step, human_needed, ...

plus the handling quotient, the action selection and any referral.

Outcome semantics (the action rule keys on this, not on verifier pass/fail):
    PASS          the verified reading says nothing needs attention
                  (the quiet label, or a non-remedial label such as PRECEDENT_FOUND)
    FAIL          a verified attention reading: the behaviour fails the COP's question
    INCONCLUSIVE  no verified definite reading: the COP held back, a required
                  dependency is missing, the candidate was rejected, or the label
                  itself states uncertainty (INDETERMINATE, UNCERTAIN, PATH_PARTIAL)
A rejected candidate is evidence against the candidate, never evidence that a
violation occurred: its outcome is INCONCLUSIVE and nothing is done to the agent.

Everything here is deterministic. A model's own assessment (its label and
self-reported confidence) is recorded under `llm_assessment` and enters only
as the raw confidence, which the support score caps.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field, asdict

from ..authority import (TAXONOMY, typed, legacy, permitted, needs_approval, HUMAN_APPROVED_ONLY,
                         OBSERVE, RECORD, ALERT, NOTIFY, REQUEST, RECOMMEND, INITIATE, INTERVENE, TIER)
from ..specs import BY_KEY, CONSUMES
from .contracts import (SUBSTANTIVE, INCONCLUSIVE, MISSING_DEPENDENCY, REJECTED, FAILED, FAIL, WARN,
                        PASS as R_PASS, stable_id)

TRIAGE_VERSION = "triage-1"
THRESHOLDS_VERSION = "thresholds-1"
PASS, FAIL_OUT, INCONC = "PASS", "FAIL", "INCONCLUSIVE"
AUTONOMOUS, ASSISTED, SPECIALIST, HUMAN = "autonomous", "assisted", "specialist", "human"
TIERS = (AUTONOMOUS, ASSISTED, SPECIALIST, HUMAN)
PLANNING_TARGETS = {AUTONOMOUS: 0.80, ASSISTED: 0.10, SPECIALIST: 0.07, HUMAN: 0.03}
TIER_ORDER = [OBSERVE, RECORD, ALERT, NOTIFY, REQUEST, RECOMMEND, INITIATE, INTERVENE]

# ---------------------------------------------------------------- label semantics per COP
# Labels that are verified readings but state uncertainty: outcome INCONCLUSIVE.
INDETERMINATE_LABELS = {"anomaly_detection": ("INDETERMINATE",), "policy_enforcement": ("UNCERTAIN",),
                        "resource_management": ("INDETERMINATE",), "interaction_discovery": ("PATH_PARTIAL",)}
# Non-quiet labels that report nothing needing remedy: outcome PASS.
NON_REMEDIAL_LABELS = {"context_memory": ("PRECEDENT_FOUND",)}

# ---------------------------------------------------------------- evidence-support score
CHECK_FACTUAL = ("SC-01", "MC-01", "DS-03")          # the data contradicts the candidate
CHECK_INTEGRITY = ("EV-01", "EV-02", "ID-01", "ID-02")  # the evidence itself cannot be trusted
CHECK_STATE = ("SC-02", "DS-01", "DS-02")            # no support, or support the state does not allow
CONFIDENCE_CAP = {1: 0.15, 2: 0.40, 3: 0.65, 4: 0.85, 5: 0.95}
SCORE_MEANING = {
    1: "evidence contradicts the candidate, or gives virtually no support",
    2: "weak support: significant gaps or contradictions",
    3: "partial or mixed support: a provisional reading at most",
    4: "strong support with bounded, named limitations",
    5: "very strong: corroborated by independent evidence kinds, reproduced, nothing material missing",
}
OK_STATES = ("AVAILABLE", "RETRIEVED_AND_VALIDATED", "NOT_APPLICABLE")


def _vr(results, check):
    return [r for r in results if r["check_id"] == check]


def _finding_fails(results):
    return [r for r in results if r["result"] == FAIL and r["effect"] == "BLOCKS_FINDING"]


def _support_kinds(f, suff, quiet):
    """Distinct independent evidence kinds supporting the label. Claims count only
    when the finding verified (their checks passed). Tool observations and the
    authoritative artifacts those tools read are one kind."""
    kinds = {}
    for c in f.get("claims") or []:
        key = {"metric_state": "metric", "metric_value": "metric", "tool_observation": "record",
               "upstream_finding": "upstream", "memory_precedent": "memory"}.get(c.get("kind"))
        if key:
            kinds.setdefault(key, []).append(c.get("statement", "")[:160])
    for it in (suff or {}).get("items", []):
        if it.get("kind") == "REQUIRED" and it["state"] == "RETRIEVED_AND_VALIDATED":
            kinds.setdefault("record", []).append(f"{it['item']} retrieved and validated")
        if quiet and it.get("kind") == "METRIC" and it["state"] == "RETRIEVED_AND_VALIDATED":
            # a quiet reading rests on the observed metrics being within their thresholds
            kinds.setdefault("metric", []).append(f"{it['item']} observed")
    # an upstream claim is support only when that upstream finding verified (SC-02 enforces it)
    if "upstream" in kinds and not any(u.get("verified") or u.get("counted_as") not in (None, "context_only")
                                       for u in f.get("upstream_used") or []):
        kinds.pop("upstream")
    return kinds


def contradictions(f, results, upstream):
    """Factual contradictions (the data disagrees with an asserted fact) and
    interpretive ones (two verified readings of the same evidence disagree)."""
    factual, interpretive = [], []
    for r in results:
        if r["check_id"] in CHECK_FACTUAL and r["result"] == FAIL:
            factual.append({"check": r["check_id"], "code": r["reason_code"],
                            "detail": (r.get("detail") or "")[:240],
                            "failures": [{k: x.get(k) for k in ("statement", "asserted", "observed", "code")}
                                         for x in (r.get("failures") or [])][:4]})
        if r["check_id"] == "RP-01" and r["result"] == WARN:
            interpretive.append({"check": "RP-01", "code": r["reason_code"],
                                 "detail": (r.get("detail") or "")[:240]})
    return {"factual": factual, "interpretive": interpretive,
            "status": "factual" if factual else ("interpretive" if interpretive else "none")}


def evidence_support(cop, f, results, suff, upstream, evidence_ceiling):
    """1-5 support for the stated reading, with the evidence for and against."""
    spec = BY_KEY[cop]
    quiet = spec.interpretations[0] if spec.interpretations else None
    status, label = f.get("status"), f.get("label")
    con = contradictions(f, results, upstream)
    supporting, limiting = [], []
    items = (suff or {}).get("items", [])
    missing_core = [i for i in items if i.get("kind") in ("REQUIRED", "METRIC") and i["state"] not in OK_STATES]
    missing_up = [i for i in items if i.get("kind") == "UPSTREAM" and i["state"] not in OK_STATES]
    for i in missing_core:
        limiting.append({"kind": "missing_evidence", "item": i["item"], "state": i["state"], "material": True})
    for i in missing_up:
        limiting.append({"kind": "upstream_unavailable", "item": i["item"], "state": i["state"],
                         "material": False})
    for r in results:
        if r["result"] == WARN:
            limiting.append({"kind": "verifier_warning", "item": f"{r['check_id']}:{r['reason_code']}",
                             "detail": (r.get("detail") or "")[:200], "material": True})
    named = [m for m in f.get("missing_evidence") or [] if m.get("reason") == "named by the investigation"]
    if named:
        limiting.append({"kind": "gaps_named_by_investigation", "item": [m["source"] for m in named][:6],
                         "material": False})
    if evidence_ceiling is not None and evidence_ceiling < 0.9:
        limiting.append({"kind": "telemetry_ceiling", "item": f"evidence ceiling {evidence_ceiling}",
                         "material": True})
    fails = _finding_fails(results)
    integrity = [r for r in fails if r["check_id"] in CHECK_INTEGRITY]
    factual = [r for r in fails if r["check_id"] in CHECK_FACTUAL]
    state = [r for r in fails if r["check_id"] in CHECK_STATE]
    for r in fails:
        limiting.append({"kind": "verifier_failure", "item": f"{r['check_id']}:{r['reason_code']}",
                         "detail": (r.get("detail") or "")[:240], "material": True})
    rule = None
    if status == REJECTED or fails:
        cand = f.get("rejected_candidate") or {}
        n_claims = len(cand.get("claims") or [])
        n_bad = sum(len(r.get("failures") or []) or 1 for r in factual)
        if integrity:
            score, rule = 1, "integrity: cited evidence failed EV/ID checks"
        elif factual and (n_bad >= n_claims or any(r["check_id"] in ("MC-01", "DS-03") for r in factual)):
            score, rule = 1, "factual contradiction: the data disproves the candidate's claims"
        elif factual:
            score, rule = 2, f"factual contradiction in {n_bad} of {n_claims} claims; the rest checked"
        elif state:
            score, rule = 2 if any(r["check_id"] == "DS-02" for r in state) else 1, \
                "the evidence state does not support the label (" + \
                ", ".join(f"{r['check_id']} {r['reason_code']}" for r in state) + ")"
        else:
            score, rule = 1, "a blocking check failed"
        reading = (f.get("rejected_candidate") or {}).get("label")
        return _support(score, rule, reading, supporting, limiting, con, candidate_verification="CONTRADICTED"
                        if factual else ("UNTRUSTED_EVIDENCE" if integrity else "UNSUPPORTED"))
    if status in (INCONCLUSIVE, MISSING_DEPENDENCY, FAILED) or label == INCONCLUSIVE:
        blocked = [i for i in missing_core if i["state"] == "MISSING_AND_UNAVAILABLE"]
        score = 1 if blocked or status in (MISSING_DEPENDENCY, FAILED) else 2
        rule = ("required evidence unavailable: no definite reading is supported" if score == 1 else
                "evidence partial: the COP could not reach a definite reading")
        return _support(score, rule, None, supporting, limiting, con, candidate_verification="NO_CANDIDATE")
    # verified substantive reading
    kinds = _support_kinds(f, suff, label == quiet)
    for k, v in kinds.items():
        supporting.append({"kind": k, "evidence": v[:4], "count": len(v)})
    mc = _vr(results, "MC-01")
    if mc and mc[0]["result"] == R_PASS:
        supporting.append({"kind": "reproduced", "evidence": ["MC-01 recomputed the cited metric values"]})
    sc = _vr(results, "SC-01")
    if sc and sc[0]["result"] == R_PASS and (f.get("claims") or []):
        supporting.append({"kind": "claims_checked", "evidence": [f"SC-01: {len(f['claims'])} claims match "
                                                                  f"the data they cite"]})
    corroborated = len([k for k in kinds if k in ("metric", "record", "upstream", "memory")]) >= 2
    material = [x for x in limiting if x["material"]]
    minor = [x for x in limiting if not x["material"]]
    s = 3 + (1 if corroborated else 0) + (1 if not material else 0)
    if s == 5 and minor:
        s = 4
    if con["interpretive"]:
        s -= 1
    if not kinds:
        s = min(s, 2)
    score = max(2, min(5, s))
    rule = (f"base 3; corroborated by {len(kinds)} kinds {'+1' if corroborated else '+0'}; "
            f"{'no material limitation +1' if not material else f'{len(material)} material limitations +0'}"
            f"{'; minor limitations hold it below 5' if s == 4 and minor and not con['interpretive'] else ''}"
            f"{'; interpretive contradiction -1' if con['interpretive'] else ''}")
    return _support(score, rule, label, supporting, limiting, con, candidate_verification="VERIFIED",
                    provisional=score <= 3)


def _support(score, rule, reading, supporting, limiting, con, candidate_verification, provisional=False):
    return {"score": score, "meaning": SCORE_MEANING[score], "rule": rule, "reading": reading,
            "candidate_verification": candidate_verification, "provisional": provisional,
            "supporting": supporting, "limiting": limiting, "contradictions": con}


def confidence_basis(f, support, memory_view, llm_assessment):
    raw = float(f.get("confidence") or 0.0)
    cap = CONFIDENCE_CAP[support["score"]]
    est = memory_view.get("established") if isinstance(memory_view, dict) else None
    definite = f.get("verified") and f.get("status") == SUBSTANTIVE and f.get("label") != INCONCLUSIVE
    val = min(raw, cap) if definite else 0.0
    return {"value": round(val, 3), "raw": raw,
            "raw_source": "model self-report, then verifier evidence ceiling" if llm_assessment
            else "rule constant, then verifier evidence ceiling",
            "cap_from_score": cap, "capped": raw > cap,
            "calibration": ("validated outcome history exists for this COP; not yet used to fit a curve"
                            if est else "uncalibrated: no validated outcome history"),
            "definite_reading": bool(definite),
            "note": "confidence never replaces verification: an unverified or inconclusive finding has value 0"}


def outcome_of(cop, f):
    spec = BY_KEY[cop]
    quiet = spec.interpretations[0] if spec.interpretations else None
    lab, st = f.get("label"), f.get("status")
    if st != SUBSTANTIVE or not f.get("verified") or lab == INCONCLUSIVE:
        return INCONC, {REJECTED: "candidate rejected by verification",
                        MISSING_DEPENDENCY: "a required dependency is missing",
                        FAILED: "the COP errored"}.get(st, "no verified definite reading")
    if lab in INDETERMINATE_LABELS.get(cop, ()):
        return INCONC, f"the verified label {lab} states uncertainty"
    if lab == quiet or lab in NON_REMEDIAL_LABELS.get(cop, ()):
        return PASS, f"verified {lab}: nothing needs remedy"
    return FAIL_OUT, f"verified attention reading {lab}"


# ---------------------------------------------------------------- handling quotient
WEIGHTS = {"evidence_strength": 0.25, "completeness": 0.15, "interpretability": 0.10,
           "contradiction_free": 0.15, "tool_availability": 0.10, "authority": 0.08,
           "reversibility": 0.05, "upstream_reliability": 0.05, "validated_history": 0.03,
           "expertise": 0.02, "no_external_approval": 0.02}
assert abs(sum(WEIGHTS.values()) - 1.0) < 1e-9


def handling_quotient(cop, f, outcome, support, suff, tool_calls, upstream, evidence_ceiling,
                      memory_view, action, approval_actions, specialist_needed):
    """perceived_handling_quotient: the COP's computed ability to handle this
    decision itself. Every component is read from checks already run."""
    items = (suff or {}).get("items", [])
    core = [i for i in items if i.get("kind") in ("REQUIRED", "METRIC") and i["state"] != "NOT_APPLICABLE"]
    ok = [i for i in core if i["state"] in OK_STATES]
    comp = {}
    comp["evidence_strength"] = ((support["score"] - 1) / 4, f"score {support['score']} -> (score-1)/4")
    comp["completeness"] = ((len(ok) / len(core)) if core else 1.0,
                            f"{len(ok)} of {len(core)} required/metric items available")
    lab = f.get("label")
    if outcome == INCONC:
        interp = 0.5 if lab in INDETERMINATE_LABELS.get(cop, ()) else 0.2
    else:
        interp = 1.0
    comp["interpretability"] = (interp, "definite verified reading" if interp == 1 else
                                ("indeterminate label" if interp == 0.5 else "no definite reading"))
    con = support["contradictions"]["status"]
    comp["contradiction_free"] = ({"none": 1.0, "interpretive": 0.5, "factual": 0.0}[con],
                                  f"contradiction status: {con}")
    latest = {}
    for c in tool_calls:
        latest[c["tool"]] = c.get("status")
    good = [t for t, s in latest.items() if s in ("OK", "executed")]
    comp["tool_availability"] = ((len(good) / len(latest)) if latest else 1.0,
                                 f"{len(good)} of {len(latest)} tools returned OK")
    spec = BY_KEY[cop]
    a = typed(action or "NONE")
    okp, basis = permitted(spec, a)
    appr = needs_approval(a, approval_actions)
    comp["authority"] = (0.0 if not okp else (0.5 if appr else 1.0),
                         f"{a}: {basis}" + ("; needs approval" if appr and okp else ""))
    rev = (TAXONOMY.get(a).reversibility if a in TAXONOMY else "reversible")
    comp["reversibility"] = ({"reversible": 1.0, "partly_reversible": 0.5, "irreversible": 0.0}[rev], f"{a}: {rev}")
    cons = CONSUMES[cop]
    ver = [u for u in cons if (upstream or {}).get(u, {}).get("verified")]
    ceil = 1.0 if evidence_ceiling is None else float(evidence_ceiling)
    upr = ceil * (0.5 + 0.5 * (len(ver) / len(cons))) if cons else ceil
    comp["upstream_reliability"] = (upr, f"evidence ceiling {ceil}; {len(ver)} of {len(cons)} upstream verified")
    est = (memory_view or {}).get("established") or []
    if est:
        same = [r for r in est if (r.get("payload") or {}).get("label") == lab]
        h = 1.0 if same else 0.25
        why = f"{len(same)} of {len(est)} validated records share this reading"
    else:
        h, why = 0.5, "no validated history: neutral"
    comp["validated_history"] = (h, why)
    comp["expertise"] = (0.3 if specialist_needed else 1.0,
                         "a specialist trigger fired" if specialist_needed else "within the COP's domain")
    comp["no_external_approval"] = (0.0 if appr else 1.0, "approval required" if appr else "no approval needed")
    total = sum(WEIGHTS[k] * v for k, (v, _) in comp.items())
    return {"value": round(total, 3), "version": TRIAGE_VERSION,
            "components": {k: {"value": round(v, 3), "weight": WEIGHTS[k],
                               "contribution": round(WEIGHTS[k] * v, 4), "basis": why}
                           for k, (v, why) in comp.items()},
            "note": "separate from the event's inherent risk; no component is self-reported by the COP"}


# ---------------------------------------------------------------- per-COP thresholds and routing
@dataclass(frozen=True)
class Thresholds:
    autonomous: float        # q >= autonomous: the COP handles a FAIL itself, if authorised
    assisted: float          # q >= assisted: bounded support from tools, evidence or another COP
    specialist: float        # q >= specialist: specialist investigation; below: human
    justification: str
    human_labels: tuple = ()     # verified labels that need an accountable human regardless of q
    version: str = THRESHOLDS_VERSION

    def band(self, q):
        if q >= self.autonomous:
            return AUTONOMOUS
        if q >= self.assisted:
            return ASSISTED
        if q >= self.specialist:
            return SPECIALIST
        return HUMAN

    def to_dict(self):
        return asdict(self)


T = Thresholds
THRESHOLDS = {
    "runtime_monitoring": T(0.80, 0.60, 0.35, "its reading caps every other COP's evidence ceiling; "
                            "default bands"),
    "workflow_orchestration": T(0.78, 0.58, 0.35, "guidance actions are reversible and approval-gated "
                                "when supervised; slightly lower autonomous bar"),
    "context_memory": T(0.75, 0.55, 0.30, "observational: WATCH is its strongest action"),
    "interaction_discovery": T(0.80, 0.60, 0.35, "route findings feed policy and risk; default bands"),
    "agent_lifecycle_management": T(0.82, 0.62, 0.38, "identity and configuration drift feed policy "
                                    "enforcement; a wrong reading propagates"),
    "behavior_analytics": T(0.75, 0.55, 0.30, "pattern characterisation only; WATCH is its strongest action"),
    "anomaly_detection": T(0.80, 0.60, 0.35, "ALERT on anomalies; default bands"),
    "resource_management": T(0.80, 0.60, 0.35, "ALERT on entitlement breaches; default bands"),
    "workload_optimization": T(0.78, 0.58, 0.35, "efficiency advice is reversible"),
    "policy_enforcement": T(0.85, 0.65, 0.40, "owns permission; its actions intervene. Highest bar",
                            ("VIOLATION",)),
    "classification_tagging": T(0.72, 0.52, 0.30, "tags only; its one permitted action is NONE"),
    "recovery_resilience": T(0.80, 0.60, 0.35, "restoration checks; irreversible outcomes go to a human",
                             ("IRREVERSIBLE",)),
    "incident_response": T(0.85, 0.65, 0.40, "decides containment; a confirmed incident needs an "
                           "accountable human", ("INCIDENT",)),
    "risk_mitigation": T(0.85, 0.65, 0.40, "residual-risk calls drive escalation", ("MATERIAL",)),
    "audit_traceability": T(0.82, 0.62, 0.38, "attribution errors are hard to undo in an audit record"),
    "governance_intelligence": T(0.75, 0.55, 0.30, "aggregate patterns; reviews, not interventions"),
    "knowledge_policy_management": T(0.82, 0.62, 0.38, "policy precedence affects every enforcement"),
    "governance_architecture": T(0.82, 0.62, 0.38, "structural changes to COP responsibilities need a human",
                                 ("STRUCTURAL_CHANGE_RECOMMENDED",)),
}
assert set(THRESHOLDS) == set(BY_KEY)

# ---------------------------------------------------------------- action selection for FAIL
# label -> (default typed action, highest authority tier the label justifies)
LABEL_ACTIONS = {
    "workflow_orchestration": {"DRIFTING": ("REQUEST_CORRECTION", RECOMMEND),
                               "STALLED": ("CHANGE_EXECUTION_PLAN", RECOMMEND),
                               "EXCESSIVE_DELEGATION": ("REQUEST_CORRECTION", RECOMMEND),
                               "UNEXPECTED_BRANCH": ("ALERT", RECOMMEND)},
    "runtime_monitoring": {"DEGRADED": ("WATCH", ALERT), "INSUFFICIENT": ("ALERT", NOTIFY)},
    "anomaly_detection": {"ANOMALOUS": ("ALERT", NOTIFY)},
    "context_memory": {"STALE": ("WATCH", RECORD), "CONTRADICTORY": ("WATCH", NOTIFY),
                       "CONTEXT_LOSS": ("WATCH", NOTIFY)},
    "interaction_discovery": {"PROPAGATING": ("REQUEST_COP_SUPPORT", NOTIFY),
                              "NEW_ROUTE": ("REQUEST_COP_SUPPORT", NOTIFY)},
    "incident_response": {"WATCH": ("WATCH", ALERT), "INCIDENT": ("OPEN_INCIDENT", INTERVENE)},
    "classification_tagging": {k: ("NONE", OBSERVE) for k in
                               ("BOUNDARY", "OVERSIGHT", "LOOPS", "OUTCOME", "INTEGRITY", "COST")},
    "policy_enforcement": {"VIOLATION": ("BLOCK_OPERATION", INTERVENE),
                           "APPROVAL_REQUIRED": ("REQUEST_APPROVAL", REQUEST)},
    "risk_mitigation": {"EMERGING": ("WATCH", RECOMMEND), "MATERIAL": ("ESCALATE", INITIATE)},
    "resource_management": {"INEFFICIENT": ("WATCH", ALERT), "EXCESSIVE": ("ALERT", ALERT),
                            "UNAUTHORIZED": ("ALERT", ALERT)},
    "governance_intelligence": {"RECURRING": ("SCHEDULE_REVIEW", RECORD),
                                "CONCENTRATED": ("SCHEDULE_REVIEW", RECORD),
                                "BLIND_SPOT": ("REQUEST_COP_SUPPORT", NOTIFY)},
    "behavior_analytics": {k: ("WATCH", NOTIFY) for k in ("LOOPING", "DRIFTING", "OVER_EXPLORING",
                                                         "POOR_RECOVERY")},
    "recovery_resilience": {"PARTIAL_RECOVERY": ("WATCH", ALERT), "FAILED_RECOVERY": ("ALERT", INITIATE),
                            "IRREVERSIBLE": ("ALERT", INITIATE)},
    "workload_optimization": {k: ("WATCH", RECOMMEND) for k in ("REDUNDANT", "BOTTLENECK",
                                                               "OVERPROVISIONED", "UNDERPROVISIONED")},
    "audit_traceability": {"PARTIAL": ("WATCH", ALERT), "BROKEN_TRACE": ("ALERT", ALERT),
                           "UNATTRIBUTABLE": ("ALERT", ALERT)},
    "knowledge_policy_management": {"STALE": ("SCHEDULE_REVIEW", RECORD),
                                    "CONFLICT": ("DELEGATE_TO_SPECIALIST", NOTIFY),
                                    "UPDATE_REQUIRED": ("SCHEDULE_REVIEW", RECORD)},
    "agent_lifecycle_management": {"NEW": ("WATCH", ALERT), "CHANGED": ("WATCH", ALERT),
                                   "DRIFTED": ("ALERT", ALERT), "INACTIVE": ("WATCH", RECORD),
                                   "RETIRED": ("ALERT", ALERT)},
    "governance_architecture": {"GAP": ("SCHEDULE_REVIEW", RECORD),
                                "STRUCTURAL_CHANGE_RECOMMENDED": ("SCHEDULE_REVIEW", RECORD)},
}
for _k, _spec in BY_KEY.items():
    _att = [x for x in _spec.interpretations[1:] if x not in INDETERMINATE_LABELS.get(_k, ())
            and x not in NON_REMEDIAL_LABELS.get(_k, ())]
    assert set(_att) <= set(LABEL_ACTIONS[_k]), (_k, set(_att) - set(LABEL_ACTIONS[_k]))

# ---------------------------------------------------------------- specialist map
SPECIALISTS = {
    "workflow_orchestration": dict(
        responsibility="complex task-plan reconstruction and repeated-failure analysis",
        receiver="specialist:workflow_plan_reconstruction", support_cops=("behavior_analytics",),
        tools=("get_task_milestones", "retrieve_trace_window", "compare_workflow_failures"),
        decides="whether the task is drifting, stalled or on an unexpected branch, from the full plan",
        cannot="instruct the agent or change its plan without approval"),
    "runtime_monitoring": dict(
        responsibility="collector disagreement, source reconstruction and telemetry integrity diagnostics",
        receiver="specialist:telemetry_integrity", support_cops=("audit_traceability",),
        tools=("get_collector_breakdown", "check_collector_expectations"),
        decides="which collector is right when sources disagree; whether telemetry is SUFFICIENT",
        cannot="alter collector configuration or discard events"),
    "anomaly_detection": dict(
        responsibility="unusual behaviours needing deeper baseline or contextual investigation",
        receiver="specialist:baseline_investigation", support_cops=("behavior_analytics",),
        tools=("compare_versioned_baseline", "find_similar_validated_cases", "retrieve_trace_window"),
        decides="ANOMALOUS or NORMAL against a wider baseline", cannot="block or pause the agent"),
    "context_memory": dict(
        responsibility="unresolved context provenance, compaction or semantic contradiction",
        receiver="specialist:context_provenance", support_cops=("audit_traceability",),
        tools=("verify_context_provenance", "compare_instruction_versions", "get_context_snapshots"),
        decides="which context version is authoritative", cannot="rewrite the agent's context or memory"),
    "interaction_discovery": dict(
        responsibility="complex agent-to-agent or MCP route reconstruction",
        receiver="specialist:route_reconstruction", support_cops=("audit_traceability",),
        tools=("build_interaction_graph", "compare_known_routes"),
        decides="the full interaction path and whether a route is new", cannot="block a route"),
    "incident_response": dict(
        responsibility="multi-agent incident correlation and containment planning",
        receiver="specialist:incident_correlation", support_cops=("risk_mitigation",),
        tools=("get_incident_evidence", "get_verified_findings", "check_exposure"),
        decides="whether findings form one incident and a containment plan to propose",
        cannot="pause, block or stop a session: those need a human approval"),
    "classification_tagging": dict(
        responsibility="difficult taxonomy interpretation and ambiguous event categorisation",
        receiver="specialist:taxonomy_interpretation", support_cops=("governance_intelligence",),
        tools=("query_taxonomy", "compare_validated_examples"),
        decides="the governance tag for an ambiguous event", cannot="change the taxonomy itself"),
    "policy_enforcement": dict(
        responsibility="conflicting permission rules, entitlement ambiguity and approval resolution",
        receiver="specialist:policy_resolution", support_cops=("knowledge_policy_management",),
        tools=("evaluate_session_operations", "check_entitlements"),
        decides="which rule governs and whether approval is required",
        cannot="grant an exception or block an operation without approval"),
    "risk_mitigation": dict(
        responsibility="multi-dimensional risk assessment and mitigation trade-offs",
        receiver="specialist:risk_assessment", support_cops=("incident_response",),
        tools=("calculate_risk_components", "evaluate_control_prerequisites", "read_risk_register"),
        decides="residual risk and the recommended mitigation", cannot="accept residual risk on behalf of the owner"),
    "resource_management": dict(
        responsibility="complex budget, quota and resource allocation conflicts",
        receiver="specialist:quota_resolution", support_cops=("workload_optimization",),
        tools=("compute_consumption", "get_quota"),
        decides="which entitlement applies when quotas conflict", cannot="raise a quota or budget"),
    "governance_intelligence": dict(
        responsibility="cross-session systemic trends and recurring governance failures",
        receiver="specialist:systemic_trends", support_cops=("governance_architecture",),
        tools=("query_aggregate_report", "compare_time_windows", "get_confirmed_incidents"),
        decides="whether a pattern is systemic", cannot="change COP responsibilities"),
    "behavior_analytics": dict(
        responsibility="complex behavioural trajectories and recovery-pattern anomalies",
        receiver="specialist:trajectory_analysis", support_cops=("recovery_resilience",),
        tools=("compute_sequence_statistics", "compare_behaviour_history"),
        decides="the behavioural pattern over a longer window", cannot="intervene on the agent"),
    "recovery_resilience": dict(
        responsibility="failed or uncertain restoration and rollback analysis",
        receiver="specialist:restoration_analysis", support_cops=("incident_response",),
        tools=("get_recovery_records", "validate_checkpoints"),
        decides="whether a viable state was restored and which checkpoint to use",
        cannot="perform a rollback (REQUEST_ROLLBACK needs approval)"),
    "workload_optimization": dict(
        responsibility="justified versus wasteful resource usage across task types",
        receiver="specialist:workload_comparison", support_cops=("resource_management",),
        tools=("compare_task_classes", "calculate_usage_rates", "evaluate_previous_changes"),
        decides="whether usage is justified for the task class", cannot="restrict models or tools"),
    "audit_traceability": dict(
        responsibility="broken provenance chains and attribution reconstruction",
        receiver="specialist:attribution_reconstruction", support_cops=("runtime_monitoring",),
        tools=("reconstruct_trace", "check_decision_linkage"),
        decides="who acted and whether the chain can be rebuilt", cannot="amend audit records"),
    "knowledge_policy_management": dict(
        responsibility="policy precedence and semantic conflicts",
        receiver="specialist:policy_precedence", support_cops=("policy_enforcement",),
        tools=("get_policy_versions", "compare_policy_documents", "inspect_rule_dependencies"),
        decides="which policy version takes precedence", cannot="approve or publish a policy"),
    "agent_lifecycle_management": dict(
        responsibility="configuration, identity and integration drift needing deeper validation",
        receiver="specialist:configuration_validation", support_cops=("interaction_discovery",),
        tools=("get_agent_registry", "detect_configuration_changes"),
        decides="whether a change is authorised drift or an unregistered agent", cannot="retire or block an agent"),
    "governance_architecture": dict(
        responsibility="structural coverage gaps and changes to COP responsibilities",
        receiver="specialist:architecture_review", support_cops=("governance_intelligence",),
        tools=("query_coverage_matrix", "validate_cop_relationships", "inspect_cop_catalogue"),
        decides="whether a coverage gap is genuine and which COP should own it",
        cannot="change COP responsibilities: that is a human architecture decision"),
}
assert set(SPECIALISTS) == set(BY_KEY)
for _k, _s in SPECIALISTS.items():
    _s.setdefault("transfers", ("finding and rejected candidate", "verifier results", "triage record",
                                "evidence sufficiency", "tool call ids and results", "upstream findings used"))
    _s.setdefault("returns", f"a verified finding to {_k} as an upstream input; {_k} re-runs grade_and_route. "
                             f"The specialist writes no VALIDATED memory (record_feedback stays the only route)")
    _s.setdefault("human_when", "the specialist cannot verify a reading within its budget, the decision needs "
                                "approval, the referral hits a cycle, hop limit, timeout or cost limit")
    _s.setdefault("is_human", False)

MAX_HOPS, REFERRAL_TIMEOUT_S, REFERRAL_COST_USD = 2, 900, 0.10


# ---------------------------------------------------------------- referral contracts
@dataclass
class ReferralContract:
    initiator: str
    receiver: str
    kind: str                       # cop_support | specialist
    reason: str
    case_id: str
    chain: list = field(default_factory=list)
    max_hops: int = MAX_HOPS
    timeout_s: int = REFERRAL_TIMEOUT_S
    cost_budget_usd: float = REFERRAL_COST_USD
    transfers: tuple = ()
    can_decide: str = ""
    cannot_authorise: str = ""
    returns: str = ""
    status: str = "PENDING"         # PENDING | RETURNED | ESCALATED_TO_HUMAN | REFUSED
    escalation_reason: str = ""

    @property
    def referral_id(self):
        return stable_id(self.case_id, self.initiator, self.receiver, len(self.chain), "referral")

    def to_dict(self):
        d = asdict(self)
        d["referral_id"] = self.referral_id
        return d


class ReferralRouter:
    """Bounded referrals: no cycles, at most max_hops, a timeout and a cost budget.
    A violation escalates to a human; it is never retried."""

    def open(self, initiator, receiver, kind, reason, case_id, chain=()):
        spec = SPECIALISTS.get(initiator, {})
        chain = list(chain) or [initiator]
        c = ReferralContract(initiator, receiver, kind, reason, case_id, chain + [receiver],
                             transfers=tuple(spec.get("transfers", ())), can_decide=spec.get("decides", ""),
                             cannot_authorise=spec.get("cannot", ""), returns=spec.get("returns", ""))
        if receiver in chain:
            c.status, c.escalation_reason = "ESCALATED_TO_HUMAN", f"cycle: {' -> '.join(chain + [receiver])}"
        elif len(chain) > c.max_hops:
            c.status, c.escalation_reason = "ESCALATED_TO_HUMAN", f"hop limit {c.max_hops} reached"
        return c

    def dispatch(self, contract, handlers, clock=time.monotonic):
        """Run a referral through handlers {receiver: fn(contract) -> dict}. A handler may
        return {"refer_to": cop} to pass it on. Used by tests and by any future
        specialist runtime; the graph only opens contracts (dry-run tasks)."""
        c, spent, t0 = contract, 0.0, clock()
        while c.status == "PENDING":
            h = handlers.get(c.receiver)
            if h is None:
                c.status, c.escalation_reason = "ESCALATED_TO_HUMAN", f"no handler for {c.receiver}"
                break
            out = h(c) or {}
            spent += float(out.get("cost_usd") or 0)
            if clock() - t0 > c.timeout_s or out.get("timed_out"):
                c.status, c.escalation_reason = "ESCALATED_TO_HUMAN", f"timeout after {c.timeout_s}s"
                break
            if spent > c.cost_budget_usd:
                c.status, c.escalation_reason = "ESCALATED_TO_HUMAN", f"cost {spent:.4f} over {c.cost_budget_usd}"
                break
            if out.get("refer_to"):
                nxt = self.open(c.receiver, out["refer_to"], "specialist", out.get("reason", "onward"),
                                c.case_id, c.chain)
                if nxt.status != "PENDING":
                    c.status, c.escalation_reason = nxt.status, nxt.escalation_reason
                    c.chain = nxt.chain
                    break
                c = nxt
                continue
            c.status = "RETURNED"
            c.result = out.get("result")
        return c


# ---------------------------------------------------------------- the classifier
def _repeated_failures(cop, memory_view, case_id):
    rows = (memory_view or {}).get("unverified") or []
    return [r for r in rows if (r.get("payload") or {}).get("finding_status") == REJECTED
            and r.get("from_case") != case_id]


def _cause(cop, f, suff, upstream, recovery, results):
    """Why an INCONCLUSIVE outcome happened, in the order it should be resolved."""
    items = (suff or {}).get("items", [])
    cs = f.get("cop_specific") or {}
    if f.get("status") == FAILED:
        return "cop_error", f.get("rationale")
    if cs.get("llm_error"):
        return "investigation_not_run", cs.get("llm_error")
    rm = (upstream or {}).get("runtime_monitoring") or {}
    if cop != "runtime_monitoring" and rm.get("verified") and rm.get("label") == "INSUFFICIENT":
        return "telemetry_insufficient", None
    if f.get("status") == REJECTED:
        rec = recovery or f.get("recovery") or {}
        if rec.get("outcome") in ("rejected_again", "no_candidate", "not_substantive"):
            return "recovery_exhausted", rec
        if rec.get("recoverable"):
            return "rejected_recoverable", rec
        disproved = [r for r in results if r["result"] == FAIL and (
            r["check_id"] == "MC-01" or (r["check_id"] == "SC-01" and any(
                x.get("code") == "VALUE_MISMATCH" for x in r.get("failures") or [])))]
        if disproved:
            return "candidate_disproved", disproved
    retr = [i for i in items if i.get("kind") == "REQUIRED" and i["state"] == "MISSING_BUT_RETRIEVABLE"]
    if retr:
        return "missing_retrievable", retr
    req = [i for i in items if i.get("kind") == "REQUIRED" and i["state"] == "MISSING_AND_UNAVAILABLE"]
    deps = f.get("missing_dependencies") or []
    if req or deps:
        return "missing_authoritative", req or deps
    met = [i for i in items if i.get("kind") == "METRIC" and i["state"] not in OK_STATES]
    if met:
        return "missing_telemetry", met
    up = [i for i in items if i.get("kind") == "UPSTREAM" and i["state"] not in OK_STATES]
    if up:
        return "missing_upstream", up
    if f.get("status") == REJECTED:
        return "rejected_unrecoverable", recovery or f.get("recovery")
    return "no_actionable_gap", None


POLICY_ARTIFACTS = ("policy_store", "entitlements")


def _first_permitted(spec, names):
    for n in names:
        if permitted(spec, n)[0]:
            return n
    return "NONE"


def classify(cop, f, results, suff, tool_calls, upstream, evidence_ceiling, memory_view, recovery,
             mode="dry_run", live_session=False, pending_operations=(), approval_actions=(),
             llm_assessment=None, security_events=()):
    """Steps 4-9 of the decision sequence. Returns the triage record."""
    spec = BY_KEY[cop]
    th = THRESHOLDS[cop]
    sp = SPECIALISTS[cop]
    support = evidence_support(cop, f, results, suff, upstream, evidence_ceiling)
    outcome, outcome_reason = outcome_of(cop, f)
    conf = confidence_basis(f, support, memory_view or {}, llm_assessment)
    proposed = typed((f.get("proposed_action") or {}).get("action") or "NONE")
    cand = (f.get("rejected_candidate") or {}).get("proposed_action") or {}
    original = typed(cand.get("action") or (f.get("gated_action") or proposed))
    repeated = _repeated_failures(cop, memory_view, f.get("case_id"))
    fails = _finding_fails(results)
    integrity = [r for r in fails if r["check_id"] in ("EV-02", "ID-01", "ID-02")]
    # instruction-shaped text in a tool result: the source itself is suspect
    security = [e for e in security_events or [] if e.get("kind") == "prompt_injection_in_tool_result"]
    triggers = []
    if repeated and outcome != PASS:
        triggers.append(f"repeated failure: {len(repeated)} earlier rejected candidate(s) in memory")
    sel_reason, action, tier, next_step, primary = "", "NONE", AUTONOMOUS, "", ""
    cause, cause_detail = (None, None)
    human_task = None
    referral = None
    rules_fired = []

    # ---- outcome-specific selection (step 9), before quotient so the quotient sees the action
    if outcome == PASS:
        action = "NONE"
        sel_reason = "PASS: no remedial action (routine telemetry and audit continue)"
    elif outcome == FAIL_OUT:
        default, max_tier = LABEL_ACTIONS[cop][f["label"]]
        ok_prop = permitted(spec, proposed)[0] and \
            TIER_ORDER.index(TIER.get(proposed, OBSERVE)) <= TIER_ORDER.index(max_tier) and \
            (proposed != "NONE" or default == "NONE")
        if ok_prop:
            action, sel_reason = proposed, f"the COP's proposal {proposed} is permitted and within " \
                                           f"the {max_tier} ceiling for {f['label']}"
        else:
            action = default if permitted(spec, default)[0] else _first_permitted(spec, ("ALERT", "WATCH", "NONE"))
            sel_reason = (f"the COP proposed {proposed}, which is "
                          + ("outside its authority" if not permitted(spec, proposed)[0] else
                             (f"above the {max_tier} ceiling for {f['label']}" if proposed != "NONE"
                              else "no action on a verified attention reading"))
                          + f"; the label's default {action} is selected")
        if action == "NONE":
            sel_reason += ". No remedial action: " + ("the tag is the COP's output; remediation belongs "
                                                      "to downstream COPs" if cop == "classification_tagging"
                                                      else "nothing further is justified")
    else:
        cause, cause_detail = _cause(cop, f, suff, upstream, recovery, results)
        if cause == "cop_error":
            action, sel_reason = "NONE", "the COP itself errored: no governance action can rest on it; " \
                                         "an engineer must fix the defect"
        elif cause == "investigation_not_run":
            action, sel_reason = "NONE", "the model investigation did not run (provider unavailable or " \
                                         "invalid reply); re-run when it is available. Nothing is inferred"
        elif cause == "telemetry_insufficient":
            action, sel_reason = "REQUEST_COP_SUPPORT", "telemetry verified INSUFFICIENT by Runtime Monitoring"
        elif cause == "rejected_recoverable":
            action = "RECHECK"
            sel_reason = "candidate rejected for a recoverable reason; one bounded re-assessment with " \
                         "the verifier's feedback" + ("" if not (cause_detail or {}).get("reason") else
                                                     f" ({cause_detail['reason'][:120]})")
        elif cause == "recovery_exhausted":
            action, sel_reason = "DELEGATE_TO_SPECIALIST", "the bounded re-assessment did not produce a " \
                                                           "verified reading"
            triggers.append("recovery exhausted")
        elif cause == "candidate_disproved":
            action, sel_reason = "NONE", "the recomputed data disproves the candidate's claims and the " \
                                         "rejection is not recoverable: nothing is done to the agent (evidence " \
                                         "against a claim is not evidence of a violation)"
        elif cause == "missing_retrievable":
            tools = sorted({t for i in cause_detail for t in i.get("retrieve_with", [])})
            action, sel_reason = "COLLECT_EVIDENCE", f"required item retrievable with {', '.join(tools)}"
        elif cause == "missing_authoritative":
            names = [i.get("item") or i.get("dependency") for i in cause_detail]
            is_policy = any(any(p in str(n) for p in POLICY_ARTIFACTS) for n in names)
            if is_policy and pending_operations and live_session and permitted(spec, "HOLD_TOOL_CALL")[0]:
                action = "HOLD_TOOL_CALL"
                sel_reason = f"authoritative policy missing ({', '.join(map(str, names))}) and " \
                             f"{len(pending_operations)} operation(s) pending: hold until the policy is supplied. " \
                             f"Confidence cannot stand in for the missing policy"
            else:
                action = "REQUEST_CONTEXT"
                sel_reason = f"required artifact unavailable: {', '.join(map(str, names))}" + \
                             ("; no live operation is pending, so nothing to hold" if is_policy else "")
        elif cause == "missing_telemetry":
            action, sel_reason = "REQUEST_COP_SUPPORT", "required metrics missing: Runtime Monitoring owns " \
                                                        "telemetry completeness"
        elif cause == "missing_upstream":
            action, sel_reason = "REQUEST_COP_SUPPORT", "upstream finding(s) missing or unverified: " + \
                ", ".join(i["item"] for i in cause_detail)
        elif cause == "rejected_unrecoverable":
            action, sel_reason = "DELEGATE_TO_SPECIALIST", "rejection is not recoverable with existing evidence"
        else:
            action, sel_reason = "NONE", "INCONCLUSIVE with no actionable gap; no further action is justified " \
                                         "(no automatic WATCH)"
        if repeated and action in ("RECHECK", "COLLECT_EVIDENCE", "NONE"):
            action, sel_reason = "DELEGATE_TO_SPECIALIST", f"{sel_reason}; but this COP has failed on this " \
                                                           f"agent before ({len(repeated)} earlier rejection(s))"
    specialist_needed = bool(triggers) or action == "DELEGATE_TO_SPECIALIST"
    hq = handling_quotient(cop, f, outcome, support, suff, tool_calls, upstream, evidence_ceiling,
                           memory_view, action, approval_actions, specialist_needed)
    q = hq["value"]
    band = th.band(q)
    appr = needs_approval(action, approval_actions)
    rev = TAXONOMY[typed(action)].reversibility if typed(action) in TAXONOMY else "reversible"

    # ---- routing precedence (step 8). First matching rule sets the tier.
    if integrity or security:
        tier, primary = SPECIALIST, "source-integrity or provenance failure: " + ", ".join(
            [f"{r['check_id']} {r['reason_code']}" for r in integrity] + [e.get("kind") for e in security])
        if outcome != PASS:
            action = "DELEGATE_TO_SPECIALIST"
        rules_fired.append("R1 integrity")
    elif outcome == PASS:
        tier, primary = AUTONOMOUS, "PASS: verified reading needs no remedy"
        if q < th.assisted:
            next_step = f"quotient {q} below the {cop} assisted bar {th.assisted}: sampled in routine audit"
        rules_fired.append("R2 pass")
    elif outcome == FAIL_OUT and (f["label"] in th.human_labels or appr or rev == "irreversible"):
        tier = HUMAN
        primary = ("label " + f["label"] + " needs an accountable human" if f["label"] in th.human_labels
                   else (f"{action} requires approval" if appr else f"{action} is irreversible"))
        rules_fired.append("R3 human-required action or label")
    elif outcome == INCONC and appr:
        tier, primary = HUMAN, f"{action} requires approval"
        rules_fired.append("R3 human-required action")
    elif outcome == FAIL_OUT and triggers:
        tier, primary = SPECIALIST, "; ".join(triggers)
        action = "DELEGATE_TO_SPECIALIST"
        rules_fired.append("R4 specialist trigger")
    elif outcome == FAIL_OUT:
        tier = band
        primary = f"quotient {q} in the {band} band of {cop} (autonomous >= {th.autonomous}, " \
                  f"assisted >= {th.assisted}, specialist >= {th.specialist})"
        if band in (SPECIALIST, HUMAN):
            # A low quotient alone never sends a case to a human: below the
            # specialist bar the specialist still goes first, and escalates to
            # a human on its own human_when conditions or a referral limit.
            if band == HUMAN:
                primary += "; below the specialist bar, so the specialist goes first and escalates per its rules"
            action, tier = "DELEGATE_TO_SPECIALIST", SPECIALIST
        rules_fired.append("R5 quotient band")
    else:   # INCONCLUSIVE
        tier = TAXONOMY[typed(action)].routing_tier if typed(action) in TAXONOMY else ASSISTED
        primary = f"INCONCLUSIVE ({cause}): {sel_reason}"
        if tier == AUTONOMOUS and action == "RECHECK" and q < th.specialist:
            action, tier = "DELEGATE_TO_SPECIALIST", SPECIALIST
            primary += f"; quotient {q} below the {cop} specialist bar {th.specialist}, so delegated instead"
        if action == "NONE":
            tier = {"cop_error": HUMAN, "investigation_not_run": ASSISTED}.get(cause, AUTONOMOUS)
        rules_fired.append("R6 inconclusive cause")
    if repeated and outcome == INCONC and action == "DELEGATE_TO_SPECIALIST":
        tier = SPECIALIST
        primary = f"repeated failure: {len(repeated)} earlier rejection(s); " + primary

    # ---- referral or human task (step 11 is in execute_action; the contract is opened here)
    if action == "DELEGATE_TO_SPECIALIST":
        referral = ReferralRouter().open(cop, sp["receiver"], "specialist", primary[:300],
                                         f.get("case_id", "")).to_dict()
        tier = SPECIALIST if referral["status"] == "PENDING" else HUMAN
    elif action == "REQUEST_COP_SUPPORT":
        to = "runtime_monitoring" if cause in ("telemetry_insufficient", "missing_telemetry") else None
        if cause == "missing_upstream":
            to = next((i["item"].split(":", 1)[1] for i in cause_detail or []), None)
        if outcome == FAIL_OUT:
            to = sp["support_cops"][0] if sp["support_cops"] else None
            if cop == "interaction_discovery":
                to = "policy_enforcement"
            elif cop == "governance_intelligence":
                to = "governance_architecture"
        if to == cop or to is None:
            to = sp["support_cops"][0]
        referral = ReferralRouter().open(cop, to, "cop_support", sel_reason[:300], f.get("case_id", "")).to_dict()
        if referral["status"] != "PENDING":
            tier = HUMAN
    if tier == HUMAN and human_task is None:
        human_task = {"kind": "approval" if appr else ("defect" if cause == "cop_error" else "resolution"),
                      "question": f"{cop}: {primary}"[:300],
                      "proposed_action": action, "evidence": [x["kind"] for x in support["supporting"]]}

    next_step = next_step or {
        "NONE": "none: decision recorded" if outcome == PASS else "none: " + sel_reason[:160],
        "RECHECK": "re-assess once with the verifier's feedback (needs a model call)",
        "COLLECT_EVIDENCE": "retrieve the named item, then re-run the COP",
        "REQUEST_CONTEXT": "ask the artifact owner to supply the missing item; finding stays INCONCLUSIVE",
        "REQUEST_COP_SUPPORT": f"referral to {(referral or {}).get('receiver')}",
        "DELEGATE_TO_SPECIALIST": f"specialist task for {(referral or {}).get('receiver')}",
        "HOLD_TOOL_CALL": "hold the pending operation; a human confirms the hold or supplies the policy",
    }.get(typed(action), f"{action} via authority_check")
    human_needed = tier == HUMAN
    return {
        "version": TRIAGE_VERSION, "cop": cop,
        "outcome": outcome, "outcome_reason": outcome_reason,
        "evidence_support": support,
        "confidence": conf,
        "llm_assessment": llm_assessment,
        "perceived_handling_quotient": hq,
        "thresholds": th.to_dict(),
        "routing": {"tier": tier, "band_from_quotient": band, "primary_reason": primary,
                    "rules_fired": rules_fired, "threshold_version": th.version,
                    "next_step": next_step, "human_needed": human_needed,
                    "automated_action_authorised": None,     # set by authority_check
                    "specialist_triggers": triggers,
                    "supporting_metrics": {"score": support["score"], "confidence": conf["value"],
                                           "quotient": q, "contradictions": support["contradictions"]["status"],
                                           "completeness": hq["components"]["completeness"]["value"],
                                           "action_reversibility": rev, "approval_required": appr}},
        "action_selection": {"original_proposal": original, "verified_proposal": proposed,
                             "selected": typed(action), "legacy_alias": legacy(typed(action)),
                             "reason": sel_reason, "cause": cause,
                             "suppressed_on_pass": original if outcome == PASS and original != "NONE" else None},
        "referral": referral, "human_task": human_task,
    }


def distribution(records):
    """Observed tier counts and shares, beside the planning targets. Nothing is adjusted."""
    n = len(records)
    counts = {t: sum(1 for r in records if r == t) for t in TIERS}
    return {"n": n, "counts": counts,
            "observed": {t: round(counts[t] / n, 4) if n else 0.0 for t in TIERS},
            "planning_targets": PLANNING_TARGETS,
            "human_share_below_5pct": (counts[HUMAN] / n < 0.05) if n else None,
            "note": "targets are planning goals only; routing follows evidence, authority and thresholds"}
