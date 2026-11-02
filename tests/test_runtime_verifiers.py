"""Evidence semantics (B4) and verifier independence (B8)."""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "collector"))

import pytest

from cops.models import MetricEvent, INCONCLUSIVE
from cops.interpret import RuleInterpreter
from cops.runtime import evaluation as E, fixtures as FX, synthetic as S
from cops.runtime.contracts import (INVALID, UNKNOWN, NOT_APPLICABLE, OBSERVED, MISSING, BLOCKS_FINDING,
                                    BLOCKS_ACTION, BLOCKS_MEMORY, DIAGNOSTIC)
from cops.runtime.evidence import classify, assess, usable
from cops.runtime.graph import run_cop
from cops.runtime.llm import FixtureProvider
from cops.runtime.verifiers import CHECKS, CATEGORIES, REGISTRY_VERSION, describe


def ev(**k):
    base = dict(metric_event_id="e1", metric_name="m", metric_id="insights.secrets", session_id="s1", state="NORMAL", value=0.0, threshold=None,
                evidence_refs=[], features={}, source="observables", metric_version="2026-10-05.1")
    base.update(k)
    return MetricEvent(**{f: base.get(f) for f in MetricEvent.__dataclass_fields__ if f in base})


# ---------------------------------------------------------------- B4 evidence semantics

def test_five_evidence_states_are_distinct():
    assert classify(ev(), "s1")[0] == OBSERVED
    assert classify(ev(state="UNKNOWN", value=None, features={"reason": "basis absent"}), "s1") == (UNKNOWN, "basis absent")
    assert classify(ev(state="NOT_APPLICABLE", value=None), "s1")[0] == NOT_APPLICABLE
    assert classify(ev(state="CRITICAL", value=None), "s1")[0] == INVALID           # judged, nothing measured
    assert classify(ev(source=None), "s1")[0] == INVALID                            # no provenance
    assert classify(ev(session_id="other"), "s1")[0] == INVALID                     # wrong session
    assert classify(ev(state="BANANA"), "s1")[0] == INVALID
    pic = assess([ev(), ev()], ["insights.secrets", "insights.blocked"], "s1")
    assert [r["evidence"] for r in pic["rows"]] == [INVALID, MISSING]                # duplicate; never computed
    assert not pic["judgeable"]


def test_invalid_rows_are_withheld_from_rules():
    good, bad = ev(metric_id="insights.blocked"), ev(state="CRITICAL", value=None)
    pic = assess([good, bad], ["insights.blocked", "insights.secrets"], "s1")
    assert [e.metric_id for e in usable([good, bad], pic)] == ["insights.blocked"]


def test_only_not_applicable_is_not_a_judgement():
    pic = assess([ev(state="NOT_APPLICABLE", value=None)], ["insights.secrets"], "s1")
    assert pic["only_not_applicable"] and not pic["judgeable"] and pic["attention"] == []


def test_audit_and_classification_do_not_conclude_from_nothing():
    ri = RuleInterpreter()
    assert ri.audit_traceability([], {}, {}, 0.9)["interpretation"] == INCONCLUSIVE
    assert ri.classification_tagging([], {}, {}, 0.9)["interpretation"] == INCONCLUSIVE


# ---------------------------------------------------------------- B8 registry

def test_registry_is_versioned_categorised_and_effects_are_declared():
    assert REGISTRY_VERSION and set(c[1] for c in CHECKS.values()) == set(CATEGORIES)
    assert {c[2] for c in CHECKS.values()} == {BLOCKS_FINDING, BLOCKS_ACTION, BLOCKS_MEMORY, DIAGNOSTIC}
    # the only re-run check is diagnostic and says it is not independent
    assert CHECKS["RP-01"][2] == DIAGNOSTIC and CHECKS["RP-01"][3] is False
    assert all(c[3] for k, c in CHECKS.items() if k != "RP-01")
    assert len(describe()) == len(CHECKS)


ROWS = E._estate(S.normal_session("v1", 9))


def verify_with(claims, label="ANOMALOUS", upstream=None, cop="anomaly_detection", rows=ROWS, sid="v1"):
    script = lambda c, purpose, p, n: FX.plan("x", [], [], [], FX.req("calculate_deviation", "t", quantity="tokens")) \
        if purpose == "plan" else FX.conclude("x", label, 0.7, claims(p), "r", "WATCH")
    rt = E.make_runtime(f"v-{abs(hash(str(claims)))}", sid, rows, {}, provider=FixtureProvider(script))
    out = run_cop(rt, cop, upstream=upstream)
    return out["finding"], {r["check_id"]: r for r in out["verifier_results"]}


def test_true_claims_pass_independent_checks():
    f, v = verify_with(lambda p: [FX.cm(p, "insights.token_outlier"), FX.ct(p, "calculate_deviation", "current")],
                       label="NORMAL")
    assert f["verified"] and v["SC-01"]["result"] == "PASS" and v["MC-01"]["result"] == "PASS"


def test_mc01_recomputes_from_raw_and_can_fail():
    f, v = verify_with(lambda p: [{"statement": "ratio 40", "kind": "metric_value", "metric_id": "insights.token_outlier",
                                   "asserted_value": 40.0, "evidence_refs": ["metric:insights.token_outlier"]}])
    assert v["MC-01"]["result"] == "FAIL" and f["status"] == "REJECTED_BY_VERIFIER"


def test_sc01_catches_a_misread_tool_result():
    f, v = verify_with(lambda p: [FX.ct(p, "calculate_deviation", "current", value=99999.0)])
    assert v["SC-01"]["result"] == "FAIL" and "tool returned" in v["SC-01"]["detail"]


def test_ev02_and_id02_catch_invented_references():
    f, v = verify_with(lambda p: [{"statement": "made up", "kind": "tool_observation", "tool_call_id": "nope",
                                   "result_field": "x", "asserted_value": 1, "evidence_refs": ["tool:nope"]}])
    assert v["EV-02"]["result"] == "FAIL" and v["ID-02"]["result"] == "FAIL"


def test_ev01_catches_label_outside_vocabulary():
    f, v = verify_with(lambda p: [FX.cm(p, "insights.token_outlier")], label="SUSPICIOUS")
    assert v["EV-01"]["result"] == "FAIL" and f["label"] == "INCONCLUSIVE"


def test_ds01_quiet_label_on_insufficient_telemetry_fails():
    rows = E._estate(S.Session("thin", 9, collectors=("cli_wrapper",)).start().tools(S.NORMAL_TOOLS).end())
    rm = {"cop": "runtime_monitoring", "label": "INSUFFICIENT", "status": "SUBSTANTIVE", "verified": True,
          "finding_id": "f-rm", "lineage": ["telemetry.collector_coverage"]}
    f, v = verify_with(lambda p: [FX.ct(p, "calculate_deviation", "current")], label="NORMAL",
                       upstream={"runtime_monitoring": rm}, rows=rows, sid="thin")
    assert v["DS-01"]["result"] == "FAIL" and f["status"] == "REJECTED_BY_VERIFIER"


def test_ds02_correlated_upstream_counted_as_independent_fails():
    from cops.runtime.verifiers import verify_finding
    from cops.runtime.case import CaseData
    case = CaseData("x", "t1", "v1", ROWS, synthetic=True)
    spec = __import__("cops.specs", fromlist=["BY_KEY"]).BY_KEY["risk_mitigation"]
    pic = assess(case.case_events(spec.metric_keys), spec.metric_keys, "v1")
    up = {"anomaly_detection": {"label": "ANOMALOUS", "verified": True, "status": "SUBSTANTIVE",
                                "finding_id": "a", "lineage": ["insights.token_outlier"]}}
    f = {"label": "EMERGING", "status": "SUBSTANTIVE", "confidence": 0.5, "claims": [],
         "upstream_used": [{"cop": "anomaly_detection", "counted_as": "independent"}]}
    res = verify_finding({"finding": f, "spec": spec, "picture": pic, "events": case.case_events(spec.metric_keys),
                          "case": case, "tool_calls": {}, "upstream": up, "memory": None,
                          "cop": "risk_mitigation", "baseline": None, "rule_rerun": None})
    ds02 = next(r for r in res if r["check_id"] == "DS-02")
    assert ds02["result"] == "FAIL"                 # token_outlier is one of Risk's own metrics


def test_rp01_is_diagnostic_and_never_blocks():
    f, v = verify_with(lambda p: [FX.cm(p, "insights.token_outlier"), FX.ct(p, "calculate_deviation", "current")],
                       label="NORMAL")
    assert v["RP-01"]["effect"] == "DIAGNOSTIC" and not v["RP-01"]["independent"]


# ---------------------------------------------------------------- AU-01 on the original proposal

def run_with_action(action, mode="dry_run", live=False, cop="anomaly_detection"):
    def script(c, purpose, p, n):
        if purpose == "plan":
            return FX.plan("x", [], [], [], FX.req("calculate_deviation", "t", quantity="tokens"))
        return FX.conclude("x", "NORMAL", 0.7, [FX.cm(p, "insights.token_outlier"),
                                                FX.ct(p, "calculate_deviation", "current")], "r", action)
    rt = E.make_runtime(f"au-{action}-{mode}", "v1", ROWS, {}, provider=FixtureProvider(script),
                        mode=mode, live=live)
    out = run_cop(rt, cop)
    assert out["finding"]["verified"]                    # the finding itself is sound
    return out["proposed_action"], out["verifier_results"]


def test_au01_fails_directly_on_an_unauthorised_action():
    from cops.runtime.verifiers import au01, verify_action
    from cops.specs import BY_KEY
    ad, pe = BY_KEY["anomaly_detection"], BY_KEY["policy_enforcement"]
    assert au01(ad, "BLOCK", "supervised", "x")["result"] == "FAIL"
    assert au01(ad, "BLOCK", "supervised", "x")["reason_code"] == "not_permitted"
    assert au01(ad, "SELF_GRANT", "supervised", "x")["reason_code"] == "not_permitted"   # not in the vocabulary
    assert au01(pe, "BLOCK", "dry_run", "x")["reason_code"] == "intervene_in_dry_run"
    assert au01(pe, "BLOCK", "supervised", "x")["result"] == "PASS"
    case = __import__("cops.runtime.case", fromlist=["CaseData"]).CaseData("x", "t1", "v1", ROWS, synthetic=True)
    first = verify_action(ad, {"action": "BLOCK"}, "supervised", case, {})[0]
    assert (first["check_id"], first["result"], first["subject"]) == ("AU-01", "FAIL", "final_action")


def test_au01_fails_on_an_unauthorised_proposal_inside_the_graph():
    pa, vr = run_with_action("BLOCK", mode="supervised", live=True)   # Anomaly Detection may not BLOCK
    orig = pa["original_verification"]
    assert pa["original_proposal"]["action"] == "BLOCK"
    assert (orig["check_id"], orig["result"], orig["reason_code"], orig["subject"]) == \
        ("AU-01", "FAIL", "not_permitted", "original_proposal")
    assert pa["corrected_proposal"]["action"] == "WATCH"
    assert pa["corrected_proposal"]["source"] == "authority.check substitute"
    assert pa["action"] == "WATCH" and pa["final_authority"]["action"] == "WATCH"
    final = [c for c in pa["final_authority"]["checks"] if c["check_id"] == "AU-01"][0]
    assert final["result"] == "PASS" and final["subject"] == "final_action"
    assert pa["execution_status"] == "NOT_EXECUTED_NO_ACTION"     # BLOCK never reached execution
    au = [r for r in vr if r["check_id"] == "AU-01"]
    assert [r["subject"] for r in au] == ["original_proposal", "final_action"]


def test_au01_records_the_dry_run_substitution_as_a_failed_original():
    from cops.runtime.graph import run_cop as rc
    rows = E._estate(E.sensitive_session("au-pe", 9))
    rt = E.make_runtime("au-pe", "au-pe", rows, {**E.c05_art()}, None)
    pa = rc(rt, "policy_enforcement")["proposed_action"]
    assert pa["original_proposal"]["action"] == "BLOCK"
    assert pa["original_verification"]["reason_code"] == "intervene_in_dry_run"
    assert pa["corrected_proposal"]["action"] == "ALERT"
    assert pa["final_authority"]["authorised"] and pa["execution_status"] == "NOT_EXECUTED_DRY_RUN"


def test_au01_passes_and_records_no_correction_for_a_permitted_proposal():
    pa, vr = run_with_action("ALERT")
    assert pa["original_verification"]["result"] == "PASS" and pa["corrected_proposal"] is None
    assert pa["final_authority"]["action"] == "ALERT"


def test_ds03_rejects_a_quiet_model_reading_the_rule_baseline_contradicts():
    from cops.runtime import live_eval as L
    case = next(c for c in L.CASES if c["id"] == "knowledge_policy_management:abnormal")
    r = L.run_one(case, L.offline_provider(), None)       # the scripted reply says CURRENT
    assert r["baseline_label"] == "CONFLICT" and r["rejected_candidate"] == "CURRENT"
    assert r["status"] == "REJECTED_BY_VERIFIER" and r["label"] == "INCONCLUSIVE"
    assert "DS-03" in r["verifier_fail"]


def test_ds03_passes_when_baseline_agrees_and_is_not_run_on_attention_labels():
    from cops.runtime import live_eval as L
    ok = L.run_one(next(c for c in L.CASES if c["id"] == "knowledge_policy_management:normal"),
                   L.offline_provider(), None)
    assert ok["label"] == "CURRENT" and ok["verified"] and "DS-03" not in ok["verifier_fail"]
    rt = E.make_runtime("ds03-attn", "c02x", E._estate(E.spike_session("c02x", 9)), {},
                        provider=FixtureProvider(lambda c, purpose, p, n: E.ad_spike_steps()[min(n, 2)](p)))
    out = run_cop(rt, "anomaly_detection")
    ds03 = {x["check_id"]: x for x in out["verifier_results"]}["DS-03"]
    assert out["finding"]["label"] == "ANOMALOUS" and ds03["result"] == "NOT_RUN"


def test_sc01_rejects_a_value_claim_that_states_no_value():
    # first live run (2026-11-02): the model put 40.0 in the statement and left asserted_value empty
    def script(c, purpose, p, n):
        if purpose == "plan":
            return FX.plan("x", [], [], [], FX.req("calculate_deviation", "t", quantity="tokens"))
        claim = {**FX.ct(p, "calculate_deviation", "current"), "asserted_value": None}
        return FX.conclude("x", "NORMAL", 0.7, [FX.cm(p, "insights.token_outlier"), claim], "r")
    rt = E.make_runtime("sc01-novalue", "v1", ROWS, {}, provider=FixtureProvider(script))
    out = run_cop(rt, "anomaly_detection")
    sc = {x["check_id"]: x for x in out["verifier_results"]}["SC-01"]
    assert (sc["result"], sc["reason_code"]) == ("FAIL", "claim_unchecked")
    assert "no asserted_value" in sc["detail"] and out["finding"]["status"] == "REJECTED_BY_VERIFIER"


def test_result_field_paths_accept_both_list_index_forms():
    from cops.runtime.verifiers import _get_path
    r = {"snapshots": [{"ids": ["C1", "C2"]}, {"ids": ["C2"]}]}
    assert _get_path(r, "snapshots.1.ids") == _get_path(r, "snapshots[1].ids") == ["C2"]
    assert _get_path(r, "snapshots.5.ids") is None


def test_result_field_paths_from_the_live_run():
    # forms the model wrote in the 2026-11-02 live run that the resolver did not understand
    from cops.runtime.verifiers import _get_path
    r = {"repeated_reads": {"/ws/app.py": 7}, "risks": [{"impact": 3}, {"impact": 1}]}
    assert _get_path(r, "repeated_reads./ws/app.py") == 7          # a key containing dots
    assert _get_path(r, "result.risks.1.impact") == 1              # prefixed with the payload's "result"
    assert _get_path(r, "risks.1.impact") == 1 and _get_path(r, "risks.1.missing") is None
