"""
Local gateway recordings: one action lifecycle per adapted action type, written as JSON.

    python -m cops.runtime.gateway_recordings --out ../docs/cops/evaluation/gateway   # from collector/

The same flows as tests/test_runtime_gateway.py, recorded instead of asserted:

    alert             Anomaly Detection ALERT, delivered to a loopback endpoint and read back
    request_approval  the same ALERT under a tenant policy that needs approval; the named test
                      reviewer approves it in code, then it is delivered
    hold_tool_call    Policy Enforcement with no policy store, while a mock tool call is pending;
                      the hold is approved by the test reviewer and the call stays held

What is real and what is not: the COP graph, authority checks, gateway state machine,
SQLite approval and hold stores and the loopback HTTP endpoint run for real on this
machine. Anomaly Detection's model replies are scripted (FixtureProvider, simulated);
Policy Enforcement is deterministic. The coding agent's tool call is a mock
(MockToolRunner). The reviewer is a fixed test identity answering in code, not a person.
Nothing leaves the machine and no model is called.
"""

import argparse
import json
import os
import tempfile
from dataclasses import replace

from langgraph.checkpoint.memory import InMemorySaver

from . import evaluation as E, fixtures as FX, live_eval as LE
from . import gateway as G
from .bench_cases import DET_CASES
from .decision_record import run_detail
from .graph import run_cop, run_case, resume_cop, resume_case
from .llm import UnavailableProvider
from .persistence import decision_id

VERSION = "gateway-recordings-1"
REVIEWER = "reviewer@tenant-a.example"


def _ad_runtime(gw, case_id, approval_alert=False):
    rt = E.make_runtime(case_id, "sp1", E._estate(E.spike_session("sp1", 9)), {},
                        FX.Script({"anomaly_detection": E.ad_spike_steps()}), mode="supervised", enforcement=gw)
    if approval_alert:
        p = rt.profiles["anomaly_detection"]
        rt.profiles = {**rt.profiles, "anomaly_detection": replace(p, approval_actions=p.approval_actions + ("ALERT",))}
    return rt


def _store_dump(store, case_id):
    acts = [a for a in store.where("gateway_actions") if a["case_id"] == case_id]
    ids = {a["action_id"] for a in acts}
    return {"actions": acts,
            "approvals": [r for r in store.where("gateway_approvals") if r.get("action_id") in ids],
            "holds": [r for r in store.where("gateway_holds") if r.get("action_id") in ids]}


def _record(name, mode_note, rt, cop, final_state, store, ep, events, runner=None):
    did = decision_id(rt.run_id, rt.case.session_id, cop)
    return {"recording": name, "version": VERSION, "case_id": rt.case.case_id, "cop": cop,
            "session_id": rt.case.session_id, "decision_id": did,
            "execution_mode": mode_note,
            "simulated": {"model_replies": "scripted (FixtureProvider)" if cop == "anomaly_detection" else None,
                          "reviewer": "fixed test identity answering in code",
                          "tool_runner": "MockToolRunner" if runner else None,
                          "action_targets": "loopback HTTP endpoint and local SQLite"},
            "events": events, "detail": run_detail(final_state, did, f"{rt.case.case_id}:{cop}"),
            "gateway": _store_dump(store, rt.case.case_id),
            "endpoint_received": ep.read_all()}


def record_alert(tmp):
    ep = G.TestAlertEndpoint(os.path.join(tmp, "alerts.jsonl"))
    store = G.GatewayStore(os.path.join(tmp, "gateway.sqlite"))
    try:
        gw = G.SandboxGateway(store, alert_url=ep.url)
        rt = _ad_runtime(gw, "gw-alert")
        out = run_cop(rt, "anomaly_detection")
        pa = out["proposed_action"]
        ev = [{"step": "decided_and_executed", "typed_action": pa["typed_action"],
               "execution_status": pa["execution_status"],
               "gateway_action_id": (pa.get("execution_outcome") or {}).get("gateway_action_id")}]
        return _record("alert", "scripted simulation (model replies) + real local gateway execution",
                       rt, "anomaly_detection", out, store, ep, ev)
    finally:
        ep.close()


def record_request_approval(tmp):
    ep = G.TestAlertEndpoint(os.path.join(tmp, "alerts.jsonl"))
    store = G.GatewayStore(os.path.join(tmp, "gateway.sqlite"))
    try:
        gw = G.SandboxGateway(store, alert_url=ep.url)
        saver = InMemorySaver()
        rt = _ad_runtime(gw, "gw-approval", approval_alert=True)
        out = run_cop(rt, "anomaly_detection", checkpointer=saver, thread_id="rec-appr")
        intr = out["interrupted"][0]
        ev = [{"step": "approval_requested", "approval_id": intr["approval_id"], "typed_action": intr["typed_action"],
               "pending_before_decision": [r["approval_id"] for r in store.where("gateway_approvals")
                                           if r["status"] == "PENDING"],
               "delivered_while_pending": len(ep.read_all())}]
        gw.decide(intr["approval_id"], "approve", REVIEWER, "checked the spike")
        ev.append({"step": "approval_decided", "approval_id": intr["approval_id"], "decision": "approve",
                   "reviewer": REVIEWER, "reviewer_kind": "fixed test identity"})
        res = resume_cop(rt, {"decision": "approve", "reviewer": REVIEWER}, saver, "rec-appr")
        pa = res["proposed_action"]
        ev.append({"step": "resumed_and_executed", "execution_status": pa["execution_status"],
                   "gateway_action_id": (pa.get("execution_outcome") or {}).get("gateway_action_id")})
        return _record("request_approval", "scripted simulation (model replies) + real local gateway execution",
                       rt, "anomaly_detection", res, store, ep, ev)
    finally:
        ep.close()


def record_hold_tool_call(tmp):
    ep = G.TestAlertEndpoint(os.path.join(tmp, "alerts.jsonl"))
    store = G.GatewayStore(os.path.join(tmp, "gateway.sqlite"))
    try:
        runner = G.MockToolRunner(store)
        gw = G.SandboxGateway(store, alert_url=ep.url, runner=runner)
        case = next(c for c in DET_CASES if c["id"] == "policy_enforcement:missing")
        b = case["build"]("pemiss")
        rt = E.make_runtime("gw-hold", "pemiss", b["rows"], b["art"], provider=UnavailableProvider(),
                            mode="supervised", live=True, enforcement=gw)
        cops = LE.deterministic_upstream(case["cop"])
        ev = [{"step": "agent_tool_call_submitted", "tool_call_id": "tc-write-2", "tool": "Write",
               "target": "/prod/config.yaml", "runner_state":
               runner.submit("tc-write-2", rt.case.session_id, "Write", "write /prod/config.yaml", defer=True),
               "source": "MockToolRunner (mock coding-agent call)"}]
        rt.pending_operations = [{"tool": "Write", "target": "/prod/config.yaml", "tool_call_id": "tc-write-2"}]
        saver = InMemorySaver()
        out = run_case(rt, cops=cops, checkpointer=saver, thread_id="rec-hold")
        intr = out["interrupted"][0]
        ev.append({"step": "hold_requested", "cop": intr["cop"], "typed_action": intr["typed_action"],
                   "approval_id": intr["approval_id"], "runner_state": runner.step("tc-write-2"),
                   "executed": runner.executed("tc-write-2")})
        gw.decide(intr["approval_id"], "approve", REVIEWER, "hold until policy is supplied")
        ev.append({"step": "approval_decided", "approval_id": intr["approval_id"], "decision": "approve",
                   "reviewer": REVIEWER, "reviewer_kind": "fixed test identity"})
        res = resume_case(rt, {"decision": "approve", "reviewer": REVIEWER}, cops=cops, checkpointer=saver,
                          thread_id="rec-hold")
        pe = res["results"]["policy_enforcement"]
        pa = pe["proposed_action"]
        ev.append({"step": "resumed_and_executed", "execution_status": pa["execution_status"],
                   "gateway_action_id": (pa.get("execution_outcome") or {}).get("gateway_action_id"),
                   "runner_state": runner.step("tc-write-2"), "executed": runner.executed("tc-write-2")})
        # run_case returns published results, not the run state: record the result fields it has
        state = {"cop": "policy_enforcement", "case_id": rt.case.case_id, "finding": pe["finding"],
                 "result": pe, "proposed_action": pa, "tool_calls": pe.get("tool_calls"),
                 "llm_invocations": pe.get("llm_invocations"), "verifier_results": pe.get("verifier_results"),
                 "sufficiency": pe.get("sufficiency"), "triage": pe.get("triage"), "mode": pe.get("mode"),
                 "classification": pe.get("classification"), "node_timings": pe.get("node_timings")}
        return _record("hold_tool_call", "deterministic COP + real local gateway execution + mock tool runner",
                       rt, "policy_enforcement", state, store, ep, ev, runner)
    finally:
        ep.close()


RECORDERS = {"alert": record_alert, "request_approval": record_request_approval,
             "hold_tool_call": record_hold_tool_call}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    os.makedirs(a.out, exist_ok=True)
    for name, fn in RECORDERS.items():
        with tempfile.TemporaryDirectory() as d:
            r = fn(d)
        with open(os.path.join(a.out, f"{name}.json"), "w") as f:
            json.dump(r, f, indent=1, default=str)
        hist = [a_["history"] for a_ in r["gateway"]["actions"]]
        print(f"{name:18s} {r['cop']:20s} {[h['state'] for h in hist[0]] if hist else []}")


if __name__ == "__main__":
    main()
