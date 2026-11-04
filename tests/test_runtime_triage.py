"""Graded verification and triage (cops/runtime/triage.py, authority.TAXONOMY).

Unit tests for each component, then the twelve scenarios of the redesign. Every
test is deterministic and offline: no model is called."""
import os, sqlite3, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "collector"))

import pytest

from cops.specs import BY_KEY, SPECS
from cops import authority as AU
from cops.runtime import triage as T
from cops.runtime import evaluation as E
from cops.runtime import fixtures as FX
from cops.runtime import live_eval as LE
from cops.runtime import synthetic as S
from cops.runtime.bench_cases import DET_CASES
from cops.runtime.graph import run_case, run_cop, resume_case, MockEnforcementGateway
from cops.runtime.llm import UnavailableProvider, FixtureProvider
from cops.runtime.verifiers import R
from cops.runtime.persistence import SQLiteStore


# ---------------------------------------------------------------- helpers

def finding(cop, label, status="SUBSTANTIVE", verified=True, conf=0.8, claims=None, action="NONE", **kw):
    f = {"cop": cop, "case_id": "t-case", "label": label, "status": status, "verified": verified,
         "confidence": conf, "origin": "computational", "claims": claims if claims is not None else [
             {"statement": "m is CRITICAL", "kind": "metric_state", "metric_id": "m"},
             {"statement": "tool says so", "kind": "tool_observation", "tool_call_id": "tc1"}],
         "proposed_action": {"action": action, "reason": "r"}, "missing_evidence": [],
         "missing_dependencies": [], "upstream_used": [], "cop_specific": {}}
    f.update(kw)
    return f


def passing(*extra):
    out = [R(c, "PASS", "ok") for c in ("EV-01", "EV-02", "ID-01", "ID-02", "MC-01", "SC-01", "SC-02",
                                        "DS-01")]
    return out + list(extra)


def suff(*items):
    return {"items": [{"item": i, "kind": k, "state": s, "retrieve_with": rw} for i, k, s, rw in items]}


FULL = suff(("metric:m", "METRIC", "RETRIEVED_AND_VALIDATED", []),
            ("artifact:a", "REQUIRED", "RETRIEVED_AND_VALIDATED", ["t"]))
CALLS = [{"tool": "t", "status": "OK"}]


def classify(cop, f, results=None, sf=FULL, **kw):
    args = dict(tool_calls=CALLS, upstream={}, evidence_ceiling=1.0, memory_view={}, recovery=None)
    args.update(kw)
    return T.classify(cop, f, results if results is not None else passing(), sf, args.pop("tool_calls"),
                      args.pop("upstream"), args.pop("evidence_ceiling"), args.pop("memory_view"),
                      args.pop("recovery"), **args)


def det(cid, **kw):
    case = next(c for c in DET_CASES if c["id"] == cid)
    sid = cid.replace(":", "-").replace("_", "")[:24]
    b = case["build"](sid)
    rt = E.make_runtime(f"tri-{sid}-{kw.get('mode', 'dry_run')}", sid, b["rows"], b["art"],
                        provider=UnavailableProvider(), **kw)
    return rt, LE.deterministic_upstream(case["cop"])


# ================================================================ unit: taxonomy and authority

def test_every_legacy_action_keeps_its_name_and_maps_into_the_taxonomy():
    for a in ("NONE", "WATCH", "ALERT", "PASS_TO_COP", "REQUEST_CONTEXT", "REQUEST_APPROVAL", "INSTRUCT",
              "ESCALATE", "PAUSE", "BLOCK"):
        assert AU.typed(a) in AU.TAXONOMY and AU.legacy(AU.typed(a)) == a


def test_every_taxonomy_action_defines_its_operational_semantics():
    need = ("trigger", "required_evidence", "quotient_band", "routing_tier", "authority_tier", "target",
            "reversibility", "approval", "execution", "on_failure", "observable_result", "verified_by")
    for a in AU.TAXONOMY.values():
        for k in need:
            assert getattr(a, k), (a.name, k)
        assert a.routing_tier in T.TIERS and a.execution in ("auto", "propose_only")
        assert a.legacy_alias in AU.LEGACY_TO_TYPED
        if a.authority_tier == AU.INTERVENE:
            assert a.execution == "propose_only" and a.approval == "required", a.name


def test_legacy_permissions_are_unchanged():
    for s in SPECS:
        for a in AU.LEGACY_TO_TYPED:
            if a in s.permitted_actions:
                assert AU.permitted(s, a)[0]
        assert AU.check(s, "BLOCK", "dry_run") == AU.check(s, "BLOCK", "dry_run")


def test_stronger_powers_need_an_explicit_grant():
    for s in SPECS:
        for a in ("STOP_SESSION", "RESTRICT_CAPABILITY", "RELEASE_HOLD", "RESTART_ISOLATED_SESSION"):
            assert AU.permitted(s, a)[0] == (a in AU.GRANTS.get(s.key, ())), (s.key, a)


def test_universal_actions_never_change_what_an_agent_may_do():
    for a in AU.UNIVERSAL:
        assert AU.TAXONOMY[a].authority_tier in (AU.OBSERVE, AU.NOTIFY, AU.REQUEST)


def test_check_typed_substitutes_like_check():
    ad, pe = BY_KEY["anomaly_detection"], BY_KEY["policy_enforcement"]
    assert AU.check_typed(ad, "STOP_SESSION")[0] is False
    ok, sub, why = AU.check_typed(pe, "HOLD_TOOL_CALL", "dry_run")
    assert not ok and sub == "ALERT" and "dry-run" in why
    assert AU.check_typed(pe, "HOLD_TOOL_CALL", "supervised", live_session=True)[0] is True
    assert AU.check_typed(pe, "HOLD_TOOL_CALL", "supervised", live_session=False)[0] is False


# ================================================================ unit: score, confidence, quotient

def test_score_5_needs_corroboration_and_no_limitation():
    s = T.evidence_support("anomaly_detection", finding("anomaly_detection", "ANOMALOUS"), passing(), FULL, {}, 1.0)
    assert s["score"] == 5 and s["candidate_verification"] == "VERIFIED" and not s["provisional"]


def test_score_drops_with_a_material_limitation_and_names_it():
    sf = suff(("metric:m", "METRIC", "RETRIEVED_AND_VALIDATED", []), ("metric:n", "METRIC",
                                                                     "MISSING_AND_UNAVAILABLE", []))
    s = T.evidence_support("anomaly_detection", finding("anomaly_detection", "ANOMALOUS"), passing(), sf, {}, 1.0)
    assert s["score"] == 4
    assert any(x["item"] == "metric:n" and x["material"] for x in s["limiting"])


def test_single_kind_support_with_a_limitation_is_provisional():
    f = finding("anomaly_detection", "ANOMALOUS", claims=[{"statement": "x", "kind": "metric_state"}])
    sf = suff(("metric:m", "METRIC", "RETRIEVED_AND_VALIDATED", []), ("metric:n", "METRIC", "MISSING_AND_UNAVAILABLE", []))
    s = T.evidence_support("anomaly_detection", f, passing(), sf, {}, 1.0)
    assert s["score"] == 3 and s["provisional"]


def test_a_value_contradiction_scores_1_or_2_never_more():
    bad = R("SC-01", "FAIL", "claim_contradicted", "x", failures=[{"code": "VALUE_MISMATCH"}])
    f = finding("anomaly_detection", "INCONCLUSIVE", status="REJECTED_BY_VERIFIER", verified=False,
                rejected_candidate={"label": "ANOMALOUS", "claims": [{}, {}, {}]})
    s = T.evidence_support("anomaly_detection", f, passing()[:5] + [bad], FULL, {}, 1.0)
    assert s["score"] == 2 and s["candidate_verification"] == "CONTRADICTED"
    assert s["contradictions"]["status"] == "factual"
    f["rejected_candidate"]["claims"] = [{}]
    assert T.evidence_support("anomaly_detection", f, passing()[:5] + [bad], FULL, {}, 1.0)["score"] == 1


def test_integrity_failure_scores_1_whatever_else_holds():
    f = finding("anomaly_detection", "INCONCLUSIVE", status="REJECTED_BY_VERIFIER", verified=False)
    s = T.evidence_support("anomaly_detection", f, [R("EV-02", "FAIL", "unresolved_ref")], FULL, {}, 1.0)
    assert s["score"] == 1 and s["candidate_verification"] == "UNTRUSTED_EVIDENCE"


def test_confidence_is_capped_by_support_and_zero_without_a_definite_reading():
    f = finding("anomaly_detection", "ANOMALOUS", conf=0.99,
                claims=[{"statement": "x", "kind": "metric_state"}])
    sf = suff(("metric:n", "METRIC", "MISSING_AND_UNAVAILABLE", []))
    t = classify("anomaly_detection", f, sf=sf)
    assert t["confidence"]["value"] == T.CONFIDENCE_CAP[t["evidence_support"]["score"]]
    assert t["confidence"]["capped"] and t["confidence"]["calibration"].startswith("uncalibrated")
    g = finding("anomaly_detection", "INCONCLUSIVE", status="INCONCLUSIVE", verified=False, conf=0.9)
    assert classify("anomaly_detection", g)["confidence"]["value"] == 0.0


def test_quotient_records_every_component_weight_and_basis():
    t = classify("anomaly_detection", finding("anomaly_detection", "ANOMALOUS", action="ALERT"))
    hq = t["perceived_handling_quotient"]
    assert set(hq["components"]) == set(T.WEIGHTS)
    assert hq["value"] == pytest.approx(sum(c["contribution"] for c in hq["components"].values()), abs=1e-3)
    assert all(c["basis"] for c in hq["components"].values())


def test_quotient_is_not_the_model_confidence():
    a = classify("anomaly_detection", finding("anomaly_detection", "ANOMALOUS", conf=0.99))
    b = classify("anomaly_detection", finding("anomaly_detection", "ANOMALOUS", conf=0.31))
    assert a["perceived_handling_quotient"]["value"] == b["perceived_handling_quotient"]["value"]


def test_every_cop_has_its_own_thresholds_and_a_justification():
    assert set(T.THRESHOLDS) == set(BY_KEY)
    for k, th in T.THRESHOLDS.items():
        assert 1 > th.autonomous > th.assisted > th.specialist > 0, k
        assert th.justification and th.version == T.THRESHOLDS_VERSION
    assert len({(t.autonomous, t.assisted, t.specialist) for t in T.THRESHOLDS.values()}) > 1


def test_the_same_quotient_routes_differently_by_cop():
    q = 0.83
    assert T.THRESHOLDS["anomaly_detection"].band(q) == T.AUTONOMOUS
    assert T.THRESHOLDS["policy_enforcement"].band(q) == T.ASSISTED


def test_every_cop_has_a_bounded_specialist_pathway():
    for k, sp in T.SPECIALISTS.items():
        for f_ in ("responsibility", "receiver", "transfers", "tools", "decides", "cannot", "returns", "human_when"):
            assert sp[f_], (k, f_)
        assert sp["is_human"] is False                     # a specialist COP is not a human
        assert set(sp["tools"]) <= set(__import__("cops.runtime.tools", fromlist=["TOOLS"]).TOOLS)
        assert all(c in BY_KEY and c != k for c in sp["support_cops"])


def test_label_action_defaults_are_within_each_cops_authority_or_fall_back():
    for k, labels in T.LABEL_ACTIONS.items():
        for lab, (default, ceiling) in labels.items():
            assert ceiling in T.TIER_ORDER and default in AU.TAXONOMY


def test_distribution_reports_what_happened_without_adjusting():
    d = T.distribution(["autonomous"] * 6 + ["assisted"] * 3 + ["human"])
    assert d["counts"] == {"autonomous": 6, "assisted": 3, "specialist": 0, "human": 1}
    assert d["observed"]["human"] == 0.1 and d["human_share_below_5pct"] is False


# ================================================================ the twelve scenarios

def test_s01_strong_evidence_is_handled_autonomously():
    rt, cops = det("resource_management:abnormal")
    r = run_case(rt, cops=cops)["results"]["resource_management"]
    t = r["triage"]
    assert t["outcome"] == "FAIL" and t["evidence_support"]["score"] >= 4
    assert t["routing"]["tier"] == "autonomous" and not t["routing"]["human_needed"]
    assert r["proposed_action"]["typed_action"] == "ALERT" and t["routing"]["automated_action_authorised"]
    assert r["proposed_action"]["dry_run_receipt"]["external_effect"] is False


def test_s02_partial_evidence_supports_a_qualified_finding():
    rt, cops = det("runtime_monitoring:abnormal")
    t = run_case(rt, cops=cops)["results"]["runtime_monitoring"]["triage"]
    s = t["evidence_support"]
    assert t["outcome"] == "FAIL" and s["score"] == 3 and s["provisional"]
    assert any(x["material"] for x in s["limiting"])           # the limitation is explicit
    assert t["confidence"]["value"] <= T.CONFIDENCE_CAP[3]


def test_s03_interpretive_contradiction_goes_to_assisted_not_inconclusive():
    f = finding("anomaly_detection", "ANOMALOUS", action="ALERT")
    t = classify("anomaly_detection", f, passing(R("RP-01", "WARN", "baseline_differs", "rules read NORMAL")))
    assert t["outcome"] == "FAIL"                              # the finding stands
    assert t["evidence_support"]["contradictions"]["status"] == "interpretive"
    assert t["evidence_support"]["score"] == 3
    assert t["routing"]["tier"] == "assisted"


def test_s04_a_contradiction_that_disproves_the_claim_does_nothing_to_the_agent():
    rt = E.make_runtime("tri-c16", "c16", E._estate(S.normal_session("c16", 9)), {},
                        FX.Script({"anomaly_detection": E.ad_wrong_steps()}))
    r = run_case(rt, cops=["runtime_monitoring", "anomaly_detection"])["results"]["anomaly_detection"]
    t = r["triage"]
    assert r["finding"]["status"] == "REJECTED_BY_VERIFIER"     # verifiers unchanged
    assert t["outcome"] == "INCONCLUSIVE" and t["evidence_support"]["score"] == 1
    assert t["evidence_support"]["candidate_verification"] == "CONTRADICTED"
    assert t["action_selection"]["cause"] == "candidate_disproved"
    assert r["proposed_action"]["typed_action"] == "NONE"       # a low score is not a violation


def test_s05_missing_but_retrievable_evidence_is_collected():
    f = finding("workload_optimization", "INCONCLUSIVE", status="INCONCLUSIVE", verified=False, action="WATCH")
    sf = suff(("artifact:task_classes", "REQUIRED", "MISSING_BUT_RETRIEVABLE", ["compare_task_classes"]))
    t = classify("workload_optimization", f, sf=sf)
    assert t["action_selection"]["selected"] == "COLLECT_EVIDENCE"
    assert "compare_task_classes" in t["action_selection"]["reason"]
    assert t["routing"]["tier"] == "assisted"
    assert t["action_selection"]["selected"] != "WATCH"        # no reflex WATCH on INCONCLUSIVE


def test_s06_missing_policy_with_a_pending_operation_holds_it_for_a_human():
    rt, cops = det("policy_enforcement:missing", mode="supervised", live=True)
    rt.pending_operations = [{"tool": "Write", "target": "/prod/config.yaml"}]
    out = run_case(rt, cops=cops)
    req = out["interrupted"][0]
    assert req["cop"] == "policy_enforcement" and req["typed_action"] == "HOLD_TOOL_CALL"
    assert req["triage"]["human_needed"] and rt.enforcement.applied == []
    # without a pending operation there is nothing to hold: the policy is requested instead
    rt2, _ = det("policy_enforcement:missing")
    t = run_case(rt2, cops=cops)["results"]["policy_enforcement"]["triage"]
    assert t["action_selection"]["selected"] == "REQUEST_CONTEXT" and t["confidence"]["value"] == 0.0


def test_s07_repeated_failure_goes_to_the_specialist():
    rej = R("SC-01", "FAIL", "claim_contradicted", "", failures=[{"code": "CITATION_MALFORMED"}])
    f = finding("workflow_orchestration", "INCONCLUSIVE", status="REJECTED_BY_VERIFIER", verified=False,
                rejected_candidate={"label": "STALLED", "claims": [{}, {}]},
                recovery={"recoverable": True, "reason": "replay"})
    once = classify("workflow_orchestration", f, passing()[:5] + [rej])
    assert once["action_selection"]["selected"] == "RECHECK"
    mem = {"unverified": [{"record_id": "r1", "from_case": "earlier-case",
                           "payload": {"label": "STALLED", "finding_status": "REJECTED_BY_VERIFIER"}}]}
    again = classify("workflow_orchestration", f, passing()[:5] + [rej], memory_view=mem)
    assert again["action_selection"]["selected"] == "DELEGATE_TO_SPECIALIST"
    assert again["routing"]["tier"] == "specialist" and not again["routing"]["human_needed"]
    assert again["referral"]["receiver"] == T.SPECIALISTS["workflow_orchestration"]["receiver"]
    assert again["referral"]["status"] == "PENDING"


def test_s08_high_impact_action_needs_a_human_however_strong_the_evidence():
    rt, cops = det("policy_enforcement:abnormal")
    r = run_case(rt, cops=cops)["results"]["policy_enforcement"]
    t = r["triage"]
    assert t["outcome"] == "FAIL" and t["evidence_support"]["score"] >= 4
    assert t["routing"]["tier"] == "human" and t["routing"]["human_needed"]
    assert r["proposed_action"]["typed_action"] == "ALERT"      # BLOCK recorded, not executed, in dry-run
    assert r["proposed_action"]["dry_run_receipt"]["human_task"]["kind"] == "approval"
    rt, cops = det("policy_enforcement:abnormal", mode="supervised", live=True)
    out = run_case(rt, cops=cops)
    assert out["interrupted"][0]["typed_action"] == "BLOCK_OPERATION" and rt.enforcement.applied == []


def test_s09_an_action_proposed_on_pass_is_suppressed_but_still_audited():
    def script(c, purpose, p, n):
        if purpose == "plan":
            return FX.plan("x", [], [], [], FX.req("calculate_deviation", "t", quantity="tokens"))
        return FX.conclude("x", "NORMAL", 0.7, [FX.cm(p, "insights.token_outlier"),
                                                FX.ct(p, "calculate_deviation", "current")], "r", "ALERT")
    rows = E._estate(S.normal_session("v9", 9))
    rt = E.make_runtime("tri-pass", "v9", rows, {}, provider=FixtureProvider(script))
    out = run_cop(rt, "anomaly_detection")
    t, pa = out["triage"], out["proposed_action"]
    assert t["outcome"] == "PASS" and t["action_selection"]["suppressed_on_pass"] == "ALERT"
    assert pa["typed_action"] == "NONE" and pa["execution_status"] == "NOT_EXECUTED_NO_ACTION"
    assert pa["original_proposal"]["action"] == "ALERT"        # kept for audit


def test_s10_an_action_outside_authority_is_never_selected():
    f = finding("workflow_orchestration", "STALLED", action="BLOCK")
    t = classify("workflow_orchestration", f)
    assert t["action_selection"]["selected"] != "BLOCK_OPERATION"
    assert "outside its authority" in t["action_selection"]["reason"]
    assert AU.permitted(BY_KEY["workflow_orchestration"], t["action_selection"]["selected"])[0]
    assert AU.check_typed(BY_KEY["anomaly_detection"], "STOP_SESSION")[0] is False


def test_s11_referral_cycles_hops_timeouts_and_cost_escalate_to_a_human():
    rr = T.ReferralRouter()
    cyc = rr.open("policy_enforcement", "policy_enforcement", "specialist", "x", "c")
    assert cyc.status == "ESCALATED_TO_HUMAN" and "cycle" in cyc.escalation_reason
    c = rr.open("policy_enforcement", "knowledge_policy_management", "cop_support", "x", "c")
    out = rr.dispatch(c, {"knowledge_policy_management": lambda c: {"refer_to": "policy_enforcement"}})
    assert out.status == "ESCALATED_TO_HUMAN" and "cycle" in out.escalation_reason
    c = rr.open("a", "b", "specialist", "x", "c")
    out = rr.dispatch(c, {"b": lambda c: {"refer_to": "d"}, "d": lambda c: {"refer_to": "e"},
                          "e": lambda c: {"result": "x"}})
    assert out.status == "ESCALATED_TO_HUMAN" and "hop limit" in out.escalation_reason
    t = iter([0, 10_000])
    c = rr.open("a", "b", "specialist", "x", "c")
    out = rr.dispatch(c, {"b": lambda c: {"result": "late"}}, clock=lambda: next(t))
    assert out.status == "ESCALATED_TO_HUMAN" and "timeout" in out.escalation_reason
    c = rr.open("a", "b", "specialist", "x", "c")
    out = rr.dispatch(c, {"b": lambda c: {"result": "x", "cost_usd": 5.0}})
    assert out.status == "ESCALATED_TO_HUMAN" and "cost" in out.escalation_reason
    c = rr.open("a", "b", "specialist", "x", "c")
    assert rr.dispatch(c, {"b": lambda c: {"result": "ok"}}).status == "RETURNED"


def _approval(tmp_path, gw):
    from langgraph.checkpoint.sqlite import SqliteSaver
    ck, st = str(tmp_path / "ck.sqlite"), str(tmp_path / "st.sqlite")
    rows = E._estate(E.sensitive_session("ap", 9))
    conn = sqlite3.connect(ck, check_same_thread=False)
    rt = E.make_runtime("tri-ap", "ap", rows, E.c05_art(), FX.Script({}), store=SQLiteStore(st),
                        mode="supervised", live=True, enforcement=gw)
    return rt, SqliteSaver(conn), ["runtime_monitoring", "interaction_discovery", "policy_enforcement"]


@pytest.mark.parametrize("gw", [MockEnforcementGateway(fail=("BLOCK",)),
                                MockEnforcementGateway(unconfirmed=("BLOCK",))], ids=["fails", "unconfirmed"])
def test_s12_failed_or_unconfirmed_execution_becomes_a_human_task(tmp_path, gw):
    rt, saver, cops = _approval(tmp_path, gw)
    run_case(rt, cops=cops, checkpointer=saver)
    out = resume_case(rt, {"decision": "approve", "reviewer": "lead"}, cops=cops, checkpointer=saver)
    r = out["results"]["policy_enforcement"]
    t = r["triage"]
    assert r["proposed_action"]["execution_status"] == "EXECUTION_FAILED"
    assert t["feedback"]["execution_status"] == "EXECUTION_FAILED" and t["feedback"]["follow_up"]
    assert t["routing"]["tier"] == "human" and t["routing"]["automated_action_authorised"] is False
    assert t["human_task"]["kind"] == "execution_failure"


# ================================================================ the benchmark itself

def test_bench_routing_is_reported_not_forced(tmp_path):
    from cops.runtime import test_bench as TB
    cases = TB.run_deterministic()
    tiers = [c["trace"]["triage"]["routing"]["tier"] for c in cases]
    assert len(tiers) == 24 and set(tiers) <= set(T.TIERS)
    for c in cases:
        t = c["trace"]["triage"]
        if t["outcome"] == "PASS":
            assert c["trace"]["typed_action"] == "NONE", c["id"]        # Sundar's rule
        if t["outcome"] == "INCONCLUSIVE":
            assert c["trace"]["typed_action"] != "WATCH", c["id"]       # no reflex WATCH
        assert t["evidence_support"]["score"] in (1, 2, 3, 4, 5)
        assert AU.permitted(BY_KEY[c["cop"]], c["trace"]["typed_action"])[0], c["id"]


def test_triage_record_matches_the_contract():
    from dataclasses import fields
    from cops.runtime.contracts import TriageRecord, EvidenceSupport, ConfidenceBasis, HandlingQuotient, Routing
    rt, cops = det("resource_management:abnormal")
    t = run_case(rt, cops=cops)["results"]["resource_management"]["triage"]
    for cls, obj in ((TriageRecord, t), (EvidenceSupport, t["evidence_support"]),
                     (ConfidenceBasis, t["confidence"]), (HandlingQuotient, t["perceived_handling_quotient"]),
                     (Routing, t["routing"])):
        assert {f.name for f in fields(cls)} <= set(obj), cls.__name__


def test_a_low_quotient_alone_never_routes_to_a_human(monkeypatch):
    """Phase 3: escalation to a human must not depend only on a low quotient.
    On real inputs a verified FAIL with a permitted, reversible action stays
    above the specialist bar (the thinnest case computes about 0.54), so the
    quotient is forced below the bar here to exercise the rule."""
    f = finding("anomaly_detection", "ANOMALOUS", action="ALERT")
    real = T.handling_quotient
    monkeypatch.setattr(T, "handling_quotient", lambda *a, **k: {**real(*a, **k), "value": 0.2})
    t = classify("anomaly_detection", f)
    assert t["outcome"] == "FAIL" and t["routing"]["band_from_quotient"] == "human"
    assert t["routing"]["tier"] == "specialist" and not t["routing"]["human_needed"]
    assert t["action_selection"]["selected"] == "DELEGATE_TO_SPECIALIST"
    assert t["referral"]["receiver"] == T.SPECIALISTS["anomaly_detection"]["receiver"]
