"""Runtime Monitoring package: contract validation, harness against ground truth,
interpretation, memory and downstream influence. Synthetic data, no model call."""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "collector"))

import pytest
import yaml

from cops.packages.runtime_monitoring import harness as H
from cops.runtime import cop_package as P, evaluation as E, evidence_assessment as EA, graph as G
from cops.runtime.contracts import UNVALIDATED, VALIDATED
from cops.runtime.memory import MemoryAccessDenied
from cops.specs import BY_KEY

COP = "runtime_monitoring"
PKG = os.path.join(os.path.dirname(__file__), "..", "collector", "cops", "packages", COP)

# Ground-truth expectations the current implementation does not meet, and why.
# The harness must miss exactly these: a new miss, or a gap that closes, fails the test.
KNOWN_GAPS = {
    ("unreliable_provenance", "cop_label"):
        "rules.py label ignores provenance; the question assessment is not wired into the label",
    ("partial_collector_failure", "cop_label"):
        "a collector that stops mid-session still counts as present; collector_heartbeat is not built",
    ("partial_collector_failure", "detects_mid_session_collector_stop"):
        "no metric records when a collector last reported; collector_heartbeat is not built",
    ("adversarial_fabricated_raw_ref", "RM-3.sufficiency"):
        "provenance checks that raw_ref is present, not that the raw event exists",
    ("adversarial_fabricated_raw_ref", "RM-3.outcome"): "as above",
    ("adversarial_fabricated_raw_ref", "RM-5.sufficiency"): "as above",
    ("adversarial_fabricated_raw_ref", "RM-5.outcome"): "as above",
}


@pytest.fixture(scope="module")
def bench():
    return H.run_all()


# ---------------------------------------------------------------- contract

def test_metrics_yaml_is_valid():
    assert P.validate(COP) == []


def test_assessment_questions_come_from_the_package_not_a_copy():
    pkg = EA.load_package(COP)
    assert list(EA.QUESTIONS[COP]) == list(pkg["questions"]) == ["RM-1", "RM-2", "RM-3", "RM-4", "RM-5"]
    for qid, q in pkg["questions"].items():
        assert EA.QUESTIONS[COP][qid]["requires"] == [(r["metric"], r["kind"]) for r in q["requires"]]


def test_validator_catches_a_broken_contract(monkeypatch):
    pkg = EA.load_package(COP)
    pkg["questions"]["RM-1"]["requires"].append({"metric": "collector_heartbeat", "kind": "REQUIRED"})
    pkg["questions"]["RM-2"]["verified"].append("NOT_A_CHECK")
    pkg["mapping"].pop("telemetry.unknown_share")
    monkeypatch.setattr(EA, "load_package", lambda cop: pkg)
    probs = P.validate(COP)
    assert "RM-1: collector_heartbeat is not built and is not OPTIONAL" in probs
    assert "RM-2: unknown check NOT_A_CHECK" in probs
    assert any("assigns telemetry.unknown_share" in p for p in probs)


def test_package_agrees_with_the_existing_spec():
    spec, pkg = BY_KEY[COP], EA.load_package(COP)
    assert set(spec.metric_keys) <= set(pkg["mapping"])
    assert set(spec.missing_metrics) <= set(pkg["mapping"])
    assert pkg["limits"] and all(v["approved"] is None for v in pkg["limits"].values())
    assert "evidence_limits" not in EA.signal_metrics.load_config()
    assert {op["by"] for op in pkg["operations"].values()} == {"deterministic"}


def test_every_package_file_exists():
    for f in ("SPEC.md", "METRICS.yaml", "INTERPRETATION.md", "MEMORY.md", "CONNECTIONS.md", "DECISIONS.md",
              "GROUND_TRUTH.yaml", "harness.py"):
        assert os.path.exists(os.path.join(PKG, f)), f
    skill = os.path.join(os.path.dirname(__file__), "..", ".claude", "skills", "cops-runtime-monitoring", "SKILL.md")
    assert os.path.exists(skill)


# ---------------------------------------------------------------- harness against ground truth

def test_ground_truth_names_only_known_scenarios_and_checks():
    truth = H.truth()
    assert set(truth) == set(H.BUILD)
    for t in truth.values():
        assert set(t.get("checks", [])) <= set(H.CHECKS)
    with open(os.path.join(PKG, "GROUND_TRUTH.yaml")) as f:
        assert yaml.safe_load(f)["contract"] == "rm_ground_truth/1"


def test_harness_meets_ground_truth_except_the_listed_gaps(bench):
    missed = {(r["scenario"], r["expectation"]) for r in bench["failed"]}
    assert missed == set(KNOWN_GAPS), sorted(missed ^ set(KNOWN_GAPS))
    assert bench["passed"] == len(bench["expectations"]) - len(KNOWN_GAPS)


def test_every_answer_is_in_the_contract_and_every_inconclusive_has_a_reason(bench):
    pkg = EA.load_package(COP)["questions"]
    for name, res in bench["results"].items():
        for key in ("questions", "s2", "without_text"):
            for qid, q in (res.get(key) or {}).items():
                ans = q["answer"]
                assert ans["outcome"] in pkg[qid]["outcomes"] + [P.INCONCLUSIVE], (name, qid)
                assert (ans["outcome"] == P.INCONCLUSIVE) == (ans["sufficiency"] != "SUFFICIENT"), (name, qid)
                if ans["outcome"] == P.INCONCLUSIVE:
                    assert ans["because"] and all(r["code"] and r["detail"] for r in ans["because"]), (name, qid)


def test_every_outcome_in_the_contract_is_reached_by_some_scenario(bench):
    pkg = EA.load_package(COP)["questions"]
    seen = {(qid, q["answer"]["outcome"]) for r in bench["results"].values()
            for key in ("questions", "s2") for qid, q in (r.get(key) or {}).items()}
    for qid, q in pkg.items():
        for o in q["outcomes"] + [P.INCONCLUSIVE]:
            assert (qid, o) in seen, (qid, o)


# ---------------------------------------------------------------- interpretation

def test_rm5_counts_unanswered_and_negative_answers_as_uncertainty(bench):
    causes = bench["results"]["abnormal_collector_silent"]["questions"]["RM-5"]["answer"]["basis"]["causes"]
    assert causes == ["RM-1: EXPECTED_COLLECTORS_MISSING", "RM-2 could not be answered (INSUFFICIENT)",
                      "RM-4: NOT_CURRENT_OR_CONTINUOUS"]
    hist = bench["results"]["insufficient_history"]["questions"]["RM-5"]["answer"]["basis"]
    assert hist["unknown_share_state"] != "NORMAL" and len(hist["causes"]) == 1


def test_observation_does_not_change_the_cop_finding(bench):
    res = bench["results"]["contradictory_evidence"]
    assert res["questions"]["RM-3"]["answer"]["outcome"] == P.INCONCLUSIVE
    assert res["cop"][COP]["label"] == "DEGRADED"
    assert "questions" not in (res["cop_result"]["finding"].get("cop_specific") or {})


# ---------------------------------------------------------------- decision influence downstream

def _case(rows, cid):
    rt = E.make_runtime(cid, "s1", rows)
    return rt, G.run_case(rt, cops=[COP, "behavior_analytics"])


def test_rm_reading_decides_whether_a_downstream_quiet_label_may_stand():
    _, thin = _case(H.BUILD["downstream_consumption"](), "rm-infl-thin")
    _, full = _case(H.BUILD["normal"](), "rm-infl-full")
    assert thin["findings"][COP]["label"] == "INSUFFICIENT" and thin["findings"][COP]["verified"]
    assert thin["findings"]["behavior_analytics"]["label"] == "INCONCLUSIVE"
    assert full["findings"][COP]["label"] == "SUFFICIENT"
    assert full["findings"]["behavior_analytics"]["label"] == BY_KEY["behavior_analytics"].interpretations[0]


def test_rm_label_sets_its_proposed_action(bench):
    acts = {n: (bench["results"][n]["cop_result"].get("proposed_action") or {}).get("action")
            for n in ("normal", "contradictory_evidence", "downstream_consumption")}
    assert acts == {"normal": "NONE", "contradictory_evidence": "WATCH", "downstream_consumption": "ALERT"}
    for n, r in bench["results"].items():
        if "cop" in r:
            assert r["cop"][COP]["label"] in BY_KEY[COP].interpretations
            assert (r["cop_result"].get("proposed_action") or {}).get("action") in BY_KEY[COP].permitted_actions


# ---------------------------------------------------------------- memory

def test_memory_write_rules():
    rt, out = _case(H.BUILD["downstream_consumption"](), "rm-mem")
    mw = out["results"][COP]["memory_write"]
    assert mw["written"] and mw["record_type"] == "collector_reliability" and mw["status"] == "VERIFIED"
    rec = rt.memory.get(mw["record_id"], rt.case.tenant_id, COP, rt.case.case_id)
    assert {"case_id", "finding_id", "session_id", "tenant_id", "verified", "synthetic"} <= set(rec["provenance"])
    _, quiet = _case(H.BUILD["normal"](), "rm-mem-quiet")
    assert quiet["results"][COP]["memory_write"]["written"] is False


def test_rerun_of_the_same_case_adds_a_second_record_known_defect():
    # The payload's lineage carries this run's tool_call ids, so the record id changes on every
    # re-run and the "re-run does not overwrite" path (KEPT) is never reached for Runtime
    # Monitoring. Shared graph code; listed in DECISIONS.md, not fixed here.
    rt, out = _case(H.BUILD["downstream_consumption"](), "rm-rerun")
    first = out["results"][COP]["memory_write"]
    again = G.run_case(rt, cops=[COP, "behavior_analytics"])["results"][COP]["memory_write"]
    recs = [r for r in rt.store.select("cop_memory_records") if r["owner_cop"] == COP]
    assert again["record_id"] != first["record_id"] and len(recs) == 2
    assert recs[0]["payload"]["lineage"][:2] == recs[1]["payload"]["lineage"][:2]
    assert recs[0]["payload"]["lineage"][2] != recs[1]["payload"]["lineage"][2]


def test_rm_cannot_validate_its_own_memory_or_write_another_type():
    rt, out = _case(H.BUILD["downstream_consumption"](), "rm-mem2")
    rid = out["results"][COP]["memory_write"]["record_id"]
    with pytest.raises(MemoryAccessDenied):
        rt.memory.record_feedback(rid, rt.case.tenant_id, VALIDATED, "cop:runtime_monitoring")
    with pytest.raises(MemoryAccessDenied):
        rt.memory.write(COP, rt.case.case_id, rt.case.tenant_id, "collector_reliability", {"x": 1},
                        {"case_id": rt.case.case_id}, status=VALIDATED)
    with pytest.raises(MemoryAccessDenied):
        rt.memory.write(COP, rt.case.case_id, rt.case.tenant_id, "anomaly_observation", {"x": 1},
                        {"case_id": rt.case.case_id}, status=UNVALIDATED)
    assert rt.memory.record_feedback(rid, rt.case.tenant_id, VALIDATED, "reviewer@tenant-a.example")[
        "validation_status"] == VALIDATED


def test_rm_reads_memory_but_its_decision_does_not_use_it():
    rt, out = _case(H.BUILD["downstream_consumption"](), "rm-mem3")
    first = out["findings"][COP]
    again = G.run_cop(rt, COP)
    assert again["memory_view"]["available"] and again["memory_view"]["verified_pending"]
    assert {k: again["finding"][k] for k in ("label", "confidence", "rationale")} == \
        {k: first[k] for k in ("label", "confidence", "rationale")}
