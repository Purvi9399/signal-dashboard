"""Persistent memory: policy, scope, lifecycle, adaptation limits."""
import os, sys
from datetime import datetime, timedelta, timezone
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "collector"))

import pytest

from cops.runtime.memory import MemoryGateway, MemoryAccessDenied, POLICIES
from cops.runtime.persistence import SQLiteStore
from cops.specs import BY_KEY

NOW = datetime(2026, 10, 29, tzinfo=timezone.utc)
PROV = {"case_id": "c1", "finding_id": "f1"}


@pytest.fixture
def gw():
    return MemoryGateway(SQLiteStore(), clock=lambda: NOW)


def test_every_cop_has_a_namespace_and_reads_what_it_writes():
    assert set(POLICIES) == set(BY_KEY)
    for k, p in POLICIES.items():
        assert p["writes"] and set(p["writes"]) <= set(p["reads"]), k
        assert p["scope"] in ("tenant", "agent")


def test_audit_policy_gaps_are_fixed():
    assert "anomaly_observation" in POLICIES["anomaly_detection"]["reads"]
    assert "context_integrity_finding" in POLICIES["behavior_analytics"]["reads"]


def test_write_only_own_types_and_never_self_validate(gw):
    with pytest.raises(MemoryAccessDenied):
        gw.write("anomaly_detection", "c1", "t1", "incident_outcome", {}, PROV)
    with pytest.raises(MemoryAccessDenied):
        gw.write("anomaly_detection", "c1", "t1", "anomaly_observation", {}, PROV, status="VALIDATED")
    with pytest.raises(MemoryAccessDenied):
        gw.write("anomaly_detection", "c1", "t1", "anomaly_observation", {}, {})
    assert len(gw.access_log(outcome="DENIED")) == 3


def test_feedback_lifecycle(gw):
    v = gw.write("anomaly_detection", "c1", "t1", "anomaly_observation", {"x": 1}, PROV, status="VERIFIED")
    u = gw.write("anomaly_detection", "c2", "t1", "anomaly_observation", {"x": 2}, {"case_id": "c2", "finding_id": "f2"})
    r = gw.read("anomaly_detection", "c3", "t1")
    assert [x["record_id"] for x in r["verified_pending"]] == [v["record_id"]]
    assert [x["record_id"] for x in r["unverified"]] == [u["record_id"]] and r["established"] == []
    with pytest.raises(ValueError):
        gw.record_feedback(v["record_id"], "t1", "VALIDATED", "")          # needs a reviewer
    with pytest.raises(MemoryAccessDenied):
        gw.record_feedback(u["record_id"], "t1", "VALIDATED", "rev")       # unverified cannot be promoted
    gw.record_feedback(v["record_id"], "t1", "VALIDATED", "rev")
    r = gw.read("anomaly_detection", "c3", "t1")
    assert [x["record_id"] for x in r["established"]] == [v["record_id"]]
    gw.record_feedback(v["record_id"], "t1", "REJECTED", "rev2")
    assert gw.read("anomaly_detection", "c3", "t1")["established"] == []


def test_tenant_isolation(gw):
    b = gw.write("anomaly_detection", "cb", "tenant-b", "anomaly_observation", {"s": 1},
                 {"case_id": "cb", "finding_id": "fb"}, status="VERIFIED")
    r = gw.read("anomaly_detection", "ca", "tenant-a")
    assert r["verified_pending"] == [] and r["excluded"] == 0               # not even counted
    with pytest.raises(MemoryAccessDenied):
        gw.read("anomaly_detection", "ca", "tenant-a", requested_tenant="tenant-b")
    with pytest.raises(MemoryAccessDenied):
        gw.get(b["record_id"], "tenant-a", "anomaly_detection", "ca")
    with pytest.raises(MemoryAccessDenied):
        gw.record_feedback(b["record_id"], "tenant-a", "VALIDATED", "rev")
    assert len(gw.access_log(tenant_id="tenant-a", outcome="DENIED")) == 3


def test_read_policy_and_agent_scope(gw):
    with pytest.raises(MemoryAccessDenied):
        gw.read("anomaly_detection", "c", "t1", record_types=("incident_outcome",))
    gw.write("anomaly_detection", "c", "t1", "anomaly_observation", {}, PROV, agent_scope="alice", status="VERIFIED")
    gw.write("anomaly_detection", "c", "t1", "anomaly_observation", {}, PROV, agent_scope="bob", status="VERIFIED")
    r = gw.read("anomaly_detection", "c", "t1", agent_scope="alice")
    assert [x["agent_scope"] for x in r["verified_pending"]] == ["alice"]


def test_retention_expires_records():
    clock = {"now": NOW}
    gw = MemoryGateway(SQLiteStore(), clock=lambda: clock["now"], retention_days={"anomaly_observation": 1})
    gw.write("anomaly_detection", "c", "t1", "anomaly_observation", {}, PROV, status="VERIFIED")
    clock["now"] = NOW + timedelta(days=2)
    r = gw.read("anomaly_detection", "c", "t1")
    assert r["verified_pending"] == [] and r["excluded"] == 1


def test_baseline_change_is_only_ever_a_proposal(gw):
    v = gw.write("anomaly_detection", "c", "t1", "anomaly_observation", {}, PROV, status="VERIFIED")
    u = gw.write("anomaly_detection", "c", "t1", "anomaly_observation", {}, {"case_id": "c9", "finding_id": "f9"})
    gw.record_feedback(v["record_id"], "t1", "VALIDATED", "rev")
    ok = gw.propose_baseline_update("anomaly_detection", "t1", [v["record_id"]], {"m": 1})
    assert ok["admissible"] and not ok["applied"] and ok["requires_human_approval"]
    bad = gw.propose_baseline_update("anomaly_detection", "t1", [v["record_id"], u["record_id"]], {"m": 1})
    assert not bad["admissible"] and bad["refused"][0]["record_id"] == u["record_id"]
