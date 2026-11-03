"""
Regression tests for the ten cases that did not pass on 2026-11-03 and fleet F2
(docs/cops/COPS_INCONCLUSIVE_ROOT_CAUSE_REPORT.md): malformed claim citations,
value mismatches, upstream disclosures, missing dependencies, evidence
sufficiency, the bounded recovery path, and the bench as a whole.

No test calls a model: scripted replies are simulated, and bench replays use the
recorded real replies.
"""

import json, os, sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "collector"))

from cops.runtime import evaluation as E, fixtures as FX, synthetic as S, live_eval as LE, test_bench as TB
from cops.runtime import bench_cases as BC
from cops.runtime.diagnosis import diagnose, CODES
from cops.runtime.graph import run_cop
from cops.runtime.llm import FixtureProvider, LLMBudget
from cops.runtime.requirements import (precheck, evidence_profile, RETRIEVED_VALIDATED, MISSING_UNAVAILABLE,
                                       ITEM_NOT_APPLICABLE, PRESENT_UNVERIFIED, MISSING_RETRIEVABLE,
                                       SUFFICIENCY_STATES)
from cops.runtime.tools import TOOLS
from cops.runtime.verifiers import verify_finding, CITATION_MALFORMED, VALUE_MISMATCH, UPSTREAM_UNVERIFIED
from cops.specs import BY_KEY, CONSUMES

DOCS = os.path.join(os.path.dirname(__file__), "..", "docs", "cops", "evaluation")
QUIET = {k: s.interpretations[0] for k, s in BY_KEY.items()}


def _case(cid):
    return next(c for c in LE.CASES + BC.DET_CASES if c["id"] == cid)


def _rt(cid, provider=None, sid=None, budget=None):
    c = _case(cid)
    sid = sid or cid.replace(":", "-").replace("_", "")[:20]
    b = c["build"](sid)
    return E.make_runtime(f"rc-{sid}", sid, b["rows"], b["art"], provider=provider, budget=budget,
                          store=b["store"], decision_store=b["store"] if b["decision_store"] else None)


def scripted(plan_fn, *assess_fns):
    """plan, then each assess/reassess step in order; records the purposes asked."""
    seen = []

    def script(cop, purpose, p, n):
        seen.append((purpose, p))
        if purpose == "plan":
            return plan_fn(p)
        k = sum(1 for x, _ in seen if x != "plan") - 1
        return assess_fns[min(k, len(assess_fns) - 1)](p)
    prov = FixtureProvider(script)
    prov.seen = seen
    return prov


def _sc01(results):
    return next(r for r in results if r["check_id"] == "SC-01")


# ====================================================================== SC-01: claims and citations

def _verify(rt, cop, claims, label, upstream=None, picture=None, calls=None):
    from cops.runtime.evidence import assess
    spec = BY_KEY[cop]
    pic = picture or assess(rt.case.case_events(spec.metric_keys), spec.metric_keys, rt.case.session_id)
    f = {"label": label, "status": "SUBSTANTIVE", "confidence": 0.6, "claims": claims, "origin": "llm_hypothesis",
         "missing_evidence": [], "upstream_used": []}
    return verify_finding({"finding": f, "spec": spec, "picture": pic, "events": rt.case.case_events(spec.metric_keys),
                           "case": rt.case, "tool_calls": calls or {}, "upstream": upstream or {},
                           "memory": None, "cop": cop, "baseline": None})


def test_sc01_upstream_disclosure_of_an_inconclusive_finding_is_not_a_failure_and_not_support():
    rt = _rt("governance_intelligence:normal")
    up = {"recovery_resilience": {"label": "INCONCLUSIVE", "status": "MISSING_DEPENDENCY", "verified": False,
                                  "finding_id": "f-rr"}}
    claim = {"statement": "recovery_resilience could not decide", "kind": "upstream_finding",
             "upstream_cop": "recovery_resilience", "upstream_label": "INCONCLUSIVE", "evidence_refs": []}
    sc = _sc01(_verify(rt, "governance_intelligence", [claim], "NO_PATTERN", up))
    assert sc["result"] == "NOT_RUN" and sc["failures"] == []        # no failure, and no support counted


def test_sc01_still_rejects_an_unverified_substantive_upstream_used_as_support():
    rt = _rt("risk_mitigation:normal")
    up = {"policy_enforcement": {"label": "VIOLATION", "status": "SUBSTANTIVE", "verified": False, "finding_id": "f"}}
    claim = {"statement": "PE found a violation", "kind": "upstream_finding", "upstream_cop": "policy_enforcement",
             "upstream_label": "VIOLATION", "evidence_refs": []}
    sc = _sc01(_verify(rt, "risk_mitigation", [claim], "EMERGING", up))
    assert sc["result"] == "FAIL" and sc["failures"][0]["code"] == UPSTREAM_UNVERIFIED
    assert sc["failures"][0]["recoverable"] is False


def test_sc01_rejects_a_disclosure_that_misstates_the_upstream_label():
    rt = _rt("governance_intelligence:normal")
    up = {"recovery_resilience": {"label": "INCONCLUSIVE", "status": "INCONCLUSIVE", "verified": False,
                                  "finding_id": "f"}}
    claim = {"statement": "RR recovered", "kind": "upstream_finding", "upstream_cop": "recovery_resilience",
             "upstream_label": "RECOVERED", "evidence_refs": []}
    sc = _sc01(_verify(rt, "governance_intelligence", [claim], "NO_PATTERN", up))
    assert sc["result"] == "FAIL" and sc["failures"][0]["code"] == "UPSTREAM_MISMATCH"


def test_sc01_metric_named_only_in_evidence_refs_is_a_malformed_citation_and_is_not_filled_in():
    rt = _rt("workload_optimization:abnormal")
    claim = {"statement": "tokens near peers", "kind": "metric_value", "metric_id": None, "asserted_value": 1.2,
             "evidence_refs": ["metric:insights.token_outlier"]}
    before = dict(claim)
    sc = _sc01(_verify(rt, "workload_optimization", [claim], "REDUNDANT"))
    f = sc["failures"][0]
    assert sc["result"] == "FAIL" and f["code"] == CITATION_MALFORMED and f["recoverable"] is True
    assert "metric:insights.token_outlier" in f["message"] and "not a citation" in f["message"]
    assert claim == before                                            # nothing rewritten


def test_sc01_uncited_metric_state_claim_names_exactly_what_is_missing():
    rt = _rt("governance_architecture:abnormal")
    claim = {"statement": "unknown_share is 0.0", "kind": "metric_state", "metric_id": None,
             "asserted_state": None, "asserted_value": 0.0, "evidence_refs": []}
    f = _sc01(_verify(rt, "governance_architecture", [claim], "GAP"))["failures"][0]
    assert f["code"] == CITATION_MALFORMED and "cites nothing" in f["message"] and f["claim"] == 1


def test_wo_steps_versus_self_transitions_are_separate_fields_and_the_mismatch_is_recorded_exactly():
    rt = _rt("workflow_orchestration:abnormal")
    res = TOOLS["analyse_execution_transitions"][0](rt.case, {}, {})
    run = next(p for p in res["patterns"] if "longest_run" in p)["longest_run"]
    assert (run["length"], run["steps"], run["self_transitions"]) == (5, 5, 4)
    call = {"tool_call_id": "t1", "tool": "analyse_execution_transitions", "status": "OK", "result": res,
            "cop": "workflow_orchestration", "case_id": rt.case.case_id}
    claim = {"statement": "TEST>TEST self-transitions occurred 4 times", "kind": "tool_observation",
             "tool_call_id": "t1", "result_field": "patterns.0.longest_run.length", "asserted_value": 4,
             "evidence_refs": []}
    f = _sc01(_verify(rt, "workflow_orchestration", [claim], "STALLED", calls={"t1": call}))["failures"][0]
    assert (f["code"], f["asserted"], f["observed"]) == (VALUE_MISMATCH, 4, 5)
    assert f["field"] == "analyse_execution_transitions.patterns.0.longest_run.length"
    ok = {**claim, "result_field": "patterns.0.longest_run.self_transitions"}
    assert _sc01(_verify(rt, "workflow_orchestration", [ok], "STALLED", calls={"t1": call}))["result"] == "PASS"


# ====================================================================== evidence sufficiency

def test_every_cop_has_an_evidence_profile_with_upstream_roles():
    for k in BY_KEY:
        p = evidence_profile(k)
        assert p["quiet_label"] == QUIET[k]
        assert [u["cop"] for u in p["upstream"]] == list(CONSUMES[k])
        assert all(u["must_be_verified"] for u in p["upstream"])


def test_precheck_keeps_missing_not_applicable_unverified_and_retrieved_apart():
    rt = _rt("classification_tagging:missing")
    from cops.runtime.evidence import assess
    spec = BY_KEY["classification_tagging"]
    pic = assess(rt.case.case_events(spec.metric_keys), spec.metric_keys, rt.case.session_id)
    up = {"anomaly_detection": {"label": "INCONCLUSIVE", "status": "INCONCLUSIVE", "verified": False}}
    s = precheck("classification_tagging", rt.case, pic, up, [])
    st = {x["item"]: x["state"] for x in s["items"]}
    assert st["artifact:taxonomy"] == MISSING_UNAVAILABLE and s["blocks_all_labels"]
    assert st["finding:anomaly_detection"] == PRESENT_UNVERIFIED
    assert st["finding:agent_lifecycle_management"] == MISSING_UNAVAILABLE
    assert set(st.values()) <= set(SUFFICIENCY_STATES)
    pe = precheck("policy_enforcement", _rt("policy_enforcement:normal").case, {"rows": []}, {}, [])
    ent = next(x for x in pe["items"] if x["item"] == "artifact:entitlements")
    assert ent["state"] == ITEM_NOT_APPLICABLE                      # no MCP server used
    pol = next(x for x in pe["items"] if x["item"] == "artifact:policy_store")
    assert pol["state"] == MISSING_RETRIEVABLE                      # present, not yet read
    pe2 = precheck("policy_enforcement", _rt("policy_enforcement:normal").case, {"rows": []}, {},
                   [{"tool": "evaluate_session_operations", "status": "OK"}])
    assert next(x for x in pe2["items"] if x["item"] == "artifact:policy_store")["state"] == RETRIEVED_VALIDATED


def test_classification_without_a_taxonomy_stops_before_any_model_call():
    calls = []
    prov = FixtureProvider(lambda *a: calls.append(a) or FX.inconclusive("x", []))
    out = run_cop(_rt("classification_tagging:missing", prov), "classification_tagging")
    f = out["finding"]
    assert calls == [] and out["llm_invocations"] == []
    assert f["label"] == "INCONCLUSIVE" and f["status"] == "MISSING_DEPENDENCY"
    assert {"MISSING_EVIDENCE", "PRECHECK_STOPPED"} <= set(f["reason_codes"])


@pytest.mark.parametrize("cid", ["behavior_analytics:missing", "interaction_discovery:missing",
                                 "audit_traceability:missing"])
def test_deterministic_quiet_label_on_insufficient_telemetry_is_withheld_by_the_cop_not_the_verifier(cid):
    """Before the fixes DS-01 had to reject these; now the COP holds back itself, and
    DS-01 still runs and agrees."""
    c = _case(cid)
    o, rt, err, _ = TB.run_investigation(c, None or E.UnavailableProvider() if hasattr(E, "UnavailableProvider")
                                         else __import__("cops.runtime.llm", fromlist=["x"]).UnavailableProvider())
    f = o["results"][c["cop"]]["finding"]
    ds = next(r for r in o["results"][c["cop"]]["verifier_results"] if r["check_id"] == "DS-01")
    assert f["label"] == "INCONCLUSIVE" and f["status"] == "INCONCLUSIVE"
    assert f["reason_codes"] == ["MISSING_EVIDENCE"] and f.get("gated_label") == QUIET[c["cop"]]
    assert ds["result"] != "FAIL"


# ====================================================================== rules

def test_workload_rule_reads_redundant_from_unchanged_repeats_and_efficient_stays_efficient():
    from cops.runtime.llm import UnavailableProvider
    for cid, want in (("workload_optimization:abnormal", "REDUNDANT"), ("workload_optimization:normal", None)):
        rt = _rt(cid, UnavailableProvider())
        out = run_cop(rt, "workload_optimization")
        base = out["finding"]["baseline"]["label"]
        if want:
            assert base == want
        else:
            assert base != "REDUNDANT"
    rep = TOOLS["inspect_repeated_operations"][0](_rt("workload_optimization:abnormal").case, {}, {})
    assert (rep["unchanged_repeat_reads"], rep["unchanged_repeat_commands"]) == (6, 3)


def test_audit_trace_with_unattributed_disk_changes_is_broken_not_partial():
    for cid, want in (("audit_traceability:abnormal", "BROKEN_TRACE"), ("audit_traceability:normal", "COMPLETE")):
        from cops.runtime.llm import UnavailableProvider
        o, rt, err, _ = TB.run_investigation(_case(cid), UnavailableProvider())
        assert o["results"]["audit_traceability"]["finding"]["label"] == want


def test_event_metadata_distinguishes_examined_and_empty_from_unavailable():
    r = TOOLS["inspect_event_metadata"][0](_rt("classification_tagging:normal").case, {}, {})
    assert r["rows"] == [] and r["scanned_rows"] > 0 and r["metadata_available"] is True


def test_coverage_matrix_separates_the_five_levels_and_case_from_estate_gaps():
    n = TOOLS["query_coverage_matrix"][0](_rt("governance_architecture:normal").case, {}, {})
    a = TOOLS["query_coverage_matrix"][0](_rt("governance_architecture:abnormal").case, {}, {})
    assert n["case_level_gaps"] == [] and set(n["estate_level_gaps"]) == {"insights.no_oversight", "insights.lockfile"}
    assert "browser_navigation" in a["case_level_gaps"]
    lvl = next(x for x in n["coverage_levels"] if x["item"] == "insights.no_oversight")
    assert lvl["metric_reader"] is True and lvl["cop_consumer"] == [] and lvl["executable_condition"] == []
    assert set(lvl) >= {"observed_in_case", "metric_reader", "cop_consumer", "executable_condition", "tested"}


def test_payload_carries_baseline_tool_results_but_not_the_baseline_label():
    seen = {}

    def plan(p):
        seen["p"] = p
        return FX.plan("x", [], [], [])
    prov = scripted(plan, lambda p: FX.inconclusive("x", ["y"]))
    run_cop(_rt("knowledge_policy_management:abnormal", prov), "knowledge_policy_management")
    p = seen["p"]
    assert any(c["tool"] == "compare_policy_documents" for c in p["baseline_tool_results"])
    assert "CONFLICT" not in json.dumps({k: v for k, v in p.items() if k != "baseline_tool_results"})
    assert "evidence_sufficiency" in p


# ====================================================================== bounded recovery

def _wo_plan(p):
    return FX.plan("check transitions", [], [], [], FX.req("analyse_execution_transitions", "count the loop"))


def _wo_claims(p, field, value):
    return [FX.cm(p, "insights.repetition"),
            FX.ct(p, "analyse_execution_transitions", field, "TEST>TEST self-transitions", value)]


def test_recovery_reassesses_once_with_the_verifier_feedback_and_verifies_again():
    first = lambda p: FX.conclude("loop", "STALLED", 0.6, _wo_claims(p, "patterns.0.longest_run.length", 4),
                                  "loop", action="WATCH")
    fixed = lambda p: FX.conclude("loop", "STALLED", 0.6,
                                  _wo_claims(p, "patterns.0.longest_run.self_transitions", None), "loop",
                                  action="WATCH")
    prov = scripted(_wo_plan, first, fixed)
    out = run_cop(_rt("workflow_orchestration:abnormal", prov, sid="woRec1"), "workflow_orchestration")
    purposes = [i["purpose"] for i in out["llm_invocations"]]
    assert purposes == ["plan", "assess", "reassess"]
    fb = [p for purpose, p in prov.seen if purpose == "reassess"][0]["verifier_feedback"]
    assert fb[0]["code"] == VALUE_MISMATCH and (fb[0]["asserted"], fb[0]["observed"]) == (4, 5)
    f = out["finding"]
    assert f["label"] == "STALLED" and f["verified"]
    rec = out["result"]["recovery"]
    assert rec["attempted"] and rec["outcome"] == "verified" and rec["first_candidate"]["label"] == "STALLED"
    assert rec["first_rejection"][0]["check_id"] == "SC-01"


def test_recovery_does_not_rewrite_a_failed_claim_into_a_passing_one():
    same = lambda p: FX.conclude("loop", "STALLED", 0.6, _wo_claims(p, "patterns.0.longest_run.length", 4),
                                 "loop", action="WATCH")
    prov = scripted(_wo_plan, same, same)
    out = run_cop(_rt("workflow_orchestration:abnormal", prov, sid="woRec2"), "workflow_orchestration")
    f = out["finding"]
    assert f["label"] == "INCONCLUSIVE" and f["status"] == "REJECTED_BY_VERIFIER"
    assert out["result"]["recovery"]["outcome"] == "rejected_again"
    assert len(out["llm_invocations"]) == 3                             # bounded: one re-assessment only


def test_recovery_keeps_the_first_rejection_when_the_reassessment_gives_no_candidate():
    first = lambda p: FX.conclude("loop", "STALLED", 0.6, _wo_claims(p, "patterns.0.longest_run.length", 4),
                                  "loop", action="WATCH")
    prov = scripted(_wo_plan, first, lambda p: FX.inconclusive("cannot tell", ["more"]))
    out = run_cop(_rt("workflow_orchestration:abnormal", prov, sid="woRec3"), "workflow_orchestration")
    f = out["finding"]
    assert f["status"] == "REJECTED_BY_VERIFIER" and f["rejected_candidate"]["label"] == "STALLED"
    assert any(v["check_id"] == "SC-01" and v["result"] == "FAIL" for v in out["verifier_results"])


def test_no_recovery_for_an_unrecoverable_failure():
    """A quiet label without its required artifact is missing evidence, not a citation slip."""
    def assess(p):
        return FX.conclude("ok", "EFFICIENT", 0.6, [FX.cm(p, r["metric"]) for r in p["evidence"]
                                                     if r["evidence"] == "OBSERVED"][:2], "fine")
    prov = scripted(lambda p: FX.plan("x", [], [], []), assess)
    out = run_cop(_rt("workload_optimization:missing", prov, sid="wlRec"), "workload_optimization")
    assert [i["purpose"] for i in out["llm_invocations"]] == ["plan", "assess"]
    rec = out["finding"]["recovery"]
    assert rec["attempted"] is False and rec["recoverable"] is False and "DS-01" in rec["reason"]


def test_no_recovery_in_a_replay_of_recorded_replies():
    first = lambda p: FX.conclude("loop", "STALLED", 0.6, _wo_claims(p, "patterns.0.longest_run.length", 4),
                                  "loop", action="WATCH")
    prov = scripted(_wo_plan, first)
    prov.replay = True
    out = run_cop(_rt("workflow_orchestration:abnormal", prov, sid="woRec4"), "workflow_orchestration")
    rec = out["finding"]["recovery"]
    assert len(out["llm_invocations"]) == 2 and rec["attempted"] is False and rec["recoverable"] is True
    assert "live call" in rec["reason"]


def test_no_recovery_when_the_model_call_budget_is_spent():
    first = lambda p: FX.conclude("loop", "STALLED", 0.6, _wo_claims(p, "patterns.0.longest_run.length", 4),
                                  "loop", action="WATCH")
    prov = scripted(_wo_plan, first, first)
    out = run_cop(_rt("workflow_orchestration:abnormal", prov, sid="woRec5",
                      budget=LLMBudget(max_llm_calls=2)), "workflow_orchestration")
    assert len(out["llm_invocations"]) == 2 and "budget" in out["finding"]["recovery"]["reason"]


def test_recovered_action_is_still_subject_to_authority():
    first = lambda p: FX.conclude("loop", "STALLED", 0.6, _wo_claims(p, "patterns.0.longest_run.length", 4),
                                  "loop", action="ALERT")
    fixed = lambda p: FX.conclude("loop", "STALLED", 0.6,
                                  _wo_claims(p, "patterns.0.longest_run.self_transitions", None), "loop",
                                  action="PAUSE")                          # not permitted for this COP
    prov = scripted(_wo_plan, first, fixed)
    out = run_cop(_rt("workflow_orchestration:abnormal", prov, sid="woRec6"), "workflow_orchestration")
    pa = out["proposed_action"]
    assert pa["requested_action"] == "PAUSE" and pa["action"] in BY_KEY["workflow_orchestration"].permitted_actions
    assert pa["original_verification"]["result"] == "FAIL"


# ====================================================================== the bench as a whole

@pytest.fixture(scope="module")
def bench():
    live = TB.load_live()
    cases = TB.run_deterministic() + TB.run_llm(live)
    before = TB.load_before()
    for c in cases:
        c["original"] = before["cases"].get(c["id"])
        c["diagnosis"] = diagnose(c)
    return cases, before


def test_bench_keeps_all_54_cases_and_every_cop_runs_three(bench):
    cases, before = bench
    assert len(cases) == 54 and {c["id"] for c in cases} == set(before["cases"])
    per = {}
    for c in cases:
        per.setdefault(c["cop"], set()).add(c["kind"])
        assert c["dimensions"]["execution"], c["id"]
    assert len(per) == 18 and all(v == {"normal", "abnormal", "missing"} for v in per.values())


def test_expectations_are_unchanged_and_independent_of_outputs(bench):
    cases, _ = bench
    old = json.load(open(os.path.join(DOCS, "test_bench_results_before_fixes.json")))
    exp = {c["id"]: c["expected"] for c in old["cases"]}
    assert all(exp[c["id"]] == c["expected"] for c in cases)


def test_legitimate_inconclusives_stay_inconclusive(bench):
    cases, before = bench
    for c in cases:
        o = before["cases"][c["id"]]
        if c["kind"] == "missing" and o["verdict"] == "PASS":
            assert c["verdict"] == "PASS" and c["label"] == o["label"], c["id"]


def test_no_case_that_passed_before_now_fails(bench):
    cases, before = bench
    assert [c["id"] for c in cases if before["cases"][c["id"]]["verdict"] == "PASS" and c["verdict"] != "PASS"] == []


def test_every_final_action_stays_within_authority_and_nothing_executes(bench):
    cases, _ = bench
    for c in cases:
        assert c["dimensions"]["authority"], c["id"]
        assert c["trace"]["authority"]["execution_status"] not in ("EXECUTED_MOCK", "EXECUTION_FAILED")


def test_replayed_results_are_never_labelled_as_new_live_calls(bench):
    cases, _ = bench
    for c in cases:
        if c["cop"] in LE.LLM_COPS if hasattr(LE, "LLM_COPS") else c["source"] == "live":
            assert c["result_source"] == "recorded_reply_replay"
            assert all(x["simulated"] for x in c["trace"]["llm"])            # replay: model not called
            assert c["usage"]["simulated"] is False                         # the spend is the live run's own


def test_root_causes_can_co_occur_and_use_the_defined_codes(bench):
    cases, _ = bench
    ir = next(c for c in cases if c["id"] == "incident_response:missing")["diagnosis"]
    assert {"EXPECTED_INCONCLUSIVE", "VERIFIER_REJECTED"} <= set(ir["root_causes"])
    for c in cases:
        assert set(c["diagnosis"]["root_causes"]) <= set(CODES)
        if c["verdict"] != "PASS":
            assert "EVALUATION_MISMATCH" in c["diagnosis"]["root_causes"]


def test_cases_fixed_without_a_new_call_are_the_ones_the_analysis_predicts(bench):
    cases, before = bench
    fixed = sorted(c["id"] for c in cases if before["cases"][c["id"]]["verdict"] != "PASS" and c["verdict"] == "PASS")
    assert fixed == ["audit_traceability:abnormal", "governance_intelligence:abnormal",
                     "governance_intelligence:normal", "risk_mitigation:normal"]


def test_dashboard_embeds_exactly_the_results_file():
    page = open(os.path.join(DOCS, "COPS_TEST_BENCH.html")).read()
    doc = json.load(open(os.path.join(DOCS, "test_bench_results.json")))
    blob = page.split('<script type="application/json" id="data">', 1)[1].split("</script>", 1)[0]
    shown = json.loads(blob.replace("<\\/", "</"))
    assert shown["summary"] == doc["summary"] and [c["id"] for c in shown["cases"]] == [c["id"] for c in doc["cases"]]
    assert [c["verdict"] for c in shown["cases"]] == [c["verdict"] for c in doc["cases"]]
    for anchor in ("rootcauses", "beforeafter", "inctiles", "filters", "lifecycle", "fleetint"):
        assert f'id="{anchor}"' in page
