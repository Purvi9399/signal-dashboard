"""
Evidence requirements per COP: which authoritative artifacts a COP needs
before it may reach its quiet ("nothing to see") conclusion.

Each requirement is one of:

- REQUIRED: the COP's quiet label asserts something this artifact is the only
  source for. Without it the quiet label is unsupported. For example,
  Workflow Orchestration's NORMAL says the task is progressing toward its
  declared objective, and only the task plan declares the objective.
- OPTIONAL: the artifact sharpens a finding or a response, but the quiet label
  does not depend on it.
- Conditional: a requirement with an `applies` test is REQUIRED when the test
  holds for this case and NOT_APPLICABLE otherwise. Entitlements matter only
  when the session used something entitlements govern (an MCP server).

An attention label (STALLED, VIOLATION, ...) is not gated here. It must cite
observed evidence, which SC-02 and DS-01 check on their own.

Which artifact backs which label is taken from the tool that reads it (the
`required_for` text of its missing-dependency result in tools.py). These
classifications are a semantic choice, listed for agreement in
docs/cops/PR4_ACCEPTANCE_REPORT.md.
"""

from .tools import latest_policy, _snapshots

REQUIRED, OPTIONAL, NOT_APPLICABLE = "REQUIRED", "OPTIONAL", "NOT_APPLICABLE"
AVAILABLE, UNAVAILABLE = "AVAILABLE", "UNAVAILABLE"


def _quota(case):
    q = case.artifact("quotas") or {}
    return bool(q.get(case.agent_scope) or q.get("*"))


def _had_failure(case):
    """Something failed or was reverted, so there is a recovery to judge."""
    rows = case.session.rows if case.session else []
    return any(r.get("success") is False or str(r.get("exit_code") or "0") not in ("0", "0.0")
               or r.get("reverted_to_earlier") for r in rows)


def _used_mcp(case):
    rows = case.session.rows if case.session else []
    return any(r.get("mcp_server") for r in rows)


# How to tell an artifact is present, matching how its tool reads it.
AVAILABLE_IF = {
    "task_plan": lambda c: bool(c.artifact("task_plan")),
    "context_snapshots": lambda c: _snapshots(c) is not None,
    "agent_registry": lambda c: bool(c.artifact("agent_registry")),
    "policy_store": lambda c: latest_policy(c) is not None,
    "entitlements": lambda c: bool(c.artifact("entitlements")),
    "recovery_records": lambda c: c.artifact("recovery_records") is not None,   # an empty store is a store
    "taxonomy": lambda c: bool(c.artifact("taxonomy")),
    "budget_or_quota": _quota,
    "risk_register": lambda c: bool(c.artifact("risk_register")),
    "decision_store": lambda c: c.decision_store is not None,
    "task_classes": lambda c: bool(c.artifact("task_classes")),
}

# cop -> [(artifact, kind, what it backs, applies or None)]
REQUIREMENTS = {
    "workflow_orchestration": [
        ("task_plan", REQUIRED, "NORMAL: progress against the declared objective", None)],
    "context_memory": [
        ("context_snapshots", REQUIRED, "CONTEXT_OK: continuity across compaction", None)],
    "agent_lifecycle_management": [
        ("agent_registry", REQUIRED, "UNCHANGED: comparison with the approved configuration", None)],
    "policy_enforcement": [
        ("policy_store", REQUIRED, "ALLOWED: rule evaluation against an approved policy version", None),
        ("entitlements", REQUIRED, "ALLOWED: MCP servers used are entitled", _used_mcp)],
    "recovery_resilience": [
        ("recovery_records", REQUIRED, "RECOVERED: restoration outcome and checkpoint validity", _had_failure)],
    "incident_response": [
        ("policy_store", OPTIONAL, "approval requirements of a proposed response", None)],
    "classification_tagging": [
        ("taxonomy", REQUIRED, "every label: approved family definitions", None)],
    "resource_management": [
        ("budget_or_quota", REQUIRED, "NORMAL: consumption within entitlement", None)],
    "risk_mitigation": [
        ("risk_register", REQUIRED, "LOW: impact and thresholds", None)],
    "governance_intelligence": [
        ("decision_store", REQUIRED, "NO_PATTERN: decision trends across sessions", None)],
    "workload_optimization": [
        ("task_classes", REQUIRED, "EFFICIENT: like-for-like comparison", None)],
    "knowledge_policy_management": [
        ("policy_store", REQUIRED, "CURRENT: approved policy versions", None)],
}
# Requirements that gate every substantive label, not only the quiet one.
GATES_ALL_LABELS = {("classification_tagging", "taxonomy")}

# COPs with no entry answer from session telemetry and their own memory only:
# runtime_monitoring, behavior_analytics, anomaly_detection,
# interaction_discovery, audit_traceability, governance_architecture.


def assess_requirements(cop, case):
    """Each requirement with its kind for this case and whether it is present."""
    out = []
    for art, kind, backs, applies in REQUIREMENTS.get(cop, ()):
        k = kind if applies is None or applies(case) else NOT_APPLICABLE
        present = AVAILABLE_IF[art](case)
        out.append({"artifact": art, "kind": k, "backs": backs,
                    "status": AVAILABLE if present else UNAVAILABLE,
                    "gates": "all_labels" if (cop, art) in GATES_ALL_LABELS else "quiet_label"})
    return out


def required_unavailable(cop, case, label=None, quiet=None):
    """Required artifacts that are absent. With a label, only those that gate
    it: every requirement gates the quiet label, and a few gate all labels."""
    out = [r for r in assess_requirements(cop, case) if r["kind"] == REQUIRED and r["status"] == UNAVAILABLE]
    if label is not None and label != quiet:
        out = [r for r in out if r["gates"] == "all_labels"]
    return out


# ====================================================================== evidence sufficiency
#
# One profile per COP, built from what the code already depends on: the
# artifact requirements above, the metrics the spec reads, the tools that can
# retrieve each artifact, and the upstream findings the COP consumes. Nothing
# here adds a threshold or a label meaning; the only upstream that bounds a
# label today is Runtime Monitoring's telemetry verdict (verifiers DS-01).
# Which upstreams, if any, should become REQUIRED is listed for agreement in
# docs/cops/COPS_INCONCLUSIVE_ROOT_CAUSE_REPORT.md.

# Six states an item of evidence can be in. Kept apart on purpose: UNKNOWN,
# NOT_APPLICABLE, a missing dependency and a verifier rejection mean different things.
AVAILABLE_NOT_READ = "AVAILABLE"                  # present in the case, not yet read by a tool
RETRIEVED_VALIDATED = "RETRIEVED_AND_VALIDATED"   # read through a permitted tool, status OK / OBSERVED row
MISSING_RETRIEVABLE = "MISSING_BUT_RETRIEVABLE"   # not in front of the COP, but a permitted tool can fetch it
MISSING_UNAVAILABLE = "MISSING_AND_UNAVAILABLE"   # does not exist for this case
ITEM_NOT_APPLICABLE = "NOT_APPLICABLE"            # the condition it concerns did not arise
PRESENT_UNVERIFIED = "PRESENT_BUT_UNVERIFIED"     # an upstream finding that is not verified, or an INVALID row
SUFFICIENCY_STATES = (AVAILABLE_NOT_READ, RETRIEVED_VALIDATED, MISSING_RETRIEVABLE, MISSING_UNAVAILABLE,
                      ITEM_NOT_APPLICABLE, PRESENT_UNVERIFIED)

# Tools that read each artifact (from tools.py: the artifact each tool opens).
ARTIFACT_TOOLS = {
    "task_plan": ("get_task_milestones",),
    "context_snapshots": ("get_context_snapshots", "compare_instruction_versions",
                          "inspect_context_references", "verify_context_provenance"),
    "agent_registry": ("get_agent_registry",),
    "policy_store": ("get_policy_versions", "compare_policy_documents", "inspect_rule_dependencies",
                     "check_authorisation_records", "evaluate_session_operations",
                     "evaluate_operation_against_policy", "check_approval_requirements"),
    "entitlements": ("check_entitlements",),
    "recovery_records": ("get_recovery_records", "validate_checkpoints"),
    "taxonomy": ("query_taxonomy",),
    "budget_or_quota": ("get_quota",),
    "risk_register": ("read_risk_register", "evaluate_control_prerequisites"),
    "decision_store": ("query_aggregate_report", "compare_time_windows"),
    "task_classes": ("compare_task_classes",),
}

# What resolves uncertainty when an artifact is missing (how to supply it).
RESOLVES = {
    "task_plan": "the task plan with its declared milestones",
    "context_snapshots": "context snapshots across the session's compactions",
    "agent_registry": "the approved agent registry entry",
    "policy_store": "an approved policy version",
    "entitlements": "the entitlement record for the MCP servers used",
    "recovery_records": "the recovery records and checkpoints",
    "taxonomy": "the approved taxonomy version",
    "budget_or_quota": "the budget or quota for this agent",
    "risk_register": "the risk register with impact scores and EMERGING/MATERIAL thresholds",
    "decision_store": "the decision store holding earlier sessions' findings",
    "task_classes": "task-class peers for a like-for-like comparison",
}

# Upstream findings: what they back, and whether they bound a label. Only
# runtime_monitoring bounds anything today (DS-01: no quiet label under
# INSUFFICIENT telemetry). Every other consumed finding is context.
BOUNDS_QUIET = "bounds_quiet_label"
CONTEXT = "context"


def upstream_role(cop, upstream):
    return BOUNDS_QUIET if upstream == "runtime_monitoring" and cop != "runtime_monitoring" else CONTEXT


def evidence_profile(cop):
    """The static evidence definition of one COP (Part 2 of the root-cause work)."""
    from ..specs import BY_KEY, CONSUMES
    from .profiles import build_profiles
    spec = BY_KEY[cop]
    quiet = spec.interpretations[0] if spec.interpretations else None
    tools = set(build_profiles()[cop].tools)
    arts = []
    for art, kind, backs, applies in REQUIREMENTS.get(cop, ()):
        arts.append({"artifact": art, "kind": kind if applies is None else f"{kind}_WHEN_APPLICABLE",
                     "backs": backs, "gates": "all_labels" if (cop, art) in GATES_ALL_LABELS else "quiet_label",
                     "not_applicable_acceptable": applies is not None,
                     "retrieved_by": [t for t in ARTIFACT_TOOLS.get(art, ()) if t in tools] or
                                     list(ARTIFACT_TOOLS.get(art, ())),
                     "resolves_uncertainty": RESOLVES.get(art)})
    ups = [{"cop": u, "role": upstream_role(cop, u),
            "must_be_verified": True,
            "note": ("a verified INSUFFICIENT reading forbids the quiet label (DS-01)"
                     if upstream_role(cop, u) == BOUNDS_QUIET else
                     "used only when verified; an unverified finding is context, never support (SC-01)")}
           for u in CONSUMES[cop]]
    return {"cop": cop, "quiet_label": quiet,
            "required": [a for a in arts if a["kind"].startswith(REQUIRED)],
            "optional": [a for a in arts if a["kind"] == OPTIONAL],
            "metrics": [{"metric": m, "not_applicable_acceptable": True,
                         "note": "NOT_APPLICABLE supports only 'the condition did not arise'; "
                                 "UNKNOWN and MISSING support nothing"} for m in spec.metric_keys],
            "upstream": ups,
            "quiet_label_needs": [a["artifact"] for a in arts if a["kind"].startswith(REQUIRED)]
                                 + (["runtime_monitoring not INSUFFICIENT"]
                                    if "runtime_monitoring" in CONSUMES[cop] else []),
            "missing_metrics": list(spec.missing_metrics)}


def precheck(cop, case, picture, upstream, tool_calls):
    """Evidence sufficiency before any conclusion. Reads only what the case and the
    calls already made hold; changes nothing.

    Returns {items, quiet_label_permitted, blocks_all_labels, reasons}."""
    from ..specs import CONSUMES
    from .profiles import build_profiles
    tools = set(build_profiles()[cop].tools)
    ok_tools = {c["tool"] for c in tool_calls if c.get("status") == "OK"}
    items, reasons = [], []
    for r in assess_requirements(cop, case):
        art = r["artifact"]
        readers = ARTIFACT_TOOLS.get(art, ())
        if r["kind"] == NOT_APPLICABLE:
            st = ITEM_NOT_APPLICABLE
        elif r["status"] == UNAVAILABLE:
            st = MISSING_UNAVAILABLE
        elif any(t in ok_tools for t in readers):
            st = RETRIEVED_VALIDATED
        elif any(t in tools for t in readers):
            st = MISSING_RETRIEVABLE
        else:
            st = AVAILABLE_NOT_READ
        items.append({"item": f"artifact:{art}", "kind": r["kind"], "state": st, "backs": r["backs"],
                      "gates": r["gates"], "retrieve_with": [t for t in readers if t in tools],
                      "resolves_uncertainty": RESOLVES.get(art)})
        if r["kind"] == REQUIRED and st == MISSING_UNAVAILABLE:
            reasons.append({"code": "MISSING_EVIDENCE", "item": f"artifact:{art}",
                            "detail": f"required for {r['backs']}; supply {RESOLVES.get(art)}",
                            "gates": r["gates"]})
    for row in picture.get("rows", []):
        ev = row["evidence"]
        st = {"OBSERVED": RETRIEVED_VALIDATED, "NOT_APPLICABLE": ITEM_NOT_APPLICABLE,
              "INVALID": PRESENT_UNVERIFIED}.get(ev, MISSING_UNAVAILABLE)
        items.append({"item": f"metric:{row['metric']}", "kind": "METRIC", "state": st,
                      "evidence": ev, "reason": row.get("reason") or ""})
    quiet_ok = not any(x["gates"] == "quiet_label" or x["gates"] == "all_labels" for x in reasons)
    for u in CONSUMES[cop]:
        f = (upstream or {}).get(u)
        if f is None:
            st, code = MISSING_UNAVAILABLE, "MISSING_DEPENDENCY"
        elif f.get("verified"):
            st, code = RETRIEVED_VALIDATED, None
        else:
            st, code = PRESENT_UNVERIFIED, "MISSING_DEPENDENCY"
        role = upstream_role(cop, u)
        items.append({"item": f"finding:{u}", "kind": "UPSTREAM", "role": role, "state": st,
                      "label": (f or {}).get("label"), "status": (f or {}).get("status")})
        if role == BOUNDS_QUIET and f and f.get("verified") and f.get("label") == "INSUFFICIENT":
            quiet_ok = False
            reasons.append({"code": "MISSING_EVIDENCE", "item": f"finding:{u}",
                            "detail": "Runtime Monitoring reads the telemetry INSUFFICIENT, so a quiet "
                                      "label is not supported", "gates": "quiet_label"})
        elif code:
            reasons.append({"code": code, "item": f"finding:{u}", "gates": "none",
                            "detail": f"upstream {u} is {'absent' if f is None else (f.get('label') + ', unverified')}; "
                                      f"it is context only and supports nothing"})
    return {"items": items, "quiet_label_permitted": quiet_ok,
            "blocks_all_labels": any(x["gates"] == "all_labels" for x in reasons),
            "reasons": reasons}
