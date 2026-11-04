"""The action lifecycle: every stage the demo distinguishes, the dry-run
adapter's lack of effect, and the original proposal kept for audit."""

import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "collector"))

from cops.specs import BY_KEY
from cops.runtime import actions as A
from cops.runtime import evaluation as E
from cops.runtime import fixtures as FX
from cops.runtime import live_eval as LE
from cops.runtime import synthetic as S
from cops.runtime import test_bench as TB
from cops.runtime.bench_cases import DET_CASES
from cops.runtime.graph import MockEnforcementGateway, run_case
from cops.runtime.llm import UnavailableProvider


def _det(cid, gw=None):
    case = next(c for c in DET_CASES if c["id"] == cid)
    sid = cid.replace(":", "-").replace("_", "")[:24]
    b = case["build"](sid)
    rt = E.make_runtime(f"act-{sid}", sid, b["rows"], b["art"], provider=UnavailableProvider(),
                        enforcement=gw or MockEnforcementGateway())
    out = run_case(rt, cops=LE.deterministic_upstream(case["cop"]))
    return out["results"][case["cop"]], rt


def test_no_action_proposed_has_no_receipt():
    r, _ = _det("policy_enforcement:normal")
    lc = r["proposed_action"]["lifecycle"]
    assert lc["stage"] == A.NO_ACTION_PROPOSED and lc["final_action"] == "NONE"
    assert lc["dry_run_receipt"] is None


def test_block_in_dry_run_is_rejected_by_authority_and_the_original_is_kept():
    gw = MockEnforcementGateway()
    r, _ = _det("policy_enforcement:abnormal", gw)
    pa = r["proposed_action"]
    lc = pa["lifecycle"]
    assert r["finding"]["label"] == "VIOLATION" and r["finding"]["verified"] is True
    assert pa["original_proposal"]["action"] == "BLOCK"                  # kept for audit
    assert pa["original_verification"]["result"] == "FAIL"               # AU-01 on the original
    assert lc["stage"] == A.REJECTED_BY_AUTHORITY and lc["candidate_action"] == "BLOCK"
    assert lc["substitute"] == {"action": "ALERT", "stage": A.AUTHORISED_DRY_RUN, "source": "authority substitute"}
    assert pa["execution_status"] == "NOT_EXECUTED_DRY_RUN"
    assert gw.applied == []                                             # nothing reached the gateway
    assert lc["dry_run_receipt"]["action"] == "ALERT" and lc["dry_run_receipt"]["external_effect"] is False
    assert lc["external_effect"] is False


def test_authorised_alert_in_dry_run_is_recorded_by_the_adapter_only():
    gw = MockEnforcementGateway()
    r, _ = _det("resource_management:abnormal", gw)
    lc = r["proposed_action"]["lifecycle"]
    assert lc["stage"] == A.AUTHORISED_DRY_RUN and lc["final_action"] == "ALERT" and lc["authorised"] is True
    assert gw.applied == [] and len(gw.dry_run_adapter.log) == 1


def test_verifier_rejection_withdraws_the_proposal_and_keeps_the_candidate_action():
    rt = E.make_runtime("act-c16", "c16", E._estate(S.normal_session("c16", 9)), {},
                        FX.Script({"anomaly_detection": E.ad_wrong_steps()}))
    r = run_case(rt, cops=["runtime_monitoring", "anomaly_detection"])["results"]["anomaly_detection"]
    f, lc = r["finding"], r["proposed_action"]["lifecycle"]
    assert f["status"] == "REJECTED_BY_VERIFIER"
    assert lc["stage"] == A.PROPOSAL_WITHDRAWN
    assert lc["candidate_action"] == f["rejected_candidate"]["proposed_action"]["action"]
    assert lc["substitute"]["source"] == "fallback after withdrawal"
    assert "failed verification" in lc["withdrawn_reason"]


def test_missing_required_evidence_withdraws_the_rule_action():
    r, _ = _det("resource_management:missing")
    lc = r["proposed_action"]["lifecycle"]
    assert r["finding"]["status"] == "MISSING_DEPENDENCY"
    assert lc["stage"] == A.PROPOSAL_WITHDRAWN and lc["substitute"]["source"].startswith("fallback: required")


def test_supervised_lifecycle_states_against_the_mock_gateway():
    by = {d["id"]: d for d in TB.run_lifecycle_demos()}
    assert by["L1"]["stage"] == A.AWAITING_APPROVAL and by["L1"]["mock_applied"] == []
    assert by["L2"]["stage"] == A.EXECUTED and len(by["L2"]["mock_applied"]) == 1
    assert by["L2"]["execution_outcome"]["simulated"] is True
    assert by["L3"]["stage"] == A.FAILED_OR_UNCONFIRMED
    assert by["L3"]["execution_outcome"]["outcome_confirmed"] is False
    assert by["L4"]["stage"] == A.APPROVAL_REJECTED and by["L4"]["mock_applied"] == []
    for d in by.values():
        if d["trace"]:
            assert d["trace"]["authority"]["lifecycle"]["external_effect"] is False


def test_every_case_carries_a_lifecycle_with_a_known_stage():
    for case in DET_CASES:
        r, _ = _det(case["id"])
        lc = r["proposed_action"]["lifecycle"]
        assert lc["stage"] in A.STAGES, case["id"]
        assert lc["final_action"] in BY_KEY[case["cop"]].permitted_actions, case["id"]


def test_live_eval_record_keeps_the_whole_proposal():
    case = next(c for c in LE.CASES if c["id"] == "incident_response:abnormal")
    rec = LE.run_one(case, LE.offline_provider(), None)
    assert rec["proposed_action"]["lifecycle"]["stage"] in A.STAGES
    assert rec["requested_action"] == rec["proposed_action"]["requested_action"]
    assert rec["action_stage"] == rec["proposed_action"]["lifecycle"]["stage"]


def test_the_lifecycle_is_persisted_with_the_decision():
    r, rt = _det("policy_enforcement:abnormal")
    rows = rt.store.select("cop_decisions")
    pe = [x for x in rows if x["cop"] == "policy_enforcement"][0]
    pa = pe["proposed_action"] if isinstance(pe["proposed_action"], dict) else __import__("json").loads(pe["proposed_action"])
    assert pa["lifecycle"]["stage"] == A.REJECTED_BY_AUTHORITY and pa["original_proposal"]["action"] == "BLOCK"
