"""COPS activation contract and router (cops/runtime/activation.py, router.py).

Offline: synthetic debugging sessions with collector schema fields, the real COP subgraph in
dry-run, no model (Anomaly Detection runs with UnavailableProvider), no database."""
import os
import sys
import threading
import time
from dataclasses import replace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "collector"))

import pytest

from cops.runtime import activation as A
from cops.runtime import router as R
from cops.runtime import activation_scenario as SC

SLICE = set(A.SLICE)


def verdicts(out):
    return {r.cop: r.transition for r in out["records"] if r.transition in (A.ELIGIBLE, A.NOT_ELIGIBLE, A.UNKNOWN)}


def transitions(out, cop):
    return [r.transition for r in out["records"] if r.cop == cop]


# ---------------------------------------------------------------- contracts and governance

def test_default_bundle_is_valid_and_versioned():
    b = A.default_bundle()
    assert A.validate_bundle(b) == []
    assert b.version.startswith("map-") and b.version == A.default_bundle().version
    assert {p.cop for p in b.policies} == SLICE
    for p in b.policies:
        assert p.owner == p.cop and p.rule_ref in A.RULE_KINDS
        assert set(p.depends_on) <= set(A.CONSUMES[p.cop])
    for m in b.metrics:
        assert m.provenance["code_ref"].startswith("collector/signal_metrics.py:") and m.quality
    for e in b.edges:
        assert e.behavioural_question and e.spec_version
        assert (e.interpretation_ref is not None) == (e.interpretation_status == "DEFINED")


def test_undefined_or_supporting_interpretation_cannot_trigger():
    b = A.default_bundle()
    ba = b.policy("behavior_analytics")
    # an edge made UNDEFINED
    edges = tuple(replace(e, interpretation_status="UNDEFINED", interpretation_ref=None)
                  if (e.cop, e.metric_id) == ("behavior_analytics", "insights.retry") else e for e in b.edges)
    errs = A.validate_bundle(replace(b, edges=edges))
    assert any("insights.retry interpretation is UNDEFINED" in x for x in errs)
    # a metric with no edge to the COP
    bad = replace(ba, conditions=ba.conditions + (A.Condition("oversight.declined_inferred", "WARNING"),))
    errs = A.validate_bundle(replace(b, policies=tuple(bad if p.cop == ba.cop else p for p in b.policies)))
    assert any("no edge" in x for x in errs)
    # a supporting-only metric (AD: insights.retry never drives)
    ad = b.policy("anomaly_detection")
    bad = replace(ad, conditions=ad.conditions + (A.Condition("insights.retry", "EXCEPTION"),))
    errs = A.validate_bundle(replace(b, policies=tuple(bad if p.cop == ad.cop else p for p in b.policies)))
    assert any("supporting-only" in x for x in errs)


def test_dependency_cycle_rejected():
    b = A.default_bundle()
    pols = tuple(replace(p, depends_on=("recovery_resilience",)) if p.cop == "runtime_monitoring" else p
                 for p in b.policies)
    errs = A.validate_bundle(replace(b, policies=pols))
    assert any(x.startswith("dependency cycle") for x in errs)
    reg = A.MappingRegistry()
    e = reg.propose(replace(b, policies=pols), "dev@tenant-a.example", A.HUMAN, "cycle")
    assert e.status == A.REJECTED
    with pytest.raises(A.NotApproved):
        reg.approve(replace(b, policies=pols).version, "lead@tenant-a.example", A.HUMAN)


def test_consultation_contract_limits():
    b = A.default_bundle()
    deep = replace(b.consultations[0], max_depth=A.RF.MAX_DEPTH + 1)
    outside = A.ConsultationContract("behavior_analytics", "recovery_resilience", "upstream_evidence", "q", "w",
                                     1, 1.0, "test")
    errs = A.validate_bundle(replace(b, consultations=(deep, outside)))
    assert any("depth or time limit" in x for x in errs)
    assert any("not consumed by the initiator" in x for x in errs)


def test_unapproved_mapping_change_cannot_take_effect():
    reg = SC.approved_registry()
    v1 = reg.active_version
    prop = reg.propose_from_feedback(v1, {"behavior_analytics": {"insights.retry": "WARNING"}},
                                     "feedback:review-123", "reviewer said BA missed a loop")
    v2 = prop.bundle.version
    assert prop.status == A.PROPOSED and reg.active_version == v1
    with pytest.raises(A.NotApproved):
        R.Router(reg, SC.factory, version=v2)                       # not approved: cannot route
    with pytest.raises(A.NotApproved):
        reg.activate(v2)
    with pytest.raises(A.NotApproved):
        reg.approve(v2, "feedback:review-123", "feedback")           # feedback cannot approve
    with pytest.raises(A.NotApproved):
        reg.approve(v2, "feedback:review-123", A.HUMAN)              # nor the proposer
    reg.approve(v2, SC.REVIEWER, A.HUMAN)
    assert reg.active_version == v1                                  # approved is not active
    out, _, _ = SC.run_variant("normal_debugging", registry=reg)
    assert out["mapping_version"] == v1 and verdicts(out)["behavior_analytics"] == A.NOT_ELIGIBLE


# ---------------------------------------------------------------- routing scenarios

def test_normal_debugging():
    out, _, _ = SC.run_variant("normal_debugging")
    v = verdicts(out)
    assert v == {"runtime_monitoring": A.ELIGIBLE, "behavior_analytics": A.NOT_ELIGIBLE,
                 "anomaly_detection": A.NOT_ELIGIBLE, "recovery_resilience": A.NOT_ELIGIBLE}
    ba = next(r for r in out["records"] if r.cop == "behavior_analytics" and r.transition == A.NOT_ELIGIBLE)
    assert any("insights.retry=1 is WARNING" in x for x in ba.reasons)
    assert set(out["results"]) == {"runtime_monitoring"}


def test_repeated_unproductive_failures():
    out, _, _ = SC.run_variant("repeated_failures")
    v = verdicts(out)
    assert v["behavior_analytics"] == v["recovery_resilience"] == A.ELIGIBLE
    assert v["anomaly_detection"] == A.NOT_ELIGIBLE
    assert out["results"]["behavior_analytics"]["finding"]["label"] == "POOR_RECOVERY"
    # R&R waits for BA, then runs with BA's finding
    assert transitions(out, "recovery_resilience")[:4] == [A.CANDIDATE, A.ELIGIBLE, A.WAITING, A.ACTIVATED]
    seq = {(r.cop, r.transition): r.seq for r in out["records"]}
    assert seq[("behavior_analytics", A.COMPLETED)] < seq[("recovery_resilience", A.ACTIVATED)]


def test_missing_evidence_is_unknown_not_eligible_or_quiet():
    out, _, _ = SC.run_variant("missing_evidence")
    v = verdicts(out)
    assert v["behavior_analytics"] == A.UNKNOWN and v["recovery_resilience"] == A.UNKNOWN
    assert v["runtime_monitoring"] == A.ELIGIBLE                    # ACTIVATE_TO_ASSESS
    assert out["results"]["runtime_monitoring"]["finding"]["label"] == "INSUFFICIENT"
    assert "behavior_analytics" not in out["results"]


def test_concurrent_eligibility_runs_in_parallel():
    starts, lock = {}, threading.Lock()

    def hook(cop):
        with lock:
            starts[cop] = time.monotonic()
        time.sleep(0.3)
    reg = SC.approved_registry()
    out, _, _ = SC.run_variant("concurrent", registry=reg, router=R.Router(reg, SC.factory, run_hook=hook))
    v = verdicts(out)
    assert v["anomaly_detection"] == v["recovery_resilience"] == A.ELIGIBLE
    act = {r.cop: dict(r.detail) for r in out["records"] if r.transition == A.ACTIVATED}
    assert act["anomaly_detection"]["wave"] == act["recovery_resilience"]["wave"]
    assert act["anomaly_detection"]["concurrent_with"] == ("recovery_resilience",)
    assert abs(starts["anomaly_detection"] - starts["recovery_resilience"]) < 0.25   # overlapped
    assert starts["behavior_analytics"] < starts["anomaly_detection"] - 0.25          # dependency first


def test_typed_specialist_consultation():
    out, _, _ = SC.run_variant("consultation")
    v = verdicts(out)
    assert v["behavior_analytics"] == A.NOT_ELIGIBLE and v["recovery_resilience"] == A.ELIGIBLE
    # BA was not activated as a primary, so RR runs without waiting on it and consults it instead
    assert not any(r.cop == "behavior_analytics" and r.transition == A.ACTIVATED for r in out["records"])
    done = dict(next(r for r in out["records"] if r.cop == "recovery_resilience" and r.transition == A.COMPLETED).detail)
    import json
    c = json.loads(done["consultation"])
    assert c["via"] == "referrals.run_investigation"
    assert [(x["receiver"], x["status"]) for x in c["responses"]] == [("behavior_analytics", "ANSWERED_VERIFIED")]


def test_consultation_without_contract_is_not_opened():
    reg = A.MappingRegistry()
    b = A.default_bundle()
    b2 = replace(b, consultations=tuple(c for c in b.consultations if c.initiator != "recovery_resilience"))
    reg.propose(b2, "dev@tenant-a.example", A.HUMAN, "no R&R consultation")
    reg.approve(b2.version, SC.REVIEWER, A.HUMAN)
    reg.activate(b2.version)
    out, _, _ = SC.run_variant("consultation", registry=reg)
    done = dict(next(r for r in out["records"] if r.cop == "recovery_resilience" and r.transition == A.COMPLETED).detail)
    assert done["consultation"] is None                             # ran through run_cop, nobody consulted


def test_duplicate_events_and_activations():
    reg = SC.approved_registry()
    r = R.Router(reg, SC.factory)
    rows = SC.with_tenant(SC.repeated_failures())
    est = SC.with_tenant(SC.debugging_estate())
    first = r.route(rows + rows, estate_rows=est)[0]               # duplicate delivery inside one batch
    assert first["duplicate_events_dropped"] == len(rows)
    again = r.route(rows, estate_rows=est)[0]                      # the same events again
    sup = [x for x in again["records"] if x.transition == A.SUPPRESSED]
    assert {x.cop for x in sup} == {c for c, v in verdicts(first).items() if v == A.ELIGIBLE}
    assert all(dict(x.detail)["duplicate_of"] for x in sup) and not again["results"]


def test_timeout_is_recorded_and_dependents_proceed():
    def hook(cop):
        if cop == "behavior_analytics":
            time.sleep(1.5)
    reg = SC.approved_registry()
    out, _, _ = SC.run_variant("repeated_failures", registry=reg,
                               router=R.Router(reg, SC.factory, run_hook=hook, timeout_s=0.5))
    assert A.TIMED_OUT in transitions(out, "behavior_analytics")
    rr = next(x for x in out["records"] if x.cop == "recovery_resilience" and x.transition == A.ACTIVATED)
    assert any("behavior_analytics produced no finding" in y for y in rr.reasons)


def test_run_budget_suppresses():
    reg = SC.approved_registry()
    out, _, _ = SC.run_variant("repeated_failures", registry=reg, router=R.Router(reg, SC.factory, max_runs=1))
    assert A.SUPPRESSED in transitions(out, "behavior_analytics")


def test_gates_preserved_and_decisions_linked():
    out, _, _ = SC.run_variant("missing_evidence")
    store = out["runtime"].store
    for x in out["records"]:
        if x.transition == A.COMPLETED:
            d = dict(x.detail)
            assert d["authority_stage"] and d["execution_status"].startswith("NOT_EXECUTED")   # dry-run
            assert store.decisions(decision_id=x.decision_id), x.cop


def test_records_are_immutable_and_chained():
    out, rt, _ = SC.run_variant("repeated_failures")
    with pytest.raises(Exception):
        out["records"][0].transition = "ACTIVATED"
    assert rt.log.verify_chain(out["investigation_id"])


def test_sqlite_log_is_insert_only(tmp_path):
    log = R.ActivationLog(str(tmp_path / "act.sqlite"))
    reg = SC.approved_registry()
    out, _, _ = SC.run_variant("normal_debugging", registry=reg, log=log)
    rid = out["records"][0].record_id
    import sqlite3
    with pytest.raises(sqlite3.IntegrityError):
        log.conn.execute("INSERT INTO cop_activation_records (record_id) VALUES (?)", (rid,))


def test_deterministic_replay_with_pinned_version():
    reg = SC.approved_registry()
    v1 = reg.active_version
    a, _, _ = SC.run_variant("concurrent", registry=reg)
    # a later approved and activated change does not alter a pinned replay
    prop = reg.propose_from_feedback(v1, {"behavior_analytics": {"insights.retry": "WARNING"}},
                                     "feedback:review-9", "lower BA threshold")
    reg.approve(prop.bundle.version, SC.REVIEWER, A.HUMAN)
    reg.activate(prop.bundle.version)
    b, _, _ = SC.run_variant("concurrent", registry=reg, version=v1)
    assert [r.canonical() for r in a["records"]] == [r.canonical() for r in b["records"]]
    n, _, _ = SC.run_variant("normal_debugging", registry=reg)       # the new version, now active
    assert n["mapping_version"] == prop.bundle.version
    assert verdicts(n)["behavior_analytics"] == A.ELIGIBLE
