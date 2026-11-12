"""The typed, read-only Evidence Query Service (collector/cops/runtime/evidence_query.py).

In memory only: the service reads a CaseData built by the evaluation harness. The
Supabase adapter is a disabled stub and must never return rows."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "collector"))

import pytest

from cops.runtime import evaluation as E
from cops.runtime import evidence_query as Q
from cops.runtime.persistence import SQLiteStore


@pytest.fixture
def case():
    rt = E.make_runtime("eq-1", "sp1", E._estate(E.spike_session("sp1", 9)), {}, None,
                        decision_store=SQLiteStore())
    return rt.case


def scope(c, **kw):
    return Q.Scope(tenant_id=kw.get("tenant", c.tenant_id), session_ids=kw.get("sessions", (c.session_id,)),
                   agent_id=kw.get("agent", c.agent_scope))


def test_baseline_query_returns_refs_and_record(case):
    svc = Q.EvidenceQueryService(Q.InMemoryAdapter(case))
    rows, rec = svc.query("anomaly_detection", "metric_baseline", {"metric_id": "insights.token_outlier"},
                          scope(case), reason="baseline for token outlier")
    assert rec.status in (Q.OK, Q.EMPTY)
    assert rec.rows == len(rows) == len(rec.returned_refs)
    assert all(r["session_id"] != case.session_id for r in rows)
    d = rec.to_dict()
    for k in ("requesting_cop", "query_type", "reason", "scope", "data_source", "returned_refs", "status",
              "latency_ms", "effect_on_determination"):
        assert k in d
    assert d["data_source"] == "in_memory:CaseData"


def test_unknown_query_type_and_free_text_are_rejected(case):
    svc = Q.EvidenceQueryService(Q.InMemoryAdapter(case))
    _, rec = svc.query("anomaly_detection", "SELECT * FROM observables", {}, scope(case), reason="x")
    assert rec.status == Q.REJECTED_PARAMS
    _, rec = svc.query("anomaly_detection", "metric_baseline", {"metric_id": "x", "sql": "drop"}, scope(case), "x")
    assert rec.status == Q.REJECTED_PARAMS


def test_cop_permissions(case):
    svc = Q.EvidenceQueryService(Q.InMemoryAdapter(case))
    _, rec = svc.query("anomaly_detection", "quota", {}, scope(case), reason="not mine")
    assert rec.status == Q.REJECTED_NOT_PERMITTED
    _, rec = svc.query("workflow_orchestration", "decision_history", {}, scope(case), reason="no assessment")
    assert rec.status == Q.REJECTED_NOT_PERMITTED


def test_scope_enforced(case):
    svc = Q.EvidenceQueryService(Q.InMemoryAdapter(case))
    _, rec = svc.query("anomaly_detection", "metric_baseline", {"metric_id": "insights.retry"},
                       scope(case, tenant="other-tenant"), reason="cross tenant")
    assert rec.status == Q.REJECTED_SCOPE
    _, rec = svc.query("anomaly_detection", "metric_baseline", {"metric_id": "insights.retry"},
                       scope(case, sessions=("someone-else",)), reason="wrong session")
    assert rec.status == Q.REJECTED_SCOPE
    _, rec = svc.query("resource_management", "quota", {}, scope(case, agent=None), reason="no agent")
    assert rec.status == Q.REJECTED_SCOPE


def test_missing_source_is_typed(case):
    svc = Q.EvidenceQueryService(Q.InMemoryAdapter(case))
    _, rec = svc.query("policy_enforcement", "policy_versions", {}, scope(case), reason="policy lookup")
    assert rec.status == Q.MISSING_SOURCE and "policy_store" in rec.detail


def test_decision_history_reads_local_store(case):
    svc = Q.EvidenceQueryService(Q.InMemoryAdapter(case))
    rows, rec = svc.query("governance_intelligence", "decision_history", {}, scope(case), reason="trend")
    assert rec.status == Q.EMPTY and rows == []


def test_supabase_adapter_is_disabled(case):
    svc = Q.EvidenceQueryService(Q.SupabaseAdapter())
    rows, rec = svc.query("anomaly_detection", "metric_baseline", {"metric_id": "insights.retry"}, scope(case), "x")
    assert rows is None and rec.status == Q.DISABLED


def test_assessment_and_audit():
    assert all(a["reaches_database"] is False for a in Q.ASSESSMENT.values())
    for a in Q.ASSESSMENT.values():
        assert set(a["proposed_query_types"]) <= set(Q.QUERY_TYPES)
    paths = {p["path"]: p for p in Q.DATA_ACCESS_AUDIT["paths"]}
    assert any(p["reaches_database"] is True and "SupabaseBackend" in p["code_ref"] for p in paths.values())
