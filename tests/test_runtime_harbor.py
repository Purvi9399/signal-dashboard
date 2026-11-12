"""First COPS validation on Harbor rows (cops/runtime/harbor_eval.py), on the real trial
fixtures in tests/fixtures/harbor/. No model is called: Anomaly Detection runs with
UnavailableProvider and must say it could not investigate, not guess."""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "collector"))

import signal_harbor as H
from cops.runtime import harbor_eval as HE

FIX = os.path.join(os.path.dirname(__file__), "fixtures", "harbor")


def _case(tmp_path, job):
    store, done, rej = H.ingest_paths([os.path.join(FIX, "h01-scripted-1"), os.path.join(FIX, "h02-scripted")],
                                      "dev-a", H.IngestStore(), tenant=HE.TENANT)
    assert not rej
    rep = next(r for r in done if r["trial_dir"].startswith(job + "/"))
    return rep, HE.run_case_for(rep["session_id"], f"t-{job}", store.rows(), str(tmp_path))


def test_every_record_carries_the_required_fields(tmp_path):
    rep, res = _case(tmp_path, "h02-scripted")
    assert [r["cop"] for r in res["records"]] == list(HE.COPS)
    for r in res["records"]:
        for k in ("candidate_label", "evidence_refs", "vs01", "derived_thresholds", "hard_verifiers",
                  "graded_decision", "quotient", "route", "typed_action", "authority", "execution"):
            assert k in r, (r["cop"], k)
        assert {"score", "against", "unknown", "decision"} <= set(r["vs01"])


def test_no_model_means_no_anomaly_verdict(tmp_path):
    _, res = _case(tmp_path, "h02-scripted")
    ad = next(r for r in res["records"] if r["cop"] == "anomaly_detection")
    assert ad["candidate_label"] == "INCONCLUSIVE"
    assert ad["vs01"]["decision"] == "INSUFFICIENT_EVIDENCE"
    assert ad["llm"]["stop_reason"] == "llm_unavailable"
    assert ad["typed_action"] == "NONE" and ad["execution"]["gateway_action_id"] is None


def test_behaviour_reads_the_harbor_evidence(tmp_path):
    _, res = _case(tmp_path, "h02-scripted")
    ba = next(r for r in res["records"] if r["cop"] == "behavior_analytics")
    assert ba["candidate_label"] == "POOR_RECOVERY"       # what the unchanged scoring rules say; see the report
    assert all(h["result"] == "PASS" for h in ba["hard_verifiers"])
    assert any(x.startswith("observable:") for x in ba["evidence_refs"])


def test_act_phase_goes_through_the_sandbox_only(tmp_path):
    _, res = _case(tmp_path, "h01-scripted-1")
    rm = next(r for r in res["records"] if r["cop"] == "runtime_monitoring")
    assert rm["candidate_label"] == "INSUFFICIENT"
    assert rm["execution"]["decide_phase"].startswith("NOT_EXECUTED")
    if rm["execution"]["gateway_action_id"]:
        assert rm["execution"]["gateway_history"][-1] == "VERIFIED"
    assert res["alerts_at_endpoint"] == sum(1 for a in res["gateway_actions"] if a["typed_action"] == "ALERT")
