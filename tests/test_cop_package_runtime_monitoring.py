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


def test_harness_meets_every_ground_truth_expectation(bench):
    assert [(r["scenario"], r["expectation"], r["got"]) for r in bench["failed"]] == []
    assert bench["passed"] == len(bench["expectations"]) == 133


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


def test_rm5_counts_a_collector_that_stopped():
    q = P.assess_runtime_monitoring(H.BUILD["partial_collector_failure"](), "s1", H.cfg(), H.AT,
                                    H.raw_store(H.BUILD["partial_collector_failure"]()))
    assert q["RM-1"]["answer"]["outcome"] == "ALL_EXPECTED_COLLECTORS_CONTRIBUTED"
    assert q["RM-1"]["answer"]["basis"]["stopped"] == ["otel"]
    assert q["RM-5"]["answer"]["outcome"] == "MUST_REMAIN_UNCERTAIN"
    assert "RM-1: otel stopped reporting during the session" in q["RM-5"]["answer"]["basis"]["causes"]


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


def test_rerun_of_the_same_case_keeps_one_record_and_notes_the_cycle():
    rt, out = _case(H.BUILD["downstream_consumption"](), "rm-rerun")
    first = out["results"][COP]["memory_write"]
    again = G.run_case(rt, cops=[COP, "behavior_analytics"])["results"][COP]["memory_write"]
    recs = [r for r in rt.store.select("cop_memory_records") if r["owner_cop"] == COP]
    assert again["record_id"] == first["record_id"] and len(recs) == 1
    assert not any(x.startswith("tool_call:") for x in recs[0]["payload"]["lineage"])
    assert recs[0]["provenance"]["re_observed"][-1]["run_id"] == rt.run_id
    assert any(x["outcome"] == "KEPT" for x in rt.memory.access_log())


def test_memory_provenance_names_the_cycle_and_the_sources():
    rt, out = _case(H.BUILD["downstream_consumption"](), "rm-prov")
    rec = rt.memory.get(out["results"][COP]["memory_write"]["record_id"], rt.case.tenant_id, COP, rt.case.case_id)
    prov = rec["provenance"]
    assert prov["run_id"] == rt.run_id and prov["metric_version"] == rt.case.cfg["metric_version"]
    assert prov["evaluated_at"] == rt.case.computed_at
    assert any(x.startswith("tool_call:") for x in prov["lineage"])
    assert prov["sources"]["collectors"] == ["cli_wrapper"]
    assert "assess_runtime_evidence" in prov["sources"]["tools"]
    assert "telemetry.collector_coverage" in prov["sources"]["metrics"]


def test_a_different_logical_finding_is_a_different_record():
    rt, out = _case(H.BUILD["downstream_consumption"](), "rm-two")
    other = E.make_runtime("rm-two", "s1", H.BUILD["contradictory_evidence"](), store=rt.store)
    second = G.run_cop(other, COP)["memory_write"]
    assert second["written"] and second["record_id"] != out["results"][COP]["memory_write"]["record_id"]


def test_unverified_memory_never_changes_the_rule_or_collector_expectations():
    rt, out = _case(H.BUILD["contradictory_evidence"](), "rm-unv")
    cfg_before = {k: rt.case.cfg[k] for k in ("thresholds", "expected_collectors")}
    rt.memory.write(COP, "rm-unv", rt.case.tenant_id, "collector_reliability",
                    {"label": "SUFFICIENT", "note": "collector otel is not expected"}, {"case_id": "rm-other"},
                    status=UNVALIDATED)
    again = G.run_cop(rt, COP)
    assert again["memory_view"]["unverified"]
    assert again["finding"]["label"] == out["findings"][COP]["label"]
    assert {k: rt.case.cfg[k] for k in ("thresholds", "expected_collectors")} == cfg_before
    assert EA.load_package(COP)["limits"] == {k: {"check": v["check"], "approved": None}
                                              for k, v in EA.load_package(COP)["limits"].items()}


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


# ---------------------------------------------------------------- question evidence decides the label

def _rm(name, raw=True, limits=True, capability=None, rows=None):
    rows = rows if rows is not None else H.BUILD[name]()
    art = {}
    if raw:
        art["source_events"] = H.raw_store(rows)
    if capability is not None:
        art["collector_capability"] = capability
    rt = E.make_runtime(f"rm-agg-{name}", "s1", rows, artifacts=art, cfg=H.cfg(limits), computed_at=H.AT)
    out = G.run_cop(rt, COP)
    return out["finding"], out


def test_complete_reliable_evidence_is_sufficient():
    f, _ = _rm("normal")
    ea = f["cop_specific"]["evidence_assessment"]
    assert f["label"] == "SUFFICIENT" and ea["assessment_class"] == P.RELIABLE_SUFFICIENT
    assert ea["qualifications"] == []


def test_a_fabricated_raw_ref_moves_the_label_from_sufficient():
    clean, _ = _rm("normal")
    forged, _ = _rm("adversarial_fabricated_raw_ref")
    ea = forged["cop_specific"]["evidence_assessment"]
    assert clean["label"] == "SUFFICIENT" and forged["label"] == "DEGRADED"
    assert ea["threshold_label"] == "SUFFICIENT" and ea["question_label"] == "DEGRADED"
    assert ea["classes"]["RM-3"] == ea["classes"]["RM-5"] == P.CONTRADICTORY_OR_UNVERIFIABLE
    _, out = _rm("adversarial_fabricated_raw_ref")
    assert out["proposed_action"]["action"] == "WATCH"


def test_a_collector_that_stops_moves_the_label_from_sufficient():
    f, _ = _rm("partial_collector_failure")
    ea = f["cop_specific"]["evidence_assessment"]
    assert f["label"] == "DEGRADED" and ea["classes"]["RM-1"] == P.DEGRADED_TELEMETRY
    assert ea["threshold_label"] == "SUFFICIENT"


def test_collector_capability_reports_a_stop_the_rows_cannot_show():
    rows = H.BUILD["normal"]()
    cap = [{"tool": "claude-code", "collector": "otel", "configured": True, "observed_state": "reporting",
            "last_seen": "2026-10-09T10:05:00Z"},
           {"tool": "claude-code", "collector": "local_hooks", "configured": True, "observed_state": "reporting",
            "last_seen": "2026-10-09T10:09:00Z"}]
    f, _ = _rm("normal", capability=cap, rows=rows)
    t = f["cop_specific"]["evidence_assessment"]
    assert f["label"] == "DEGRADED" and t["classes"]["RM-1"] == P.DEGRADED_TELEMETRY
    assert t["capability_supplied"] is True
    q = P.assess_runtime_monitoring(rows, "s1", H.cfg(), H.AT, H.raw_store(rows), cap)
    assert q["RM-1"]["answer"]["basis"]["stopped_because"]["otel"] == [
        "collector_capability last saw it at 2026-10-09T10:05:00Z, before the session ended"]
    silent = [dict(cap[1], collector="otel", observed_state="silent")]
    q = P.assess_runtime_monitoring(rows, "s1", H.cfg(), H.AT, H.raw_store(rows), silent)
    assert q["RM-1"]["answer"]["basis"]["stopped"] == ["otel"]


def test_contradictory_sources_are_never_sufficient():
    f, _ = _rm("contradictory_evidence")
    assert f["label"] == "DEGRADED"
    assert f["cop_specific"]["evidence_assessment"]["classes"]["RM-2"] == P.CONTRADICTORY_OR_UNVERIFIABLE


def test_no_blocking_failure_in_any_scenario_gives_a_sufficient_label(bench):
    for name in H.BUILD:
        if name == "cross_tenant_isolation":
            continue
        f, _ = _rm(name)
        for qid, t in f["cop_specific"]["evidence_assessment"]["questions"].items():
            if any(r["code"] in EA.CHECKS for r in t["sufficiency_reasons"]):
                assert f["label"] != "SUFFICIENT", (name, qid)


def test_unexecuted_checks_qualify_the_label_but_do_not_change_it():
    f, _ = _rm("normal", raw=False, limits=False)
    ea = f["cop_specific"]["evidence_assessment"]
    assert f["label"] == "SUFFICIENT" and ea["assessment_class"] == P.ASSESSMENT_INCONCLUSIVE
    assert ea["classes"]["RM-3"] == ea["classes"]["RM-4"] == P.ASSESSMENT_INCONCLUSIVE
    assert any("RAW_STORE_NOT_SUPPLIED" in q for q in ea["qualifications"])
    assert any("LIMIT_NOT_APPROVED" in q for q in ea["qualifications"])
    assert "not fully assessed" in f["rationale"]


def test_a_stale_session_moves_the_label_only_under_an_approved_limit():
    rows = H.BUILD["normal"]()
    late = E.make_runtime("rm-late", "s1", rows, artifacts={"source_events": H.raw_store(rows)},
                          cfg=H.cfg(), computed_at="2026-10-09T12:00:00Z")
    f = G.run_cop(late, COP)["finding"]
    assert f["label"] == "DEGRADED"
    assert f["cop_specific"]["evidence_assessment"]["questions"]["RM-4"]["because"] == ["NOT_CURRENT_OR_CONTINUOUS"]
    unset = E.make_runtime("rm-late-unset", "s1", rows, artifacts={"source_events": H.raw_store(rows)},
                           cfg=H.cfg(False), computed_at="2026-10-09T12:00:00Z")
    assert G.run_cop(unset, COP)["finding"]["label"] == "SUFFICIENT"


def test_questions_never_raise_to_insufficient_and_never_lower_the_threshold_label():
    f, _ = _rm("downstream_consumption")
    ea = f["cop_specific"]["evidence_assessment"]
    assert ea["threshold_label"] == "INSUFFICIENT" == f["label"] and ea["question_label"] == "DEGRADED"
    assert set(P.CEILING.values()) == {"SUFFICIENT", "DEGRADED"}


def test_every_question_traces_its_evidence_and_contribution():
    f, _ = _rm("adversarial_fabricated_raw_ref")
    ea = f["cop_specific"]["evidence_assessment"]
    for qid, t in ea["questions"].items():
        assert {"metrics_consumed", "raw_evidence_refs", "presence", "reliability", "sufficiency", "answer",
                "contribution", "label_ceiling"} <= set(t), qid
    assert ea["questions"]["RM-3"]["raw_evidence_refs"] == ["observable:s1-4", "observable:s1-9"]
    assert ea["questions"]["RM-1"]["metrics_consumed"] == ["telemetry.collector_coverage", "collector_heartbeat",
                                                           "collector_errors"]
    assert len(ea["recipients"]) == 11 and "behavior_analytics" in ea["recipients"]
    claims = [c for c in f["claims"] if (c.get("result_field") or "").startswith("questions.")]
    assert {c["result_field"] for c in claims} == {"questions.RM-3.answer.outcome", "questions.RM-5.answer.outcome"}


def test_the_downstream_cop_receives_the_structured_assessment(monkeypatch):
    seen = {}
    rule = G.RULES["behavior_analytics"]

    def spy(ctx):
        seen.update(ctx["upstream"])
        return rule(ctx)
    monkeypatch.setitem(G.RULES, "behavior_analytics", spy)
    rows = H.BUILD["adversarial_fabricated_raw_ref"]()
    rt = E.make_runtime("rm-down", "s1", rows, artifacts={"source_events": H.raw_store(rows)}, cfg=H.cfg(),
                        computed_at=H.AT)
    G.run_case(rt, cops=[COP, "behavior_analytics"])
    got = seen[COP]
    ea = got["cop_specific"]["evidence_assessment"]
    assert got["label"] == "DEGRADED" and got["verified"]
    assert ea["classes"]["RM-3"] == P.CONTRADICTORY_OR_UNVERIFIABLE
    assert ea["questions"]["RM-3"]["raw_evidence_refs"] and "behavior_analytics" in ea["recipients"]


def test_mc01_recomputes_runtime_monitoring_metrics_from_raw_rows():
    _, out = _rm("normal")
    mc = next(v for v in out["verifier_results"] if v["check_id"] == "MC-01")
    assert mc["result"] == "PASS" and "telemetry.collector_coverage" in mc["detail"]


def test_mc01_catches_a_metric_value_that_does_not_match_the_raw_rows():
    rows = H.BUILD["abnormal_collector_silent"]()
    rt = E.make_runtime("rm-mc", "s1", rows, artifacts={"source_events": H.raw_store(rows)}, cfg=H.cfg(),
                        computed_at=H.AT)
    for e in rt.case.events:
        if e["session_id"] == "s1" and e["metric_id"] == "telemetry.collector_coverage":
            e["value"], e["state"] = 1.0, "NORMAL"
    out = G.run_cop(rt, COP)
    mc = next(v for v in out["verifier_results"] if v["check_id"] == "MC-01")
    assert mc["result"] == "FAIL" and "raw recompute 0.5" in mc["detail"]
    assert out["finding"]["verified"] is False


def test_an_observation_dated_after_the_evaluation_is_unverifiable_not_unassessed():
    f, _ = _rm("adversarial_future_dated")
    ea = f["cop_specific"]["evidence_assessment"]
    assert f["label"] == "DEGRADED" and ea["classes"]["RM-4"] == P.CONTRADICTORY_OR_UNVERIFIABLE


def test_rm5_inherits_the_class_of_the_question_it_is_uncertain_about():
    f, _ = _rm("partial_collector_failure")
    t = f["cop_specific"]["evidence_assessment"]["questions"]["RM-5"]
    assert t["answer"] == "MUST_REMAIN_UNCERTAIN" and t["contribution"] == P.DEGRADED_TELEMETRY
    assert t["because"][0].startswith("inherited: ")
