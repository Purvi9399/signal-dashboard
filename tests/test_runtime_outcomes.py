"""Outcome feedback and memory across investigations and restarts (milestone 3).

The store is a SQLite file in tmp_path; a "restart" is a new SQLiteStore on the same file
with new gateways. Model replies are scripted (simulated)."""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "collector"))

import pytest

from cops.runtime import evaluation as E, fixtures as FX
from cops.runtime.graph import run_cop
from cops.runtime.memory import MemoryGateway, MemoryAccessDenied, independent_reviewer
from cops.runtime.outcomes import OutcomeLedger
from cops.runtime.persistence import SQLiteStore

REVIEWER = "reviewer@tenant-a.example"


def ad_run(store, case_id, sid="sp1", day=9):
    rows = E._estate(E.spike_session(sid, day))
    rt = E.make_runtime(case_id, sid, rows, {}, FX.Script({"anomaly_detection": E.ad_spike_steps()}), store=store)
    return rt, run_cop(rt, "anomaly_detection")


def record_of(out):
    return out["result"]["memory_write"]["record_id"]


def test_rerunning_a_case_keeps_reviewer_feedback(tmp_path):
    store = SQLiteStore(str(tmp_path / "cops.sqlite"))
    rt, out = ad_run(store, "case-a")
    rid = record_of(out)
    rt.memory.record_feedback(rid, rt.case.tenant_id, "VALIDATED", REVIEWER, "confirmed spike")
    rt2, out2 = ad_run(store, "case-a")                         # the same case again
    assert record_of(out2) == rid                              # same deterministic id ...
    rec = [r for r in store.select("cop_memory_records") if r["record_id"] == rid][0]
    assert rec["validation_status"] == "VALIDATED"             # ... but the feedback was not wiped
    assert rec["provenance"]["feedback"]["reviewer"] == REVIEWER
    assert rec["provenance"]["re_observed"][-1]["case_id"] == "case-a"
    assert any(r["outcome"] == "KEPT" for r in rt2.memory.access_log())


def test_rerunning_a_case_does_not_resurrect_a_rejected_record(tmp_path):
    store = SQLiteStore(str(tmp_path / "cops.sqlite"))
    rt, out = ad_run(store, "case-b")
    rid = record_of(out)
    rt.memory.record_feedback(rid, rt.case.tenant_id, "REJECTED", REVIEWER, "load test, expected")
    ad_run(store, "case-b")
    rec = [r for r in store.select("cop_memory_records") if r["record_id"] == rid][0]
    assert rec["validation_status"] == "REJECTED"


@pytest.mark.parametrize("who", ["cop:anomaly_detection", "anomaly_detection", "gateway", "system", "runtime:x"])
def test_a_cop_the_gateway_or_the_runtime_cannot_validate(tmp_path, who):
    store = SQLiteStore(str(tmp_path / "cops.sqlite"))
    rt, out = ad_run(store, "case-c")
    assert not independent_reviewer(who)
    with pytest.raises(MemoryAccessDenied):
        rt.memory.record_feedback(record_of(out), rt.case.tenant_id, "VALIDATED", who)


def test_feedback_and_outcomes_survive_a_restart_and_reach_a_later_investigation(tmp_path):
    path = str(tmp_path / "cops.sqlite")
    store = SQLiteStore(path)
    rt, out = ad_run(store, "case-1")
    f = out["finding"]
    led = OutcomeLedger(store, rt.memory)
    led.record_execution("case-1", rt.case.tenant_id, "anomaly_detection", f["finding_id"], "d1", "act-1", "VERIFIED")
    rev = led.record_review("case-1", rt.case.tenant_id, "anomaly_detection", f["finding_id"], "d1", "CONFIRMED",
                            REVIEWER, f["verified"], memory_record_id=record_of(out), note="confirmed")
    assert rev["eligible_for_precedent"] and rev["applied_to_memory"]
    store.conn.close()
    # restart: new store, new gateways, same file
    store2 = SQLiteStore(path)
    led2 = OutcomeLedger(store2, MemoryGateway(store2, clock=E.CLOCK))
    kinds = sorted(e["kind"] for e in led2.for_case("case-1"))
    assert kinds == ["execution_outcome", "review"]
    # a separate, later investigation of another session sees the validated record as precedent
    rt3, out3 = ad_run(store2, "case-2", sid="sp2", day=10)
    est = rt3.memory.read("anomaly_detection", "case-2", rt3.case.tenant_id,
                          agent_scope=rt3.case.agent_scope)["established"]
    assert [r["record_id"] for r in est] == [record_of(out)]
    # and the investigation itself retrieved it (retrieve_memory node) as established precedent
    assert [r["record_id"] for r in out3["memory_view"]["established"]] == [record_of(out)]


def test_only_independent_review_of_a_verified_finding_is_eligible(tmp_path):
    store = SQLiteStore(str(tmp_path / "cops.sqlite"))
    rt, out = ad_run(store, "case-e")
    f = out["finding"]
    led = OutcomeLedger(store, rt.memory)
    ex = led.record_execution("case-e", "tenant-a", "anomaly_detection", f["finding_id"], "d", "a", "VERIFIED")
    assert not ex["eligible_for_precedent"] and not ex["eligible_for_scoring"]
    selfrev = led.record_review("case-e", "tenant-a", "anomaly_detection", f["finding_id"], "d", "CONFIRMED",
                                "cop:anomaly_detection", True, memory_record_id=record_of(out))
    assert not selfrev["eligible_for_scoring"] and not selfrev["applied_to_memory"]
    unver = led.record_review("case-e", "tenant-a", "anomaly_detection", f["finding_id"], "d", "CONFIRMED",
                              REVIEWER, False, memory_record_id=record_of(out))
    assert not unver["eligible_for_scoring"] and not unver["applied_to_memory"]
    good = led.record_review("case-e", "tenant-a", "anomaly_detection", f["finding_id"], "d", "REFUTED",
                             REVIEWER, True, memory_record_id=record_of(out), note="planned load test")
    assert good["eligible_for_scoring"] and good["applied_to_memory"]
    hist = led.scoring_history("anomaly_detection")
    assert [h["feedback_id"] for h in hist] == [good["feedback_id"]]
    rec = [r for r in store.select("cop_memory_records") if r["record_id"] == record_of(out)][0]
    assert rec["validation_status"] == "REJECTED"


def test_ledger_entries_are_written_once_and_never_rewritten(tmp_path):
    store = SQLiteStore(str(tmp_path / "cops.sqlite"))
    led = OutcomeLedger(store)
    a = led.record_execution("c", "t", "anomaly_detection", "f", "d", "a", "VERIFIED", detail={"n": 1})
    b = led.record_execution("c", "t", "anomaly_detection", "f", "d", "a", "VERIFIED", detail={"n": 2})
    assert a["inserted"] and not b["inserted"]
    assert led.for_case("c")[0]["detail"] == {"n": 1}
