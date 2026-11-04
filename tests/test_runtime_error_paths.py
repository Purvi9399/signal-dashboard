"""Error and edge branches the acceptance review found untested: required
evidence, the confidence ceiling, malformed and incomplete model output,
budget stops, rule and gateway failures, memory failures, and verifier
failure branches. All model replies are simulated."""
import os, sqlite3, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "collector"))

import pytest
from langgraph.checkpoint.sqlite import SqliteSaver

from cops.runtime import evaluation as E, fixtures as FX, synthetic as S
from cops.runtime import graph as G
from cops.runtime.case import CaseData
from cops.runtime.evidence import assess
from cops.runtime.graph import run_cop, run_case, resume_case, MockEnforcementGateway
from cops.runtime.llm import FixtureProvider, ProviderUnavailable, LLMBudget
from cops.runtime.memory import MemoryAccessDenied
from cops.runtime.persistence import SQLiteStore
from cops.runtime.requirements import (REQUIREMENTS, assess_requirements, required_unavailable,
                                       REQUIRED, NOT_APPLICABLE, UNAVAILABLE)
from cops.runtime.verifiers import verify_finding, verify_action, verify_memory_update
from cops.specs import BY_KEY

NORMAL = E._estate(S.normal_session("ep", 9))
SPIKE = E._estate(E.spike_session("eps", 9))
QUIET = {k: s.interpretations[0] for k, s in BY_KEY.items()}


def rt(rows, sid, art=None, script=None, provider=None, **kw):
    return E.make_runtime(f"ep-{sid}-{abs(hash(str(art)))}", sid, rows, art or {}, script,
                          provider=provider, **kw)


def scripted(plan_fn, assess_fn):
    return FixtureProvider(lambda c, purpose, p, n: plan_fn(p) if purpose == "plan" else assess_fn(p, n))


# ---------------------------------------------------------------- 1. required evidence

# A minimal artifact for each requirement, enough for its tool to answer.
SUPPLY = {
    "task_plan": {"objective": "o", "milestones": []},
    "context_snapshots": S.context_snapshots([{"id": "C1", "text": "t"}], [], "2026-10-09T10:20:00Z",
                                             "2026-10-09T10:00:00Z"),
    "agent_registry": {"agents": [{"agent": S.OPS, "approved_versions": [], "approved_models": [],
                                   "approved_mcp_servers": []}], "approved_changes": []},
    "policy_store": E.allow_all(),
    "entitlements": {S.OPS: {"mcp_servers": []}},
    "recovery_records": [],
    "taxonomy": S.taxonomy(),
    "quotas": {S.OPS: {"token_budget": 10_000_000}},
    "risk_register": S.risk_register(),
    "task_classes": {},
}


def test_every_requirement_names_a_known_cop_and_artifact():
    for cop, reqs in REQUIREMENTS.items():
        assert cop in BY_KEY
        for art, kind, backs, applies in reqs:
            assert kind in (REQUIRED, "OPTIONAL") and backs


GATED_DETERMINISTIC = [(c, a) for c, reqs in REQUIREMENTS.items()
                       for a, kind, _, applies in reqs if kind == REQUIRED and applies is None
                       and c in ("agent_lifecycle_management", "policy_enforcement", "resource_management")]


@pytest.mark.parametrize("cop,artifact", GATED_DETERMINISTIC)
def test_deterministic_quiet_label_is_withheld_without_required_artifact(cop, artifact):
    full = {k: v for k, v in SUPPLY.items()}
    with_all = run_cop(rt(NORMAL, "ep", full), cop)["finding"]
    assert with_all["label"] == QUIET[cop] and with_all["verified"], with_all["label"]
    without = {k: v for k, v in full.items() if k != {"budget_or_quota": "quotas"}.get(artifact, artifact)}
    f = run_cop(rt(NORMAL, "ep", without), cop)["finding"]
    assert f["label"] == "INCONCLUSIVE" and f["status"] == "MISSING_DEPENDENCY"
    assert f["gated_label"] == QUIET[cop]
    assert artifact in [d["dependency"] for d in f["missing_dependencies"]]
    req = [r for r in f["evidence_requirements"] if r["artifact"] == artifact][0]
    assert req["kind"] == REQUIRED and req["status"] == UNAVAILABLE


QUIET_LLM = ["workflow_orchestration", "classification_tagging", "risk_mitigation", "workload_optimization"]


@pytest.mark.parametrize("cop", QUIET_LLM)
def test_llm_quiet_label_without_required_artifact_is_rejected_by_ds01(cop):
    """The scripted model concludes the quiet label anyway; DS-01 refuses it."""
    def assess_fn(p, n):
        claims = [FX.cm(p, r["metric"]) for r in p["evidence"] if r["evidence"] == "OBSERVED"][:3]
        return FX.conclude("all quiet", QUIET[cop], 0.7, claims, "nothing seen")
    out = run_cop(rt(NORMAL, "ep", {}, provider=scripted(lambda p: FX.plan("x", [], [], []), assess_fn)), cop)
    f = out["finding"]
    if cop == "classification_tagging":
        # The taxonomy gates every Classification label, so the evidence precheck stops
        # before the model is called: no candidate exists for a verifier to reject.
        assert out["llm_invocations"] == []
        assert f["status"] == "MISSING_DEPENDENCY" and f["label"] == "INCONCLUSIVE"
        assert "MISSING_EVIDENCE" in f["reason_codes"] and "artifact:taxonomy" in str(f["missing_evidence"])
        return
    ds = [v for v in out["verifier_results"] if v["check_id"] == "DS-01"][0]
    assert ds["result"] == "FAIL" and "required evidence unavailable" in ds["detail"]
    assert f["status"] == "REJECTED_BY_VERIFIER" and f["label"] == "INCONCLUSIVE"
    assert f["rejected_candidate"]["label"] == QUIET[cop]


def test_attention_label_is_not_gated_by_an_absent_artifact():
    # STALLED is observed from the session; it does not need the task plan.
    assert required_unavailable("workflow_orchestration", CaseData("x", "t", "ep", NORMAL, synthetic=True),
                                "STALLED", "NORMAL") == []
    # Classification needs the taxonomy for every label, not only the quiet one.
    assert required_unavailable("classification_tagging", CaseData("x", "t", "ep", NORMAL, synthetic=True),
                                "INTEGRITY", "UNCLASSIFIED")[0]["artifact"] == "taxonomy"


def test_entitlements_are_required_only_when_an_mcp_server_was_used():
    plain = CaseData("x", "t", "ep", NORMAL, synthetic=True)
    ent = [r for r in assess_requirements("policy_enforcement", plain) if r["artifact"] == "entitlements"][0]
    assert ent["kind"] == NOT_APPLICABLE
    s = S.Session("mcp", 9).start().tools(S.NORMAL_TOOLS)
    s.add(observable_type="tool_request", tool_name="mcp__x", mcp_server="x", tool_arguments={"i": 1})
    mcp = CaseData("x", "t", "mcp", E._estate(s.full_instrumentation().end()), synthetic=True)
    ent = [r for r in assess_requirements("policy_enforcement", mcp) if r["artifact"] == "entitlements"][0]
    assert ent["kind"] == REQUIRED and ent["status"] == UNAVAILABLE


def test_recovery_records_are_not_applicable_without_a_failure():
    plain = CaseData("x", "t", "ep", NORMAL, synthetic=True)
    assert assess_requirements("recovery_resilience", plain)[0]["kind"] == NOT_APPLICABLE
    failed = CaseData("x", "t", "rr", E._estate(E.recovery_session("rr", 9)), synthetic=True)
    assert assess_requirements("recovery_resilience", failed)[0]["kind"] == REQUIRED


def test_optional_evidence_never_gates():
    # Incident Response's policy store is optional: NO_INCIDENT stands without it.
    assert required_unavailable("incident_response", CaseData("x", "t", "ep", NORMAL, synthetic=True)) == []


# ---------------------------------------------------------------- 2. confidence ceiling

def test_confidence_is_capped_at_the_evidence_ceiling():
    def assess_fn(p, n):
        return FX.conclude("spike", "ANOMALOUS", 0.97, [FX.cm(p, "insights.token_outlier"),
                                                         FX.ct(p, "calculate_deviation", "current")],
                           "tokens far above baseline", "ALERT")
    prov = scripted(lambda p: FX.plan("x", [], [], [], FX.req("calculate_deviation", "t", quantity="tokens")),
                    assess_fn)
    f = run_cop(rt(SPIKE, "eps", provider=prov), "anomaly_detection")["finding"]
    assert f["verified"] and f["label"] == "ANOMALOUS"
    assert f["confidence"] == 0.9                       # ceiling with no Runtime Monitoring verdict
    assert any("capped at evidence ceiling" in q for q in f["qualifications"])


# ---------------------------------------------------------------- 3. malformed and incomplete model output

def test_detail_that_fails_the_cop_schema_is_inconclusive():
    def assess_fn(p, n):
        return FX.conclude("spike", "ANOMALOUS", 0.8, [FX.cm(p, "insights.token_outlier")], "r", "ALERT",
                           detail={"not_a_field": 1})
    f = run_cop(rt(SPIKE, "eps", provider=scripted(lambda p: FX.plan("x", [], [], []), assess_fn)),
                "anomaly_detection")["finding"]
    assert f["label"] == "INCONCLUSIVE" and not f["verified"]
    assert f["cop_specific"]["stop_reason"] == "detail_invalid"


def test_conclude_without_a_finding_is_inconclusive():
    def assess_fn(p, n):
        return {"hypothesis": "h", "hypothesis_changed": False, "decision": "conclude", "tool_requests": []}
    f = run_cop(rt(NORMAL, "ep", provider=scripted(lambda p: FX.plan("x", [], [], []), assess_fn)),
                "anomaly_detection")["finding"]
    assert f["label"] == "INCONCLUSIVE" and f["cop_specific"]["stop_reason"] == "conclude_without_finding"


def test_provider_failure_at_the_assess_stage_is_recorded():
    def assess_fn(p, n):
        return ProviderUnavailable("provider went away mid-investigation")
    out = run_cop(rt(NORMAL, "ep", provider=scripted(lambda p: FX.plan("x", [], [], []), assess_fn)),
                  "anomaly_detection")
    f = out["finding"]
    assert f["label"] == "INCONCLUSIVE" and f["cop_specific"]["stop_reason"] == "llm_unavailable"
    assert [i["purpose"] for i in out["llm_invocations"]] == ["plan", "assess"]
    assert out["llm_invocations"][1]["error"].startswith("unavailable")


def test_asking_for_more_evidence_but_naming_none_stops_the_loop():
    def assess_fn(p, n):
        return FX.more("need more", False, "unsure")             # no tool requested
    out = run_cop(rt(NORMAL, "ep", provider=scripted(lambda p: FX.plan("x", [], [], []), assess_fn)),
                  "anomaly_detection")
    assert out["finding"]["label"] == "INCONCLUSIVE"
    assert out["finding"]["cop_specific"]["stop_reason"] == "no_further_evidence_requested"
    assert len(out["llm_invocations"]) == 2                    # no further call was made


def test_tool_budget_exhaustion_stops_with_a_reason():
    def assess_fn(p, n):
        return FX.more("again", False, "more", FX.req("calculate_deviation", "t", quantity="tokens"))
    budget = LLMBudget(max_llm_calls=4, max_tool_calls=1)
    out = run_cop(rt(SPIKE, "eps", provider=scripted(
        lambda p: FX.plan("x", [], [], [], FX.req("calculate_deviation", "t", quantity="tokens")), assess_fn),
        budget=budget), "anomaly_detection")
    f = out["finding"]
    assert f["label"] == "INCONCLUSIVE"
    assert f["cop_specific"]["stop_reason"].startswith("budget_exhausted")
    assert [t for t in out["tool_calls"] if t["requested_by"] == "llm" and t["status"] == "OK"].__len__() == 1


# ---------------------------------------------------------------- 4. rule, gateway and memory failures

def test_a_rule_that_raises_is_a_failed_finding(monkeypatch):
    def boom(ctx):
        raise ValueError("bad data")
    monkeypatch.setitem(G.RULES, "behavior_analytics", boom)
    out = run_cop(rt(NORMAL, "ep"), "behavior_analytics")
    f = out["finding"]
    assert f["status"] == "FAILED" and f["label"] == "INCONCLUSIVE" and not f["verified"]
    assert "rule error ValueError" in f["rationale"]
    assert out["proposed_action"]["execution_status"] == "NOT_EXECUTED_NO_ACTION"


def test_an_enforcement_gateway_that_raises_is_execution_failed(tmp_path):
    rows = E._estate(E.sensitive_session("gw", 9))
    ck = sqlite3.connect(str(tmp_path / "ck.sqlite"), check_same_thread=False)
    r = E.make_runtime("gw-case", "gw", rows, E.c05_art(), FX.Script({}), mode="supervised", live=True,
                       enforcement=MockEnforcementGateway(fail=("BLOCK",)))
    cops = ["runtime_monitoring", "interaction_discovery", "policy_enforcement"]
    run_case(r, cops=cops, checkpointer=SqliteSaver(ck))
    out = resume_case(r, {"decision": "approve", "reviewer": "lead"}, cops=cops, checkpointer=SqliteSaver(ck))
    pa = out["results"]["policy_enforcement"]["proposed_action"]
    assert pa["execution_status"] == "EXECUTION_FAILED"
    assert pa["execution_outcome"]["applied"] is False and "RuntimeError" in pa["execution_outcome"]["detail"]
    ex = [v for v in out["results"]["policy_enforcement"]["verifier_results"] if v["check_id"] == "EX-01"]
    assert ex and ex[-1]["result"] == "FAIL"
    assert r.enforcement.applied == []


class DenyingMemory:
    """A memory gateway that refuses every operation."""
    def read(self, *a, **k):
        raise MemoryAccessDenied("denied for the test")

    def write(self, *a, **k):
        raise MemoryAccessDenied("write denied for the test")

    def get(self, *a, **k):
        raise MemoryAccessDenied("denied")

    def access_log(self, **k):
        return []


def test_memory_read_and_write_denial_is_recorded_not_fatal():
    r = rt(SPIKE, "eps")
    r.memory = DenyingMemory()
    out = run_cop(r, "resource_management")
    assert out["memory_view"]["available"] is False and "denied" in out["memory_view"]["denied"]
    mw = out["finding"].get("memory_write") or out.get("memory_write")
    assert mw["written"] is False and "denied" in mw["reason"]


def test_no_memory_gateway_is_recorded():
    r = rt(SPIKE, "eps")
    r.memory = None
    out = run_cop(r, "resource_management")
    assert out["memory_view"] == {"available": False}
    mw = out["finding"].get("memory_write") or out.get("memory_write")
    assert mw == {"written": False, "reason": "no memory gateway"}


# ---------------------------------------------------------------- 5. verifier failure branches, called directly

CASE = CaseData("v", S.TENANT, "ep", NORMAL, synthetic=True)


def vctx(cop, finding, upstream=None, case=CASE):
    spec = BY_KEY[cop]
    events = case.case_events(spec.metric_keys)
    return {"finding": finding, "spec": spec, "picture": assess(events, spec.metric_keys, case.session_id),
            "events": events, "case": case, "tool_calls": {}, "upstream": upstream or {}, "memory": None,
            "cop": cop, "baseline": None, "rule_rerun": None}


def res(results, cid):
    return [r for r in results if r["check_id"] == cid][0]


def base(**k):
    f = {"label": "NORMAL", "status": "SUBSTANTIVE", "confidence": 0.6, "claims": [], "upstream_used": []}
    f.update(k)
    return f


def test_ev01_catches_bad_status_confidence_and_claim_kind():
    r = res(verify_finding(vctx("anomaly_detection", base(status="DONE", confidence=1.5,
                                                          claims=[{"kind": "vibes", "evidence_refs": []}]))),
            "EV-01")
    assert r["result"] == "FAIL"
    assert "status 'DONE'" in r["detail"] and "confidence 1.5" in r["detail"] and "claim kinds" in r["detail"]


def test_id01_catches_unsupplied_metric():
    claim = {"kind": "metric_state", "metric_id": "insights.secrets", "asserted_state": "NORMAL",
             "evidence_refs": ["metric:insights.secrets"], "statement": "s"}
    # insights.secrets is not one of Workload Optimization's metrics
    r = res(verify_finding(vctx("workload_optimization", base(label="EFFICIENT", claims=[claim]))), "ID-01")
    assert r["result"] == "FAIL" and "was not supplied" in r["detail"]


def test_mc01_catches_a_metric_row_that_disagrees_with_raw_data():
    case = CaseData("v2", S.TENANT, "eps", SPIKE, synthetic=True)
    for e in case.events:
        if e["session_id"] == "eps" and e["metric_id"] == "insights.token_outlier":
            e["value"] = 1.0                                 # tampered row; raw data still says ~40x
    events = case.case_events(BY_KEY["anomaly_detection"].metric_keys)
    claim = {"kind": "metric_value", "metric_id": "insights.token_outlier", "asserted_value": 1.0,
             "evidence_refs": ["metric:insights.token_outlier"], "statement": "s"}
    r = res(verify_finding(vctx("anomaly_detection", base(claims=[claim]), case=case)), "MC-01")
    assert r["result"] == "FAIL" and "raw recompute" in r["detail"]


def _upstream_claim(label):
    return {"kind": "upstream_finding", "upstream_cop": "runtime_monitoring", "upstream_label": label,
            "evidence_refs": ["finding:f-rm"], "statement": "telemetry"}


def test_sc01_catches_a_misquoted_upstream_label():
    up = {"runtime_monitoring": {"finding_id": "f-rm", "label": "DEGRADED", "verified": True,
                                 "status": "SUBSTANTIVE", "lineage": []}}
    r = res(verify_finding(vctx("anomaly_detection", base(claims=[_upstream_claim("SUFFICIENT")]), upstream=up)),
            "SC-01")
    assert r["result"] == "FAIL" and "asserted SUFFICIENT, actual DEGRADED" in r["detail"]


def test_sc01_refuses_to_cite_an_unverified_upstream_finding():
    up = {"runtime_monitoring": {"finding_id": "f-rm", "label": "DEGRADED", "verified": False,
                                 "status": "SUBSTANTIVE", "lineage": []}}
    r = res(verify_finding(vctx("anomaly_detection", base(claims=[_upstream_claim("DEGRADED")]), upstream=up)),
            "SC-01")
    assert r["result"] == "FAIL" and "not verified" in r["detail"]


def test_ds01_catches_substantive_label_with_nothing_judgeable():
    case = CaseData("v3", S.TENANT, "no-such-session", NORMAL, synthetic=True)   # every metric MISSING
    r = res(verify_finding(vctx("workflow_orchestration", base(label="STALLED"), case=case)), "DS-01")
    assert r["result"] == "FAIL" and "no judgeable metric" in r["detail"]


def test_pe01_needs_a_policy_store_for_an_intervening_action():
    out = verify_action(BY_KEY["policy_enforcement"], {"action": "BLOCK"}, "supervised", CASE, {})
    pe = res(out, "PE-01")
    assert pe["result"] == "FAIL" and pe["reason_code"] == "no_policy_store"


def test_au02_fails_while_approval_is_outstanding():
    out = verify_action(BY_KEY["policy_enforcement"], {"action": "BLOCK", "requires_approval": True},
                        "supervised", CASE, {})
    assert res(out, "AU-02")["reason_code"] == "awaiting_approval" and res(out, "AU-02")["result"] == "FAIL"


def test_mu01_failure_reasons_when_called_directly():
    def mu(**k):
        a = dict(cop="anomaly_detection", case=CASE, record_type="anomaly_observation", payload={"x": 1},
                 provenance={"case_id": "v", "finding_id": "f", "tenant_id": S.TENANT}, status="VERIFIED",
                 finding_verified=True)
        a.update(k)
        return [r if isinstance(r, dict) else r.to_dict() for r in verify_memory_update(**a)][0]
    assert mu()["result"] == "PASS"
    assert "does not own" in mu(record_type="incident_outcome")["detail"]
    assert "did not verify" in mu(finding_verified=False)["detail"]
    assert "lacks case or finding" in mu(provenance={})["detail"]
    assert "another tenant" in mu(provenance={"case_id": "v", "finding_id": "f", "tenant_id": "tenant-b"})["detail"]


def test_id01_refuses_to_cite_an_invalid_row():
    case = CaseData("v4", S.TENANT, "ep", NORMAL, synthetic=True)
    for e in case.events:
        if e["session_id"] == "ep" and e["metric_id"] == "insights.token_outlier":
            e["source"] = None                               # no provenance: INVALID
    claim = {"kind": "metric_state", "metric_id": "insights.token_outlier", "asserted_state": "NORMAL",
             "evidence_refs": ["metric:insights.token_outlier"], "statement": "s"}
    r = res(verify_finding(vctx("anomaly_detection", base(claims=[claim]), case=case)), "ID-01")
    assert r["result"] == "FAIL" and "is INVALID" in r["detail"]


def test_ds01_refuses_a_quiet_label_on_missing_evidence():
    case = CaseData("v5", S.TENANT, "no-such-session", NORMAL, synthetic=True)
    r = res(verify_finding(vctx("anomaly_detection", base(label="NORMAL"), case=case)), "DS-01")
    assert r["result"] == "FAIL" and "lack of evidence is not normality" in r["detail"]


def test_ds01_refuses_an_attention_label_from_not_applicable_rows_only():
    s = S.Session("na", 9).start().tools(["Read", "Edit"]).full_instrumentation().end()   # no commands ran
    case = CaseData("v6", S.TENANT, "na", E._estate(s), synthetic=True)
    na = [e for e in case.case_events(BY_KEY["behavior_analytics"].metric_keys) if e.state == "NOT_APPLICABLE"]
    assert na, "fixture must yield a NOT_APPLICABLE row"
    claim = {"kind": "metric_state", "metric_id": na[0].metric_id, "asserted_state": "NOT_APPLICABLE",
             "evidence_refs": [f"metric:{na[0].metric_id}"], "statement": "s"}
    r = res(verify_finding(vctx("behavior_analytics", base(label="LOOPING", claims=[claim]), case=case)), "DS-01")
    assert r["result"] == "FAIL" and "NOT_APPLICABLE rows only" in r["detail"]


def test_an_intervention_with_no_policy_basis_is_blocked_before_approval():
    """Supervised and live, Incident Response proposes BLOCK with no policy store:
    PE-01 fails and the action is NOT_EXECUTED_BLOCKED without asking anyone."""
    rows = E._estate(E.sensitive_session("pb", 9))

    def assess_fn(p, n):
        hot = [r["metric"] for r in p["evidence"] if r["evidence"] == "OBSERVED" and r["state"] != "NORMAL"]
        return FX.conclude("secret exposed", "INCIDENT", 0.8, [FX.cm(p, m) for m in hot[:2]], "r", "BLOCK")
    r = E.make_runtime("pb-case", "pb", rows, {}, provider=scripted(lambda p: FX.plan("x", [], [], []), assess_fn),
                       mode="supervised", live=True)
    out = run_cop(r, "incident_response")
    pa = out["proposed_action"]
    assert out["finding"]["verified"], out["finding"].get("rejected_candidate")
    assert pa["original_proposal"]["action"] == "BLOCK" and pa["original_verification"]["result"] == "PASS"
    assert pa["execution_status"] == "NOT_EXECUTED_BLOCKED"
    assert "PE-01 no_policy_store" in pa["authority_reason"]
    assert pa["final_authority"]["authorised"] is False
    assert not out.get("interrupted") and r.enforcement.applied == []
