"""Executable COP-to-COP referrals (cops/runtime/referrals.py), milestone 1.

Every test is offline. Runtime Monitoring and Behaviour Analytics are deterministic and
run for real through the LangGraph COP subgraph. Anomaly Detection and Workflow
Orchestration are LLM COPs: their replies are scripted (FixtureProvider, simulated=True),
and each scripted step reads only the payload the gateway sent it."""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "collector"))

import pytest

from cops.runtime import evaluation as E, fixtures as FX, synthetic as S
from cops.runtime import referrals as RF
from cops.runtime.graph import run_cop
from cops.runtime.persistence import SQLiteStore
from cops.specs import CONSUMES

PLAN = {"synthetic": True, "objective": "make tests/test_api.py pass",
        "milestones": [{"id": "M1", "name": "tests pass",
                        "done_signal": {"field": "command", "pattern": "pytest", "require_success": True}}]}
COP_NODES = {"receive_case", "independent_verify", "grade_and_route", "authority_check", "persist_and_publish"}


def coding_session(sid="ca1", collectors=("local_hooks", "otel"), tokens=40000, fails=5):
    s = S.Session(sid, 9, collectors=collectors).start(tokens).tools(["Read", "Edit"])
    for i in range(fails):
        s.add(observable_type="tool_request", tool_name="Bash", command="pytest tests/test_api.py",
              exit_code=1, success=False, tool_arguments={"cmd": "pytest tests/test_api.py"},
              tool_use_id=f"{sid}-b{i}", tool_duration_ms=900)
    return E._estate(s.end())


def runtime(case_id, rows, store=None, pricing=None, **kw):
    rt = E.make_runtime(case_id, "ca1", rows, {"task_plan": PLAN}, FX.Script(
        {"anomaly_detection": E.ad_spike_steps(), "workflow_orchestration": E.wo_stall_steps()}, cycle=True),
        store=store, **kw)
    if pricing is not None:
        rt.llm.pricing = pricing
    return rt


def first_and_last(inv):
    return inv["runs"][0], inv["runs"][-1]


# ================================================================ the working path

def test_anomaly_detection_refers_runtime_monitoring_which_runs_through_langgraph():
    store = SQLiteStore()
    rt = runtime("rf-1", coding_session(collectors=("otel",)), store=store)
    out = RF.run_investigation(rt, "anomaly_detection")
    inv = out["investigation"]
    rm = next(r for r in inv["responses"] if r["receiver"] == "runtime_monitoring")
    # the receiver really executed: its own COP subgraph nodes ran and its decision was persisted
    assert rm["status"] == RF.ANSWERED and rm["admissible"] and rm["delivered_as"] == "evidence"
    assert COP_NODES <= set(rm["receiver_trace"]["graph_nodes"])
    assert store.decisions(decision_id=rm["receiver_trace"]["decision_id"])
    assert rm["finding"]["label"] == "INSUFFICIENT" and rm["finding"]["verified"]
    assert {c["check_id"]: c["result"] for c in rm["checks"]} == {f"RS-0{i}": "PASS" for i in range(1, 6)}
    assert rm["evidence"] and all(e["refs"] for e in rm["evidence"])
    # initial and revised VS-01 are both kept, and differ: telemetry reliability was assumed, now observed
    a, b = first_and_last(inv)
    assert len(inv["runs"]) == 2
    assert a["verification_support"]["assumptions"] == ["telemetry_reliability"]
    assert b["verification_support"]["assumptions"] == []
    assert a["verification_support"]["telemetry_reliability"] == 0.9
    assert b["verification_support"]["telemetry_reliability"] == 0.3
    assert b["verification_support"]["score"] < a["verification_support"]["score"]
    assert round(b["verification_support"]["score"] + b["verification_support"]["against"]
                 + b["verification_support"]["unknown"], 3) == 1.0
    # quotient and routing recomputed by grade_and_route on the second run, not copied
    assert a["quotient"] is not None and b["quotient"] is not None and a["run_key"] != b["run_key"]
    assert b["upstream_used"]["runtime_monitoring"] == {"label": "INSUFFICIENT", "verified": True}
    assert inv["resolution"]["status"] in (RF.CONFIRMED, RF.REVISED)
    assert inv["resolution"]["support_direction"] == "weakened"
    assert any(c["component"] == "deviation_metrics" for c in inv["resolution"]["component_changes"])
    # first and revised decisions are separate rows, not an overwrite
    assert a["decision_id"] != b["decision_id"]
    assert store.decisions(decision_id=a["decision_id"]) and store.decisions(decision_id=b["decision_id"])


def test_referral_request_is_typed_and_complete():
    rt = runtime("rf-2", coding_session(collectors=("otel",)))
    out = RF.run_investigation(rt, "anomaly_detection")
    req_events = [e for e in out["events"] if e["kind"] == "referral_requested"
                  and e["cop"] == "anomaly_detection"]
    assert {e["receiver"] for e in req_events} == {"runtime_monitoring", "behavior_analytics"}
    # rebuild one request exactly as the graph built it and check every field
    a = out["investigation"]["runs"][0]
    tree = RF.Tree(runtime("rf-2b", coding_session(collectors=("otel",))))
    first = run_cop(tree.rt, "anomaly_detection")
    req, _ = RF.refer(tree, "anomaly_detection", "runtime_monitoring", first, reason="telemetry assumed")
    assert req["question"].startswith("Is the telemetry") or "telemetry" in req["question"].lower()
    of = req["original_finding"]
    assert of["label"] == "ANOMALOUS" and set(of["verification_support"]) == {"decision", "score", "against", "unknown"}
    assert of["verification_support"]["score"] == a["verification_support"]["score"]
    assert req["missing_evidence"][0]["item"] == "finding:runtime_monitoring"
    t = req["trace_ids"]
    assert t["finding_id"] and t["initiator_decision_id"] and t["tool_call_ids"] and t["llm_invocation_ids"]
    sc = req["permitted_scope"]
    assert sc["receivers"] == list(CONSUMES["anomaly_detection"]) and sc["may_execute_actions"] is False
    assert sc["mode"] == "dry_run" and sc["tenant_id"] == rt.case.tenant_id and sc["receiver_tools"]
    assert req["chain"] == ["anomaly_detection", "runtime_monitoring"] and req["depth"] == 1


def test_workflow_orchestration_refers_runtime_monitoring():
    rt = runtime("rf-3", coding_session())
    inv = RF.run_investigation(rt, "workflow_orchestration")["investigation"]
    assert [r["receiver"] for r in inv["responses"]] == ["runtime_monitoring"]
    rm = inv["responses"][0]
    assert rm["status"] == RF.ANSWERED
    a, b = first_and_last(inv)
    assert a["label"] == b["label"] == "STALLED"
    assert a["verification_support"]["assumptions"] == ["telemetry_reliability"]
    assert b["verification_support"]["assumptions"] == []
    assert inv["resolution"]["status"] == RF.CONFIRMED


def test_nothing_is_referred_for_a_supported_finding_that_only_records():
    rt = E.make_runtime("rf-4", "n9", E._estate(S.normal_session("n9", 9)), {}, None)
    inv = RF.run_investigation(rt, "behavior_analytics")["investigation"]
    assert inv["responses"] == [] and inv["resolution"]["status"] == RF.NOT_NEEDED


# ================================================================ answers are not promoted

def test_an_answer_that_did_not_verify_is_context_only_and_never_verified():
    # Workflow Orchestration answers Behaviour Analytics with a claim the data contradicts:
    # SC-01 rejects it, so the answer has no finding and is not evidence
    rows = coding_session()
    bad = FX.Script({"workflow_orchestration": [
        lambda p: FX.plan("stalled?", [], [], [], FX.req("get_task_milestones", "objective")),
        lambda p: FX.conclude("stalled", "STALLED", 0.9,
                              [FX.ct(p, "get_task_milestones", "reached", value=True)],   # false: not reached
                              "milestone reached", "ALERT", "x")]}, cycle=True)
    rt = E.make_runtime("rf-5", "ca1", rows, {"task_plan": PLAN}, bad)
    tree = RF.Tree(rt)
    ba = run_cop(rt, "behavior_analytics")
    req, resp = RF.refer(tree, "behavior_analytics", "workflow_orchestration", ba)
    assert resp["status"] == RF.NO_FINDING and not resp["admissible"]
    assert resp["finding"]["verified"] is False
    assert resp["delivered_as"] in ("context_only", "not_delivered")
    if resp.get("_delivered"):
        assert resp["_delivered"]["verified"] is False
    assert "workflow_orchestration" not in tree.answers


def test_deliverable_never_raises_verified():
    pub = {"finding_id": "f", "cop": "runtime_monitoring", "label": "SUFFICIENT", "verified": False}
    out = {"published": pub, "finding": {"verification_support": {"decision": "SUPPORTED"}}}
    assert RF._deliverable(out, True, "r")["verified"] is False
    assert RF._deliverable({**out, "published": {**pub, "verified": True}}, False, "r")["verified"] is False


# ================================================================ limits

def test_cycle_is_refused_and_escalates_without_running_anything():
    rt = runtime("rf-6", coding_session())
    tree = RF.Tree(rt)
    wo = run_cop(rt, "workflow_orchestration")
    runs = tree.runs
    req, resp = RF.refer(tree, "workflow_orchestration", "runtime_monitoring", wo,
                         chain=["runtime_monitoring", "workflow_orchestration"])
    assert resp["status"] == RF.REFUSED_CYCLE and tree.runs == runs
    assert "cycle" in resp["detail"]


def test_scope_and_depth_are_enforced():
    rt = runtime("rf-7", coding_session())
    tree = RF.Tree(rt)
    ad = run_cop(rt, "anomaly_detection")
    _, resp = RF.refer(tree, "anomaly_detection", "workflow_orchestration", ad)   # AD does not consume WO
    assert resp["status"] == RF.REFUSED_SCOPE
    _, resp = RF.refer(tree, "anomaly_detection", "runtime_monitoring", ad, depth=2)
    assert resp["status"] == RF.REFUSED_DEPTH
    # needs() never proposes a COP the initiator does not consume, nor one outside the milestone scope
    nd, skipped = RF.needs("anomaly_detection", ad, {}, set())
    assert {n["receiver"] for n in nd} <= set(CONSUMES["anomaly_detection"]) & set(RF.SCOPE_RECEIVERS)


def test_timeout_discards_the_late_answer_and_routes_to_a_human():
    t = {"now": 0.0}

    def clock():
        t["now"] += 1000.0                # each read is 1000 s later: over the 900 s limit
        return t["now"]
    rt = runtime("rf-8", coding_session(collectors=("otel",)))
    out = RF.run_investigation(rt, "anomaly_detection", clock=clock)
    inv = out["investigation"]
    rm = next(r for r in inv["responses"] if r["receiver"] == "runtime_monitoring")
    assert rm["status"] == RF.TIMED_OUT and not rm["admissible"] and rm["attempts"] == 1
    assert inv["resolution"]["limit_hit"]
    assert inv["final"]["tier"] == "human" and inv["final"]["human_task"]["kind"] == "referral_limit"
    # the late answer changed nothing
    a, b = first_and_last(inv)
    assert b["verification_support"]["telemetry_reliability"] == 0.9 or len(inv["runs"]) == 1


def test_cost_limit_caps_the_receivers_model_budget_and_stops_further_referrals():
    # simulated tokens priced high on purpose, so the scripted model "costs" more than the limit
    rt = runtime("rf-9", coding_session(), pricing={"fixture-model": (2000.0, 2000.0)})
    tree = RF.Tree(rt, cost_limit=0.05)
    ba = run_cop(rt, "behavior_analytics")
    _, resp = RF.refer(tree, "behavior_analytics", "workflow_orchestration", ba)
    assert not resp["admissible"] and resp["status"] == RF.FAILED
    assert resp["attempts"] == 1                              # a spent budget is not retried
    assert any(c["check_id"] == "RS-05" and c["result"] == "FAIL" for c in resp["checks"])
    # the gateway refused the call after the limit (checked before each call: one call may overshoot)
    assert any(e["kind"] == "receiver_error" and "budget_exhausted" in e["error"] for e in tree.events)
    _, resp2 = RF.refer(tree, "behavior_analytics", "runtime_monitoring", ba)
    assert resp2["status"] == RF.REFUSED_BUDGET


def test_a_crashing_receiver_is_retried_once_then_reported():
    calls = []

    def hook(cop, key):
        if cop == "runtime_monitoring":
            calls.append(key)
            if len(calls) == 1:
                raise RuntimeError("collector store unavailable")
    rt = runtime("rf-10", coding_session(collectors=("otel",)))
    inv = RF.run_investigation(rt, "workflow_orchestration", run_hook=hook)["investigation"]
    rm = inv["responses"][0]
    assert rm["status"] == RF.ANSWERED and rm["attempts"] == 2 and len(calls) == 2

    def always(cop, key):
        if cop == "runtime_monitoring":
            raise RuntimeError("down")
    inv = RF.run_investigation(runtime("rf-11", coding_session()), "workflow_orchestration",
                               run_hook=always)["investigation"]
    rm = inv["responses"][0]
    assert rm["status"] == RF.FAILED and rm["attempts"] == 2
    assert inv["resolution"]["status"] == RF.UNRESOLVED and len(inv["runs"]) == 1


def test_a_verified_answer_is_reused_not_re_run_and_no_receiver_is_asked_twice():
    rt = runtime("rf-12", coding_session(collectors=("otel",)))
    out = RF.run_investigation(rt, "anomaly_detection")
    rm_runs = [e for e in out["events"] if e["kind"] == "assessment_started" and e["cop"] == "runtime_monitoring"]
    assert len(rm_runs) == 1
    asked = [e["receiver"] for e in out["events"] if e["kind"] == "referral_requested"]
    assert len(asked) == len(set(asked))
    assert out["runs"] <= RF.MAX_RUNS


def test_answers_that_add_no_admissible_evidence_leave_the_case_unresolved_not_upgraded():
    def always(cop, key):
        if cop in ("runtime_monitoring", "behavior_analytics"):
            raise RuntimeError("down")
    inv = RF.run_investigation(runtime("rf-13", coding_session(collectors=("otel",))), "anomaly_detection",
                               run_hook=always)["investigation"]
    assert inv["resolution"]["status"] == RF.UNRESOLVED
    assert inv["final"]["verification_support"] == inv["runs"][0]["verification_support"]
    assert len(inv["runs"]) == 1


def test_a_receiver_can_refer_onward_once_within_the_depth_limit():
    out = RF.run_investigation(runtime("rf-nest", coding_session(collectors=("otel",))), "anomaly_detection")
    req = [(e["cop"], e["receiver"], e["depth"]) for e in out["events"] if e["kind"] == "referral_requested"]
    assert ("behavior_analytics", "workflow_orchestration", 2) in req      # BA asks WO on AD's behalf
    assert all(d <= RF.MAX_DEPTH for _, _, d in req)
    # runtime_monitoring answered once and was not re-run for the nested investigation
    rm_runs = [e for e in out["events"] if e["kind"] == "referral_dispatched" and e["receiver"] == "runtime_monitoring"]
    assert len(rm_runs) == 1
