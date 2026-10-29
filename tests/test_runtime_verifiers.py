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
