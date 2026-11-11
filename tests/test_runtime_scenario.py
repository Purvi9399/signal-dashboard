"""The integrated coding-agent scenario (cops/runtime/scenario.py), milestone 4.

Offline. The coding agent is a synthetic event generator, Anomaly Detection's and
Workflow Orchestration's model replies are scripted (simulated), Runtime Monitoring and
Behaviour Analytics run for real, and every action goes through the local sandbox
gateway (loopback endpoint, SQLite, mock runner). Each variant runs the full sequence
from agent event to persisted feedback, then reopens its stores to check the restart."""
import json
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "collector"))

import pytest

from cops.runtime import scenario as SC

TRACE_DIR = os.path.join(os.path.dirname(__file__), "..", "docs", "cops", "evaluation", "scenario")
SEQUENCE = ["agent_event", "telemetry_ingested", "initial_finding", "vs01_initial", "handling_quotient_initial",
            "request_for_support", "receiver_investigating", "receiver_answered", "referral_response",
            "revised_evidence_score", "investigation_final", "final_action", "authority_check",
            "action_execution", "outcome_confirmation", "feedback_persisted", "restart_check"]


@pytest.fixture(scope="module")
def runs(tmp_path_factory):
    return {v: SC.run_scenario(v, str(tmp_path_factory.mktemp(v))) for v in SC.VARIANTS}


def stages(r):
    return [e["stage"] for e in r["trace"]]


def first(r, stage):
    return next(e for e in r["trace"] if e["stage"] == stage)


@pytest.mark.parametrize("variant", SC.VARIANTS)
def test_every_variant_runs_the_whole_sequence_in_order(runs, variant):
    st = stages(runs[variant])
    idx = [st.index(s) for s in SEQUENCE]
    assert idx == sorted(idx), [s for s in SEQUENCE]
    ts = [e["at"] for e in runs[variant]["trace"]]
    assert ts == sorted(ts)                                     # timestamps never go backwards
    assert runs[variant]["summary"]["spent_usd"] == 0.0         # no paid model call anywhere


def test_successful_alert_is_delivered_confirmed_and_feedback_validates_memory(runs):
    r = runs["successful"]
    s = r["summary"]
    assert s["initial"]["label"] == "ANOMALOUS" and s["initial"]["decision"] == "SUPPORTED"
    assert s["initial"]["assumptions"] == ["telemetry_reliability"] and s["revised"]["assumptions"] == []
    assert s["revised"]["telemetry_reliability"] == 0.3        # Runtime Monitoring: telemetry INSUFFICIENT
    assert s["revised"]["support"] < s["initial"]["support"] and s["revised"]["unknown"] > s["initial"]["unknown"]
    assert dict(s["referrals"])["runtime_monitoring"] == "ANSWERED_VERIFIED"
    assert s["final_action"] == "ALERT" and s["execution_status"] == "EXECUTED_SANDBOX"
    assert s["gateway_state"] == "VERIFIED"
    assert s["after_restart"]["gateway_history"] == ["PROPOSED", "AUTHORISED", "DELIVERED", "APPLIED", "VERIFIED"]
    assert first(r, "outcome_confirmation")["alerts_at_endpoint"] == [first(r, "action_execution")["gateway_action_id"]]
    assert s["review"] == {"outcome": "CONFIRMED", "eligible_for_scoring": True, "applied_to_memory": True}
    assert s["after_restart"]["memory_validation_status"] == "VALIDATED"
    assert s["after_restart"]["ledger"] == [("execution_outcome", "VERIFIED"), ("review", "CONFIRMED")] or \
        s["after_restart"]["ledger"] == [["execution_outcome", "VERIFIED"], ["review", "CONFIRMED"]]


def test_contradictory_answer_revises_the_finding_and_nothing_is_delivered_on_it(runs):
    r = runs["contradictory"]
    s = r["summary"]
    tel = first(r, "telemetry_ingested")
    assert len(tel["tool_calls_dropped_by_collector"]) == 5     # the agent failed 5 times; telemetry lost them
    assert s["initial"]["label"] == "NORMAL" and s["revised"]["label"] == "INCONCLUSIVE"
    assert s["resolution"] == "REVISED" and s["final_tier"] == "assisted"
    assert s["revised"]["support"] < s["initial"]["support"]
    assert first(r, "outcome_confirmation")["alerts_at_endpoint"] == []
    assert s["review_task"] and s["gateway_state"] == "VERIFIED"     # the review task row was read back
    # the reviewer's verdict is kept but the finding did not verify: not eligible, memory unchanged
    assert s["review"]["eligible_for_scoring"] is False and s["review"]["applied_to_memory"] is False
    assert s["after_restart"]["scoring_history"] == []


def test_missing_evidence_stays_unresolved_and_goes_to_a_reviewer(runs):
    r = runs["missing_evidence"]
    s = r["summary"]
    assert first(r, "telemetry_ingested")["baseline_sessions"] == 0
    assert s["initial"]["label"] == s["revised"]["label"] == "INCONCLUSIVE"
    assert s["revised"]["unknown"] == 1.0
    assert s["resolution"] == "UNRESOLVED" and s["final_tier"] == "assisted"
    assert first(r, "investigation_final")["human_task"]["kind"] == "unresolved_uncertainty"
    assert s["review_task"] and s["review"]["eligible_for_scoring"] is False


def test_timeout_discards_the_late_answer_and_a_person_approves_the_alert(runs):
    r = runs["timeout"]
    s = r["summary"]
    ref = dict(s["referrals"])
    assert ref["runtime_monitoring"] == "TIMED_OUT"
    assert s["revised"]["telemetry_reliability"] == 0.9        # the late answer changed nothing
    assert s["final_tier"] == "human"
    assert first(r, "final_action")["requires_approval"]
    dec = first(r, "approval_decided")
    assert dec["decision"] == "approve" and dec["reviewer"] == SC.REVIEWER
    assert s["after_restart"]["gateway_history"] == ["PROPOSED", "AUTHORISED", "AWAITING_APPROVAL", "AUTHORISED",
                                                     "DELIVERED", "APPLIED", "VERIFIED"]
    # Runtime Monitoring timed out once and was not run again for Behaviour Analytics
    runs_rm = [e for e in r["trace"] if e["stage"] == "receiver_investigating"
               and e["investigation_event"]["receiver"] == "runtime_monitoring"]
    assert len(runs_rm) == 1


def test_denied_approval_delivers_nothing_and_the_refutation_rejects_memory(runs):
    r = runs["denied_approval"]
    s = r["summary"]
    assert first(r, "approval_decided")["decision"] == "deny"
    assert s["execution_status"] == "APPROVAL_REJECTED" and s["gateway_state"] == "APPROVAL_DENIED"
    assert first(r, "outcome_confirmation")["alerts_at_endpoint"] == []
    assert s["review"] == {"outcome": "REFUTED", "eligible_for_scoring": True, "applied_to_memory": True}
    assert s["after_restart"]["memory_validation_status"] == "REJECTED"


def test_authority_is_checked_on_the_final_evidence_in_every_variant(runs):
    for v, r in runs.items():
        a = first(r, "authority_check")
        assert a["authorised"] is True and {c["check_id"] for c in a["checks"]} >= {"AU-01", "PE-01"}, v
        fa = first(r, "final_action")
        assert fa["exec_decision_id"] != first(r, "initial_finding")["decision_id"], v


def test_saved_traces_are_these_runs(runs):
    """The dashboard panel reads docs/cops/evaluation/scenario/*.json. Those files must be
    output of this scenario code, not hand-made: their summaries match a fresh run."""
    for v, r in runs.items():
        p = os.path.join(TRACE_DIR, f"{v}.json")
        assert os.path.exists(p), f"run: python3 -m cops.runtime.scenario --out {TRACE_DIR}"
        saved = json.load(open(p))
        assert json.loads(json.dumps(r["summary"], default=str)) == saved["summary"], v
        assert [e["stage"] for e in saved["trace"]] == stages(r), v
