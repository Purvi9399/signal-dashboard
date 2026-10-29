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
