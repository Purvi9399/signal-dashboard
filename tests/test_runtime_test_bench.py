"""The 54-case test bench: case structure, deterministic execution with no
model call, replay of recorded replies, and the fleet checks."""

import json, os

import pytest

from cops.specs import BY_KEY
from cops.runtime import live_eval as LE
from cops.runtime import test_bench as TB
from cops.runtime.bench_cases import DET_CASES, DET_EXPECTED, FLEET
from cops.runtime.llm import UnavailableProvider
from cops.runtime.profiles import DETERMINISTIC_COPS, LLM_COPS

LIVE = os.path.join(os.path.dirname(__file__), "..", "docs", "cops", "evaluation", "live_results.json")


def test_54_cases_three_per_cop():
    cases = DET_CASES + LE.CASES
    assert len(cases) == 54
    for cop in list(DETERMINISTIC_COPS) + list(LLM_COPS):
        assert sorted(c["kind"] for c in cases if c["cop"] == cop) == ["abnormal", "missing", "normal"]


def test_expected_labels_are_in_each_cops_vocabulary():
    for c in DET_CASES:
        for lab in DET_EXPECTED[c["id"]]:
            assert lab == "INCONCLUSIVE" or lab in BY_KEY[c["cop"]].interpretations, c["id"]
    for sc in FLEET:
        for cop, acc in sc["expect"].items():
            assert set(acc) <= set(BY_KEY[cop].interpretations), (sc["id"], cop)


def test_classify_knows_the_quiet_label_of_deterministic_cops():
    case = next(c for c in DET_CASES if c["id"] == "audit_traceability:abnormal")
    assert LE.classify(case, "COMPLETE") == "missed_finding"
    assert LE.classify(case, "PARTIAL") == "disagree"


@pytest.mark.parametrize("case", DET_CASES, ids=[c["id"] for c in DET_CASES])
def test_deterministic_case_executes_without_a_model_and_within_authority(case):
    o, rt, err, _ = TB.run_investigation(case, UnavailableProvider())
    rec = TB.record(case, o, rt, err, 0, "deterministic")
    assert err is None
    assert rec["dimensions"]["execution"] is True
    assert rec["dimensions"]["authority"] is True
    assert rec["usage"] is None                      # no model call recorded
    assert all(not v["llm_invocations"] for v in o["results"].values())
    assert rec["trace"]["decision"]["label"] == rec["label"]
    assert rec["verdict"] in ("PASS", "FAIL", "INCONCLUSIVE")


def test_missing_evidence_cases_never_end_on_a_quiet_label():
    for case in DET_CASES:
        if case["kind"] != "missing":
            continue
        o, rt, err, _ = TB.run_investigation(case, UnavailableProvider())
        assert o["results"][case["cop"]]["finding"]["label"] != TB.QUIET[case["cop"]], case["id"]


@pytest.mark.skipif(not os.path.exists(LIVE), reason="no recorded live run")
def test_replay_of_a_recorded_live_reply_reproduces_its_label_without_a_call():
    live = json.load(open(LIVE))
    lr = next(r for r in live["results"] if r["id"] == "anomaly_detection:abnormal")
    case = next(c for c in LE.CASES if c["id"] == lr["id"])
    o, rt, err, _ = TB.run_investigation(case, TB.replay_provider(lr), LE.LIVE_BUDGET, prefix="live")
    rec = TB.record(case, o, rt, err, 0, "replay")
    assert rec["label"] == lr["label"]
    assert rec["usage"]["simulated"] is True         # a replay is never a live call


def test_fleet_scenario_offline_stays_dry_run_and_within_authority():
    sc = next(x for x in FLEET if x["id"] == "F2")
    case = {"id": "fleet:F2", "build": sc["build"]}
    o, rt, err, _ = TB.run_investigation(case, LE.offline_provider(), cops=sc["cops"])
    per = TB._per_cop(sc, o["results"])
    checks = {c["check"]: c["passed"] for c in TB.fleet_checks(sc, per, o["consumption"])}
    assert checks["nothing executed: enforcement stays dry-run"]
    assert checks["every final action is within the COP's authority"]
    assert per["policy_enforcement"]["label"] == "VIOLATION"
    assert per["policy_enforcement"]["requested_action"] == "BLOCK"
    assert per["policy_enforcement"]["action"] != "BLOCK"      # dry-run: recorded, not enforced


def test_fleet_check_flags_an_unbacked_attention_finding():
    sc = FLEET[0]
    per = {"anomaly_detection": {"label": "ANOMALOUS", "verified": True, "action": "ALERT",
                                 "execution_status": "NOT_EXECUTED_DRY_RUN",
                                 "trace": {"verification": [{"check": "EV-02", "result": "PASS"},
                                                            {"check": "SC-01", "result": "FAIL"}],
                                           "authority": {"authorised": True}}}}
    c = next(x for x in TB.fleet_checks(sc, per, []) if x["check"].startswith("every attention"))
    assert c["passed"] is False and c["actual"] == ["anomaly_detection"]
