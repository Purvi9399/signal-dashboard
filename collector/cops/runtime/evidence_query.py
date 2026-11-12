"""
Evidence Query Service: typed, read-only, scoped retrieval of historical evidence.

Prepared for the COPs that need history beyond the case session (decision trends,
baselines, policy, quota, registry, context). It is NOT wired into the COP graph:
no COP calls it today, and no scoring, verifier or authority path depends on it.
The runtime tools in cops/runtime/tools.py keep reading CaseData in memory.

    svc = EvidenceQueryService(InMemoryAdapter(case))
    result, record = svc.query("anomaly_detection", "metric_baseline",
                               {"metric_id": "insights.retry"},
                               Scope(tenant_id="t", session_ids=("s1",)), reason="baseline for retry")

Rules:
    - only the query types in QUERY_TYPES exist; there is no free-form query and no
      model-generated SQL. Unknown parameters are rejected, not ignored.
    - each COP may run only the query types ASSESSMENT gives it.
    - every query carries a Scope (tenant, sessions, agent); rows outside it are
      refused or filtered, and the record says which.
    - every call, including a refused one, returns a RetrievalRecord.
    - SupabaseAdapter is a disabled stub. It performs no network call; it exists so the
      contract can be reviewed before any database access is approved.
"""

import time
import uuid
from dataclasses import dataclass, field, asdict
from typing import Optional

SERVICE_VERSION = "evidence-query-1"
RECORD_NS = uuid.UUID("0b8c7f5e-4b55-4c1e-9d0a-6a1f6f2f9e11")

# Status values for a RetrievalRecord
OK, EMPTY, MISSING_SOURCE, REJECTED_SCOPE, REJECTED_NOT_PERMITTED, REJECTED_PARAMS, DISABLED = (
    "OK", "EMPTY", "MISSING_SOURCE", "REJECTED_SCOPE", "REJECTED_NOT_PERMITTED", "REJECTED_PARAMS", "DISABLED")

# ------------------------------------------------------------------ query types
# params: name -> type name. scope: what the Scope must carry for this query.
QUERY_TYPES = {
    "decision_history": {
        "params": {"cop": "str|None", "window_days": "int"}, "scope": ("tenant_id",),
        "returns": "cop_decisions rows (decision_id, cop, session_id, interpretation, decided_at)",
        "in_memory_source": "CaseData.decision_store.select('cop_decisions') (persistence.SQLiteStore)",
        "max_rows": 500},
    "metric_baseline": {
        "params": {"metric_id": "str"}, "scope": ("tenant_id", "session_ids"),
        "returns": "metric_events of the same metric in other sessions of the same tool",
        "in_memory_source": "CaseData.metric_history(metric_id)", "max_rows": 500},
    "behaviour_history": {
        "params": {"metric_ids": "list[str]"}, "scope": ("tenant_id", "session_ids"),
        "returns": "per-metric history for behaviour metrics in other sessions",
        "in_memory_source": "CaseData.metric_history(metric_id)", "max_rows": 1500},
    "policy_versions": {
        "params": {}, "scope": ("tenant_id",),
        "returns": "policy store versions with approval records and rule counts",
        "in_memory_source": "CaseData.artifact('policy_store')", "max_rows": 50},
    "quota": {
        "params": {}, "scope": ("tenant_id", "agent_id"),
        "returns": "the quota entry for the scoped agent, or the '*' default",
        "in_memory_source": "CaseData.artifact('quotas')", "max_rows": 1},
    "agent_registry": {
        "params": {}, "scope": ("tenant_id", "agent_id"),
        "returns": "registry entry and approved changes for the scoped agent",
        "in_memory_source": "CaseData.artifact('agent_registry')", "max_rows": 50},
    "context_snapshots": {
        "params": {}, "scope": ("tenant_id", "session_ids"),
        "returns": "context snapshots ordered by time",
        "in_memory_source": "CaseData.artifact('context_snapshots')", "max_rows": 200},
}

# ------------------------------------------------------------------ audit
# Where each COP data path reads from today. Verified by reading the code, cited by path:symbol.
DATA_ACCESS_AUDIT = {
    "summary": ("No LangGraph runtime COP executes a database query. Runtime tools read CaseData in "
                "memory. The only Supabase reads are the legacy COPRunner path, through narrow REST GETs."),
    "paths": [
        {"path": "legacy COPRunner (cops/run.py)", "reaches_database": True, "database": "Supabase (REST)",
         "mode": "read", "code_ref": "collector/cops/reader.py:SupabaseBackend",
         "reads": ["metric_events (SupabaseBackend.metrics, sessions)",
                   "trace_steps (SupabaseBackend.lane_path, matched_policy)",
                   "observables (SupabaseBackend.evidence_rows, collector_breakdown)"],
         "used_by": "cops/run.py when SUPABASE_URL is configured; not used by the LangGraph runtime",
         "note": "not executed in this prototype; no credentials are configured or used"},
        {"path": "LangGraph runtime tools (cops/runtime/tools.py:TOOLS)", "reaches_database": False,
         "database": None, "mode": "read", "code_ref": "collector/cops/runtime/case.py:CaseData",
         "reads": ["CaseData.events / sessions / rows (signal_metrics.compute over the estate rows)",
                   "CaseData.artifacts (policy_store, quotas, agent_registry, context_snapshots, ...)"],
         "used_by": "every runtime COP"},
        {"path": "aggregate COP decision history (tools._decisions)", "reaches_database": "local only",
         "database": "SQLite (persistence.SQLiteStore), when a decision_store is passed",
         "mode": "read", "code_ref": "collector/cops/runtime/tools.py:_decisions",
         "reads": ["cop_decisions"],
         "used_by": "governance_intelligence tools (query_aggregate_report, compare_time_windows)",
         "note": "local file or in-memory SQLite; not Supabase"},
        {"path": "runtime result writer", "reaches_database": "write only, injected poster",
         "database": "Supabase (via signal_traces.post) or a test recorder", "mode": "write",
         "code_ref": "collector/cops/runtime/persistence.py:SupabaseRuntimeWriter",
         "reads": [], "used_by": "not used by any COP for retrieval"},
    ],
}

# ------------------------------------------------------------------ consumer assessment
_A = lambda cop, why, tools, src, qtypes, gap: {        # noqa: E731
    "cop": cop, "needs": why, "current_tools": tools, "current_data_source": src,
    "reaches_database": False, "proposed_query_types": qtypes, "gap": gap}

ASSESSMENT = {
    "governance_intelligence": _A(
        "governance_intelligence", "historical decision trends across sessions",
        ["query_aggregate_report", "compare_time_windows", "get_confirmed_incidents", "get_previous_reviews"],
        "CaseData.decision_store (local SQLiteStore) when passed; otherwise a typed missing dependency",
        ["decision_history"], "no tenant-scoped production decision history"),
    "behavior_analytics": _A(
        "behavior_analytics", "behaviour baselines from earlier sessions",
        ["compare_behaviour_history", "compute_sequence_statistics"],
        "CaseData.metric_history over the in-memory estate", ["behaviour_history"],
        "history limited to the sessions loaded into the case estate"),
    "context_memory": _A(
        "context_memory", "context snapshots and instruction versions",
        ["get_context_snapshots", "compare_instruction_versions", "inspect_context_references",
         "verify_context_provenance"], "CaseData.artifact('context_snapshots') (synthetic in evaluation)",
        ["context_snapshots"], "no authoritative context snapshot store"),
    "audit_traceability": _A(
        "audit_traceability", "trace reconstruction and decision linkage",
        ["reconstruct_trace", "check_decision_linkage"], "CaseData.session (in-memory trace)",
        ["decision_history"], "cross-session linkage limited to the loaded estate"),
    "knowledge_policy_management": _A(
        "knowledge_policy_management", "policy versions and authorisation records",
        ["get_policy_versions", "compare_policy_documents", "inspect_rule_dependencies",
         "check_authorisation_records"], "CaseData.artifact('policy_store') (synthetic in evaluation)",
        ["policy_versions"], "no authoritative policy store"),
    "agent_lifecycle_management": _A(
        "agent_lifecycle_management", "registry and configuration history",
        ["get_agent_registry", "detect_configuration_changes"],
        "CaseData.artifact('agent_registry') and CaseData.earlier_sessions()", ["agent_registry"],
        "no authoritative agent registry"),
    "policy_enforcement": _A(
        "policy_enforcement", "authoritative policy lookup",
        ["evaluate_session_operations", "check_entitlements"],
        "CaseData.artifact('policy_store') via tools.latest_policy", ["policy_versions"],
        "without a policy store the COP returns POLICY_UNAVAILABLE"),
    "anomaly_detection": _A(
        "anomaly_detection", "baseline retrieval",
        ["calculate_deviation", "compare_versioned_baseline", "retrieve_trace_window",
         "analyse_tool_call_sequence", "find_similar_validated_cases"],
        "CaseData.other_sessions() and metric_history over the in-memory estate", ["metric_baseline"],
        "baseline limited to the loaded estate"),
    "resource_management": _A(
        "resource_management", "quota lookup", ["get_quota", "compute_consumption"],
        "CaseData.artifact('quotas') (synthetic in evaluation)", ["quota"],
        "no authoritative quota source; get_quota returns budget_or_quota missing"),
}


# ------------------------------------------------------------------ contracts
@dataclass(frozen=True)
class Scope:
    tenant_id: str
    session_ids: tuple = ()
    agent_id: Optional[str] = None


@dataclass
class RetrievalRecord:
    retrieval_id: str
    service_version: str
    requesting_cop: str
    query_type: str
    params: dict
    reason: str
    scope: dict
    data_source: str
    status: str
    returned_refs: list = field(default_factory=list)
    rows: int = 0
    filtered_out_of_scope: int = 0
    latency_ms: float = 0.0
    detail: Optional[str] = None
    effect_on_determination: Optional[str] = None     # set by the caller; None means not used

    def to_dict(self):
        return asdict(self)


class ScopeError(ValueError):
    pass


# ------------------------------------------------------------------ adapters
class InMemoryAdapter:
    """Reads a CaseData, the same objects the runtime tools read."""
    name = "in_memory:CaseData"

    def __init__(self, case):
        self.case = case

    def run(self, qtype, p, scope):
        c = self.case
        if scope.tenant_id != c.tenant_id:
            raise ScopeError(f"tenant {scope.tenant_id!r} is not the case tenant")
        if qtype == "decision_history":
            if c.decision_store is None:
                return None, 0
            rows = c.decision_store.select("cop_decisions")
            inside = [r for r in rows if r.get("tenant_id") in (None, scope.tenant_id)
                      and (p.get("cop") is None or r.get("cop") == p["cop"])]
            return [{"ref": f"cop_decisions:{r['decision_id']}", **{k: r.get(k) for k in (
                "decision_id", "cop", "session_id", "interpretation", "decided_at")}} for r in inside], \
                len(rows) - len(inside)
        if qtype in ("metric_baseline", "behaviour_history"):
            if c.session_id not in scope.session_ids:
                raise ScopeError("the case session is not in scope")
            mids = [p["metric_id"]] if qtype == "metric_baseline" else list(p["metric_ids"])
            out = []
            for mid in mids:
                for e in c.metric_history(mid):
                    out.append({"ref": f"metric_events:{e['session_id']}:{mid}", "metric_id": mid,
                                "session_id": e["session_id"], "value": e["value"], "state": e["state"]})
            return out, 0
        if qtype == "policy_versions":
            ps = c.artifact("policy_store")
            if not ps:
                return None, 0
            return [{"ref": f"policy_store:{v['version']}", "version": v["version"],
                     "effective": v.get("effective"), "approved_by": v.get("approved_by"),
                     "rules": len(v.get("rules", []))} for v in ps.get("versions", [])], 0
        if qtype == "quota":
            q = c.artifact("quotas")
            if not q:
                return None, 0
            key = scope.agent_id if scope.agent_id in q else ("*" if "*" in q else None)
            return ([{"ref": f"quotas:{key}", "agent": key, **q[key]}] if key else []), 0
        if qtype == "agent_registry":
            reg = c.artifact("agent_registry")
            if not reg:
                return None, 0
            ents = [x for x in reg.get("agents", []) if x.get("agent") == scope.agent_id]
            return [{"ref": f"agent_registry:{x['agent']}", **x} for x in ents], 0
        if qtype == "context_snapshots":
            if c.session_id not in scope.session_ids:
                raise ScopeError("the case session is not in scope")
            snaps = c.artifact("context_snapshots")
            if not snaps:
                return None, 0
            return [{"ref": f"context_snapshots:{i}", **s}
                    for i, s in enumerate(sorted(snaps, key=lambda x: x.get("at", "")))], 0
        raise KeyError(qtype)


class SupabaseAdapter:
    """Disabled stub. Holds the contract only; never opens a connection."""
    name = "supabase:disabled"
    enabled = False

    def run(self, qtype, p, scope):
        return "DISABLED", 0


# ------------------------------------------------------------------ service
def _check_params(qtype, params):
    spec = QUERY_TYPES[qtype]["params"]
    extra = set(params) - set(spec)
    if extra:
        return f"unknown parameters {sorted(extra)}"
    for k, t in spec.items():
        v = params.get(k)
        if v is None:
            if "None" in t or (k == "window_days"):
                continue
            return f"missing parameter {k}"
        if t.startswith("list") and not (isinstance(v, (list, tuple)) and all(isinstance(x, str) for x in v)):
            return f"{k} must be a list of strings"
        if t == "str" and not isinstance(v, str):
            return f"{k} must be a string"
        if t == "int" and not isinstance(v, int):
            return f"{k} must be an integer"
    return None


class EvidenceQueryService:
    def __init__(self, adapter, permissions=None):
        self.adapter = adapter
        self.permissions = permissions or {k: tuple(v["proposed_query_types"]) for k, v in ASSESSMENT.items()}
        self.records = []

    def query(self, requesting_cop, query_type, params, scope, reason):
        t0 = time.perf_counter()
        params = dict(params or {})
        rid = str(uuid.uuid5(RECORD_NS, f"{requesting_cop}|{query_type}|{sorted(params.items())}|"
                                        f"{scope}|{len(self.records)}"))
        rec = RetrievalRecord(rid, SERVICE_VERSION, requesting_cop, query_type, params, reason,
                              {"tenant_id": scope.tenant_id, "session_ids": list(scope.session_ids),
                               "agent_id": scope.agent_id}, self.adapter.name, OK)
        rows = None
        if query_type not in QUERY_TYPES:
            rec.status, rec.detail = REJECTED_PARAMS, f"unknown query type {query_type!r}"
        elif query_type not in self.permissions.get(requesting_cop, ()):
            rec.status, rec.detail = REJECTED_NOT_PERMITTED, f"{requesting_cop} may not run {query_type}"
        elif not reason:
            rec.status, rec.detail = REJECTED_PARAMS, "a reason is required"
        elif (err := _check_params(query_type, params)):
            rec.status, rec.detail = REJECTED_PARAMS, err
        elif (miss := [s for s in QUERY_TYPES[query_type]["scope"] if not getattr(scope, s)]):
            rec.status, rec.detail = REJECTED_SCOPE, f"scope must carry {miss}"
        else:
            try:
                rows, filtered = self.adapter.run(query_type, params, scope)
                rec.filtered_out_of_scope = filtered
                if rows == "DISABLED":
                    rec.status, rec.detail, rows = DISABLED, "database adapter is disabled", None
                elif rows is None:
                    rec.status, rec.detail = MISSING_SOURCE, QUERY_TYPES[query_type]["in_memory_source"]
                else:
                    rows = rows[:QUERY_TYPES[query_type]["max_rows"]]
                    rec.status = OK if rows else EMPTY
                    rec.rows, rec.returned_refs = len(rows), [r["ref"] for r in rows]
            except ScopeError as e:
                rec.status, rec.detail, rows = REJECTED_SCOPE, str(e), None
        rec.latency_ms = round((time.perf_counter() - t0) * 1000, 3)
        self.records.append(rec)
        return rows, rec
