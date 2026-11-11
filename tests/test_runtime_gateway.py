"""The sandbox action gateway (cops/runtime/gateway.py), milestone 2.

Offline and local: the alert target is a loopback HTTP server started by the test,
approvals and holds live in a SQLite file in tmp_path, and the tool runner is a mock
that never runs anything outside the sandbox. Authority is still decided by the
graph (authority_check) and re-checked by the gateway."""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "collector"))

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from cops.runtime import evaluation as E, fixtures as FX, synthetic as S, live_eval as LE
from cops.runtime.bench_cases import DET_CASES
from cops.runtime.llm import UnavailableProvider
from cops.runtime import gateway as G
from cops.runtime.graph import run_cop, run_case, resume_case, resume_cop

PLAN = {"synthetic": True, "objective": "make tests/test_api.py pass",
        "milestones": [{"id": "M1", "name": "tests pass",
                        "done_signal": {"field": "command", "pattern": "pytest", "require_success": True}}]}


@pytest.fixture
def sandbox(tmp_path):
    ep = G.TestAlertEndpoint(str(tmp_path / "alerts.jsonl"))
    store = G.GatewayStore(str(tmp_path / "gateway.sqlite"))
    runner = G.MockToolRunner(store)
    gw = G.SandboxGateway(store, alert_url=ep.url, runner=runner)
    yield {"ep": ep, "store": store, "runner": runner, "gw": gw, "path": str(tmp_path / "gateway.sqlite")}
    ep.close()


def spike_rows():
    return E._estate(E.spike_session("sp1", 9))


def ad_runtime(gw, mode="supervised", case_id="gw-ad", approval_alert=False):
    rt = E.make_runtime(case_id, "sp1", spike_rows(), {}, FX.Script({"anomaly_detection": E.ad_spike_steps()}),
                        mode=mode, enforcement=gw)
    if approval_alert:
        from dataclasses import replace
        p = rt.profiles["anomaly_detection"]
        rt.profiles = {**rt.profiles, "anomaly_detection": replace(p, approval_actions=p.approval_actions + ("ALERT",))}
    return rt


# ================================================================ ALERT

def test_alert_is_delivered_to_the_test_endpoint_and_verified_by_reading_it_back(sandbox):
    rt = ad_runtime(sandbox["gw"])
    out = run_cop(rt, "anomaly_detection")
    pa = out["proposed_action"]
    assert pa["typed_action"] == "ALERT" and pa["final_authority"]["authorised"]
    assert pa["execution_status"] == "EXECUTED_SANDBOX"
    aid = pa["execution_outcome"]["gateway_action_id"]
    assert sandbox["gw"].history(aid) == ["PROPOSED", "AUTHORISED", "DELIVERED", "APPLIED", "VERIFIED"]
    got = sandbox["ep"].read_all()
    assert len(got) == 1 and got[0]["alert"]["finding_id"] == out["finding"]["finding_id"]
    assert got[0]["alert"]["verification_support"]["decision"] == out["finding"]["verification_support"]["decision"]
    assert any(r["check_id"] == "EX-01" and r["result"] == "PASS" for r in out["verifier_results"])
    assert pa["lifecycle"]["stage"] == "EXECUTED" and pa["lifecycle"]["external_effect"] is False


def test_alert_delivery_failure_is_recorded_not_treated_as_done(sandbox, tmp_path):
    bad = G.TestAlertEndpoint(str(tmp_path / "bad.jsonl"), fail_with=503)
    gw = G.SandboxGateway(sandbox["store"], alert_url=bad.url)
    try:
        out = run_cop(ad_runtime(gw, case_id="gw-ad-fail"), "anomaly_detection")
    finally:
        bad.close()
    pa = out["proposed_action"]
    assert pa["execution_status"] == "EXECUTION_FAILED"
    assert gw.history(pa["execution_outcome"]["gateway_action_id"])[-1] == "FAILED"
    assert out["triage"]["routing"]["tier"] == "human"          # a failed execution becomes a human task
    assert any(r["check_id"] == "EX-01" and r["result"] == "FAIL" for r in out["verifier_results"])


def test_only_loopback_endpoints_are_accepted(sandbox):
    with pytest.raises(PermissionError):
        G._http("POST", "https://example.com/alerts", {"x": 1})


def test_dry_run_delivers_nothing(sandbox):
    out = run_cop(ad_runtime(sandbox["gw"], mode="dry_run", case_id="gw-dry"), "anomaly_detection")
    assert out["proposed_action"]["execution_status"] == "NOT_EXECUTED_DRY_RUN"
    assert sandbox["ep"].read_all() == []


def test_the_gateway_refuses_what_authority_refused(sandbox):
    fa = {"authorised": False, "checks": [{"check_id": "AU-01", "result": "FAIL"}]}
    rec = sandbox["gw"].submit("anomaly_detection", "c", {"finding_id": "f", "verified": True},
                               {"typed_action": "ALERT", "final_authority": fa}, "supervised")
    assert rec["state"] == "REFUSED"
    # and refuses an action the COP may not take, even if a caller claims it was authorised
    rec = sandbox["gw"].submit("anomaly_detection", "c", {"finding_id": "f", "verified": True},
                               {"typed_action": "HOLD_TOOL_CALL", "final_authority": {"authorised": True}},
                               "supervised", live_session=True)
    assert rec["state"] == "REFUSED"


# ================================================================ REQUEST_APPROVAL

def test_approval_request_persists_and_survives_a_restart(sandbox):
    saver = InMemorySaver()
    rt = ad_runtime(sandbox["gw"], approval_alert=True, case_id="gw-appr")
    out = run_cop(rt, "anomaly_detection", checkpointer=saver, thread_id="t-appr")
    intr = out["interrupted"][0]
    assert intr["typed_action"] == "ALERT" and intr["approval_id"]
    # a fresh store on the same file (a restart) still has the pending request
    again = G.GatewayStore(sandbox["path"])
    pend = [r for r in again.where("gateway_approvals") if r["status"] == "PENDING"]
    assert [r["approval_id"] for r in pend] == [intr["approval_id"]]
    assert sandbox["ep"].read_all() == []                         # nothing delivered while pending
    gw2 = G.SandboxGateway(again, alert_url=sandbox["ep"].url)
    with pytest.raises(PermissionError):
        gw2.decide(intr["approval_id"], "approve", "cop:anomaly_detection")   # a COP cannot approve
    gw2.decide(intr["approval_id"], "approve", "reviewer@tenant-a.example", "checked the spike")
    rt.enforcement = gw2
    res = resume_cop(rt, {"decision": "approve", "reviewer": "reviewer@tenant-a.example"}, saver, "t-appr")
    pa = res["proposed_action"]
    assert pa["execution_status"] == "EXECUTED_SANDBOX" and pa["approval"]["decision"] == "approve"
    aid = pa["execution_outcome"]["gateway_action_id"]
    assert gw2.history(aid) == ["PROPOSED", "AUTHORISED", "AWAITING_APPROVAL", "AUTHORISED", "DELIVERED",
                                "APPLIED", "VERIFIED"]
    assert len(sandbox["ep"].read_all()) == 1


def test_a_denied_approval_applies_nothing(sandbox):
    saver = InMemorySaver()
    rt = ad_runtime(sandbox["gw"], approval_alert=True, case_id="gw-deny")
    out = run_cop(rt, "anomaly_detection", checkpointer=saver, thread_id="t-deny")
    apid = out["interrupted"][0]["approval_id"]
    sandbox["gw"].decide(apid, "deny", "reviewer@tenant-a.example", "expected load test")
    res = resume_cop(rt, {"decision": "reject", "reviewer": "reviewer@tenant-a.example"}, saver, "t-deny")
    assert res["proposed_action"]["execution_status"] == "APPROVAL_REJECTED"
    assert sandbox["ep"].read_all() == []
    act = [a for a in sandbox["store"].where("gateway_actions") if a["case_id"] == "gw-deny"][0]
    assert act["state"] == "APPROVAL_DENIED"


def test_graph_approval_without_the_gateways_own_decision_does_not_execute(sandbox):
    # the graph is resumed with "approve" but nobody decided the gateway's record: nothing is carried out
    saver = InMemorySaver()
    rt = ad_runtime(sandbox["gw"], approval_alert=True, case_id="gw-nodecide")
    run_cop(rt, "anomaly_detection", checkpointer=saver, thread_id="t-nd")
    res = resume_cop(rt, {"decision": "approve", "reviewer": "someone"}, saver, "t-nd")
    assert res["proposed_action"]["execution_status"] == "EXECUTION_FAILED"
    assert sandbox["ep"].read_all() == []


def test_expired_approval_is_a_denial(sandbox):
    from datetime import datetime, timedelta, timezone
    t = {"now": datetime(2026, 11, 11, 10, tzinfo=timezone.utc)}
    gw = G.SandboxGateway(sandbox["store"], alert_url=sandbox["ep"].url, clock=lambda: t["now"],
                          approval_ttl_s=60)
    rec = gw.submit("anomaly_detection", "c-exp", {"finding_id": "f", "verified": True, "label": "ANOMALOUS"},
                    {"typed_action": "ALERT", "final_authority": {"authorised": True}, "requires_approval": True},
                    "supervised")
    rec = gw.carry_out(rec["action_id"], {"label": "ANOMALOUS"}, {"requires_approval": True, "reason": "x"})
    assert rec["state"] == "AWAITING_APPROVAL"
    t["now"] += timedelta(seconds=120)
    assert gw.expire() == [gw.approval_id(rec["action_id"])]
    assert gw.history(rec["action_id"])[-1] == "APPROVAL_DENIED"


# ================================================================ HOLD_TOOL_CALL

def _pe_missing(gw, case_id):
    case = next(c for c in DET_CASES if c["id"] == "policy_enforcement:missing")
    b = case["build"]("pemiss")
    rt = E.make_runtime(case_id, "pemiss", b["rows"], b["art"], provider=UnavailableProvider(),
                        mode="supervised", live=True, enforcement=gw)
    return rt, LE.deterministic_upstream(case["cop"])


def test_hold_keeps_a_mock_tool_call_from_running_until_released(sandbox):
    gw, runner = sandbox["gw"], sandbox["runner"]
    saver = InMemorySaver()
    rt, cops = _pe_missing(gw, "gw-hold")
    # the coding agent's next call is submitted while governance is deciding
    assert runner.submit("tc-write-2", rt.case.session_id, "Write", "write /prod/config.yaml",
                         defer=True) == "SUBMITTED"
    rt.pending_operations = [{"tool": "Write", "target": "/prod/config.yaml", "tool_call_id": "tc-write-2"}]
    out = run_case(rt, cops=cops, checkpointer=saver, thread_id="t-hold")
    intr = out["interrupted"][0]
    assert intr["cop"] == "policy_enforcement" and intr["typed_action"] == "HOLD_TOOL_CALL"
    assert runner.step("tc-write-2") == "WAITING_FOR_DECISION"     # fail closed while the hold is decided
    assert not runner.executed("tc-write-2")
    gw.decide(intr["approval_id"], "approve", "reviewer@tenant-a.example", "hold until policy is supplied")
    res = resume_case(rt, {"decision": "approve", "reviewer": "reviewer@tenant-a.example"}, cops=cops,
                      checkpointer=saver, thread_id="t-hold")
    pa = res["results"]["policy_enforcement"]["proposed_action"]
    assert pa["execution_status"] == "EXECUTED_SANDBOX"
    aid = pa["execution_outcome"]["gateway_action_id"]
    assert gw.history(aid) == ["PROPOSED", "AUTHORISED", "AWAITING_APPROVAL", "AUTHORISED", "DELIVERED",
                               "APPLIED", "VERIFIED"]
    assert runner.step("tc-write-2") == "HELD" and not runner.executed("tc-write-2")
    gw.end_hold(aid, "release", "reviewer@tenant-a.example", "policy supplied: allowed")
    assert runner.executed("tc-write-2") and gw.history(aid)[-1] == "RELEASED"


def test_a_denied_hold_never_lets_the_call_run(sandbox):
    gw, runner = sandbox["gw"], sandbox["runner"]
    rec = gw.submit("policy_enforcement", "c-h", {"finding_id": "f", "verified": False},
                    {"typed_action": "HOLD_TOOL_CALL", "final_authority": {"authorised": True}},
                    "supervised", live_session=True)
    runner.submit("tc-9", "s", "Bash", "rm -rf build", defer=True)
    rec = gw.apply_hold(rec["action_id"], "tc-9")
    assert rec["state"] == "VERIFIED" and runner.step("tc-9") == "HELD"
    with pytest.raises(PermissionError):
        gw.end_hold(rec["action_id"], "deny", "system")
    gw.end_hold(rec["action_id"], "deny", "reviewer@tenant-a.example", "destructive")
    assert runner.step("tc-9") == "CANCELLED" and not runner.executed("tc-9")
    assert gw.history(rec["action_id"])[-1] == "CANCELLED"


def test_a_hold_cannot_apply_to_a_call_that_already_ran(sandbox):
    gw, runner = sandbox["gw"], sandbox["runner"]
    assert runner.submit("tc-ran", "s", "Bash", "ls") == "EXECUTED"
    rec = gw.submit("policy_enforcement", "c-r", {"finding_id": "f"},
                    {"typed_action": "HOLD_TOOL_CALL", "final_authority": {"authorised": True}},
                    "supervised", live_session=True)
    assert gw.apply_hold(rec["action_id"], "tc-ran")["state"] == "FAILED"
