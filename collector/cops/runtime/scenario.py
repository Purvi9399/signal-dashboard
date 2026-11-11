"""
One integrated sandbox scenario: a coding agent fails the same test repeatedly,
uses far more tokens than comparable sessions, and only one telemetry collector
is reporting (milestone 4).

    coding agent event -> telemetry -> initial COP finding -> VS-01 -> handling
    quotient -> request for support -> another COP investigates -> revised
    evidence score -> final action -> authority check -> action execution ->
    outcome confirmation -> persisted feedback

What runs and what is simulated:
    - the coding agent is a synthetic event generator (synthetic.Session); no agent runs
    - telemetry rows are built from those events by the synthetic estate builder
    - every COP runs through the LangGraph COP subgraph (graph.run_cop); the
      investigation and its referrals run through referrals.investigation_graph
    - Anomaly Detection is an LLM COP: its model replies are scripted
      (fixtures.FixtureProvider, simulated=True). Runtime Monitoring and Behaviour
      Analytics are deterministic and run for real.
    - actions go through gateway.SandboxGateway: a loopback HTTP endpoint, a SQLite
      approval store and a mock tool runner. Nothing leaves the machine.
    - the reviewer is a named test identity answering in code

Two phases. The decide phase is the investigation in dry_run (no effect anywhere).
The act phase runs the same COP once more in supervised mode on the final evidence,
so grade_and_route, authority_check, await_approval and execute_action make the
decision on the revised inputs; it is a separate run with its own decision id.

Variants: successful, contradictory, missing_evidence, timeout, denied_approval.
"""

import copy
import json
import os
from dataclasses import replace
from datetime import datetime, timezone

from langgraph.checkpoint.memory import InMemorySaver

from . import evaluation as E, fixtures as FX, synthetic as S
from . import referrals as RF
from . import gateway as G
from .graph import run_cop, resume_cop
from .outcomes import OutcomeLedger
from .persistence import SQLiteStore, decision_id
from .memory import MemoryGateway

SCENARIO_VERSION = "coding-agent-scenario-1"
VARIANTS = ("successful", "contradictory", "missing_evidence", "timeout", "denied_approval")
COP = "anomaly_detection"
SESSION = "ca1"
REVIEWER = "reviewer@tenant-a.example"
PLAN = {"synthetic": True, "objective": "make tests/test_api.py pass",
        "milestones": [{"id": "M1", "name": "tests pass",
                        "done_signal": {"field": "command", "pattern": "pytest", "require_success": True}}]}


def _iso():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


# ====================================================================== the coding agent

def agent_events(variant):
    """What the (synthetic) coding agent did. The same in every variant except
    missing_evidence, where the tenant has no earlier sessions to compare with."""
    ev = [{"event": "session_start", "session_id": SESSION},
          {"event": "user_prompt", "objective": PLAN["objective"]},
          {"event": "model_usage", "total_tokens": 40000},
          {"event": "tool_call", "tool": "Read"}, {"event": "tool_call", "tool": "Edit"}]
    for i in range(5):
        ev.append({"event": "tool_call", "tool": "Bash", "command": "pytest tests/test_api.py",
                   "tool_use_id": f"{SESSION}-b{i}", "exit_code": 1, "success": False, "duration_ms": 900})
    ev.append({"event": "session_end"})
    return ev


def telemetry(variant, events):
    """Turn the agent's events into telemetry rows, as the one reporting collector
    (otel) saw them. In the contradictory variant the collector lost the failing
    calls and most of the token count; the agent's own events still show them."""
    lost = variant == "contradictory"
    tokens = 1000 if lost else next(e["total_tokens"] for e in events if e["event"] == "model_usage")
    s = S.Session(SESSION, 9, collectors=("otel",))
    s.start(tokens).tools([e["tool"] for e in events if e["event"] == "tool_call" and e["tool"] != "Bash"])
    dropped = []
    for e in events:
        if e["event"] == "tool_call" and e["tool"] == "Bash":
            if lost:
                dropped.append(e["tool_use_id"])
                continue
            s.add(observable_type="tool_request", tool_name="Bash", command=e["command"], exit_code=e["exit_code"],
                  success=e["success"], tool_arguments={"cmd": e["command"]}, tool_use_id=e["tool_use_id"],
                  tool_duration_ms=e["duration_ms"])
    sess = s.end()
    rows = list(sess) if variant == "missing_evidence" else E._estate(sess)
    return rows, {"collectors": ["otel"], "rows_for_session": sum(1 for r in rows if r["session_id"] == SESSION),
                  "baseline_sessions": len({r["session_id"] for r in rows if r["session_id"] != SESSION}),
                  "tokens_reported": tokens, "tool_calls_dropped_by_collector": dropped}


# ====================================================================== scripted model replies (simulated)

def _quiet_steps():
    """The contradictory variant: the model reads what the telemetry shows, which is ordinary."""
    def s0(p):
        return FX.plan("is this session unusual", [], ["deviation from comparable sessions"], [],
                       FX.req("calculate_deviation", "raw tokens against comparable sessions", quantity="tokens"))

    def s1(p):
        claims = [FX.cm(p, m) for m in ("insights.duration_outlier", "activity.action_rate_ratio")
                  if FX.row(p, m) and FX.row(p, m)["evidence"] == "OBSERVED"]
        return FX.conclude("usage within range", "NORMAL", 0.7, claims,
                           "duration and action rate are ordinary", "NONE")
    return [s0, s1]


def _no_baseline_steps():
    """The missing-evidence variant: the deviation tool finds no comparable sessions."""
    def s0(p):
        return FX.plan("token use looks high", [], ["deviation from comparable sessions"], [],
                       FX.req("calculate_deviation", "raw tokens against comparable sessions", quantity="tokens"))

    def s1(p):
        dev = FX.res(p, "calculate_deviation") or {}
        return FX.inconclusive("no comparable sessions to judge against",
                               ["baseline: comparable sessions for this tenant"],
                               f"calculate_deviation: n_baseline={dev.get('n_baseline')}; {dev.get('note', '')}")
    return [s0, s1]


def script_for(variant):
    steps = {"contradictory": _quiet_steps(), "missing_evidence": _no_baseline_steps()}.get(variant,
                                                                                          E.ad_spike_steps())
    # Behaviour Analytics may refer Workflow Orchestration on Anomaly Detection's behalf; WO is an
    # LLM COP too, so its replies are scripted as well
    return FX.Script({COP: steps, "workflow_orchestration": E.wo_stall_steps()}, cycle=True,
                     label=f"scenario:{variant}")


# ====================================================================== trace

class Trace:
    def __init__(self, variant):
        self.variant, self.events = variant, []

    def add(self, stage, **detail):
        self.events.append({"seq": len(self.events) + 1, "at": _iso(), "stage": stage, **detail})


def _vs(snap):
    v = snap["verification_support"]
    return {"decision": v["decision"], "support": v["score"], "against": v["against"], "unknown": v["unknown"],
            "telemetry_reliability": v["telemetry_reliability"], "assumptions": [a if isinstance(a, str) else a.get("item") for a in v["assumptions"]]}


def _timeout_clock():
    """Elapsed time is read on the investigation's clock. Runtime Monitoring's run is
    made to take 1000 s (over the 900 s limit); every other run takes none."""
    t = {"now": 0.0}

    def clock():
        return t["now"]

    def hook(cop, run_key):
        if cop == "runtime_monitoring":
            t["now"] += 1000.0
    return clock, hook


# ====================================================================== the scenario

def run_scenario(variant, workdir, reviewer=REVIEWER):
    assert variant in VARIANTS, variant
    os.makedirs(workdir, exist_ok=True)
    tr = Trace(variant)
    paths = {"cops": os.path.join(workdir, "cops.sqlite"), "gateway": os.path.join(workdir, "gateway.sqlite"),
             "alerts": os.path.join(workdir, "alerts.jsonl")}
    case_id = f"scn-{variant}"
    ep = G.TestAlertEndpoint(paths["alerts"])
    try:
        return _run(variant, workdir, reviewer, tr, paths, case_id, ep)
    finally:
        ep.close()


def _run(variant, workdir, reviewer, tr, paths, case_id, ep):
    # 1. coding agent events and 2. telemetry
    events = agent_events(variant)
    for e in events:
        tr.add("agent_event", source="synthetic coding agent", **e)
    rows, tel = telemetry(variant, events)
    tr.add("telemetry_ingested", **tel)

    store = SQLiteStore(paths["cops"])
    gstore = G.GatewayStore(paths["gateway"])
    gw = G.SandboxGateway(gstore, alert_url=ep.url)
    rt = E.make_runtime(case_id, SESSION, rows, {"task_plan": PLAN}, script_for(variant), store=store,
                        mode="dry_run", enforcement=gw)
    if variant == "denied_approval":
        # tenant policy for this variant: an ALERT from Anomaly Detection needs a person's approval
        p = rt.profiles[COP]
        rt.profiles = {**rt.profiles, COP: replace(p, approval_actions=p.approval_actions + ("ALERT",))}

    # 3-8. decide phase: investigation with referrals (dry_run)
    limits = {}
    if variant == "timeout":
        clock, hook = _timeout_clock()
        limits = {"clock": clock, "run_hook": hook}
    res = RF.run_investigation(rt, COP, **limits)
    inv = res["investigation"]
    first, last = inv["runs"][0], inv["runs"][-1]
    tr.add("initial_finding", cop=COP, run_key=first["run_key"], decision_id=first["decision_id"],
           label=first["label"], status=first["status"], verified=first["verified"],
           simulated_model_replies=True)
    tr.add("vs01_initial", **_vs(first), components=[{k: c[k] for k in ("name", "direction", "contribution")}
                                                     for c in first["verification_support"]["components"]])
    tr.add("handling_quotient_initial", quotient=first["quotient"], tier=first["tier"],
           selected_action=first["selected_action"], routing_reason=first["routing_reason"])
    for e in res["events"]:
        if e["kind"].startswith("referral") or e["kind"].startswith("reassessment"):
            stage = {"referral_requested": "request_for_support", "referral_dispatched": "receiver_investigating",
                     "referral_answered": "receiver_answered"}.get(e["kind"], e["kind"])
            tr.add(stage, investigation_event=e)
    for r in inv["responses"]:
        tr.add("referral_response", receiver=r["receiver"], status=r["status"], admissible=r["admissible"],
               delivered_as=r["delivered_as"], label=(r["finding"] or {}).get("label"),
               checks={c["check_id"]: c["result"] for c in r["checks"]},
               receiver_decision_id=(r.get("receiver_trace") or {}).get("decision_id"),
               graph_nodes=(r.get("receiver_trace") or {}).get("graph_nodes"))
    tr.add("revised_evidence_score", **_vs(last), quotient=last["quotient"], tier=last["tier"],
           selected_action=last["selected_action"], resolution=inv["resolution"]["status"],
           resolution_reason=inv["resolution"]["reason"],
           component_changes=inv["resolution"]["component_changes"])
    final = inv["final"]
    tr.add("investigation_final", tier=final["tier"], routing_reason=final["routing_reason"],
           human_task=final["human_task"], selected_action=final["selected_action"])

    # 9-12. act phase: the same COP on the final evidence, supervised, through the sandbox gateway
    rx = copy.copy(rt)
    rx.mode, rx.live_session, rx.run_id = "supervised", True, f"{rt.run_id}:exec"
    sel = final["selected_action"]
    needs_person = final["tier"] in ("human",) and sel in G.ADAPTED
    if needs_person:
        p = rx.profiles[COP]
        if sel not in p.approval_actions:
            rx.profiles = {**rx.profiles, COP: replace(p, approval_actions=p.approval_actions + (sel,))}
    saver, thread = InMemorySaver(), f"{case_id}:{COP}:exec"
    out = run_cop(rx, COP, upstream=res["upstream"], checkpointer=saver, thread_id=thread)
    f, pa = out["finding"], out["proposed_action"]
    tr.add("final_action", typed_action=pa["typed_action"], triage_selected=pa["triage_selected"],
           requires_approval=bool(pa.get("requires_approval")),
           approval_because=("investigation tier is human: " + (final["routing_reason"] or "")[:160]) if needs_person
           else ("tenant policy: ALERT needs approval" if variant == "denied_approval" else None),
           exec_decision_id=decision_id(rx.run_id, SESSION, COP),
           verification_support=f.get("verification_support", {}).get("decision"),
           support=f.get("verification_support", {}).get("score"))
    tr.add("authority_check", authorised=pa["final_authority"]["authorised"],
           checks=pa["final_authority"]["checks"], authority_reason=pa.get("authority_reason"),
           execution_status=pa["final_authority"]["execution_status"])

    ledger = OutcomeLedger(store, rt.memory)
    gateway_action_id, exec_outcome, review_task = None, None, None
    if out.get("interrupted"):
        intr = out["interrupted"][0]
        tr.add("approval_requested", approval_id=intr["approval_id"], typed_action=intr["typed_action"],
               pending=[r["approval_id"] for r in gstore.where("gateway_approvals") if r["status"] == "PENDING"])
        decision = "deny" if variant == "denied_approval" else "approve"
        note = "planned load test, not an incident" if decision == "deny" else "checked the failing runs"
        gw.decide(intr["approval_id"], decision, reviewer, note)
        tr.add("approval_decided", approval_id=intr["approval_id"], decision=decision, reviewer=reviewer, note=note)
        out = resume_cop(rx, {"decision": "approve" if decision == "approve" else "reject",
                              "reviewer": reviewer, "note": note}, saver, thread)
        f, pa = out["finding"], out["proposed_action"]
    gateway_action_id = (pa.get("execution_outcome") or {}).get("gateway_action_id")
    if gateway_action_id is None:
        gateway_action_id = next((a["action_id"] for a in gstore.where("gateway_actions", "case_id = ?", (case_id,))
                                  if a["typed_action"] != G.HUMAN_REVIEW), None)
    tr.add("action_execution", execution_status=pa["execution_status"], gateway_action_id=gateway_action_id,
           gateway_history=gw.history(gateway_action_id) if gateway_action_id else [],
           endpoint_received=len(ep.read_all()), detail=(pa.get("execution_outcome") or {}).get("detail"))
    approved_by_person = any(e["stage"] == "approval_decided" for e in tr.events)
    if final["human_task"] and not approved_by_person:
        # nothing reached a person through an approval: the escalation goes out as a review task
        rv = gw.open_review_task(case_id, COP, f, final["human_task"])
        review_task = rv["action_id"]
        tr.add("review_task_opened", gateway_action_id=review_task, kind=final["human_task"]["kind"],
               question=final["human_task"]["question"], gateway_history=gw.history(review_task))

    # 13. outcome confirmation: independent read of each target
    ga = gstore.get("gateway_actions", "action_id", gateway_action_id or review_task) \
        if (gateway_action_id or review_task) else None
    state = ga["state"] if ga else None
    exec_outcome = {"VERIFIED": "VERIFIED", "APPROVAL_DENIED": "APPROVAL_DENIED", "FAILED": "FAILED",
                    "REFUSED": "REFUSED", "AWAITING_APPROVAL": "AWAITING_APPROVAL"}.get(state, "NOT_EXECUTED")
    tr.add("outcome_confirmation", gateway_state=state, observed=(ga or {}).get("detail", {}).get("observed"),
           alerts_at_endpoint=[a["alert"]["action_id"] for a in ep.read_all()],
           review_task_state=(gstore.get("gateway_actions", "action_id", review_task) or {}).get("state")
           if review_task else None)

    # 14. persisted feedback
    rec_id = (out.get("result") or {}).get("memory_write", {}).get("record_id") if out.get("result") else None
    ex = ledger.record_execution(case_id, rt.case.tenant_id, COP, f.get("finding_id"),
                                 decision_id(rx.run_id, SESSION, COP), gateway_action_id or review_task or "none",
                                 exec_outcome, investigation_id=inv["investigation_id"],
                                 detail={"gateway_history": gw.history(gateway_action_id) if gateway_action_id else []})
    verdict = {"successful": "CONFIRMED", "timeout": "CONFIRMED", "denied_approval": "REFUTED",
               "contradictory": "REFUTED", "missing_evidence": "CONFIRMED"}[variant]
    why = {"successful": "the failing runs and the token spike are real",
           "timeout": "confirmed after approving the alert",
           "denied_approval": "planned load test, not an incident",
           "contradictory": "the collector dropped the failing runs: the NORMAL reading was wrong",
           "missing_evidence": "a new tenant: no baseline yet"}[variant]
    rv = ledger.record_review(case_id, rt.case.tenant_id, COP, f.get("finding_id"), decision_id(rx.run_id, SESSION, COP),
                              verdict, reviewer, bool(f.get("verified")), memory_record_id=rec_id,
                              investigation_id=inv["investigation_id"], note=why)
    tr.add("feedback_persisted", execution_entry={k: ex[k] for k in ("feedback_id", "outcome",
                                                                     "eligible_for_precedent", "inserted")},
           review_entry={k: rv[k] for k in ("feedback_id", "outcome", "source", "independent",
                                            "eligible_for_precedent", "eligible_for_scoring", "applied_to_memory")},
           ineligible_because=rv["detail"]["ineligible_because"], memory_record_id=rec_id)

    # restart: new stores on the same files; the feedback and the gateway records are still there
    store.conn.close()
    s2 = SQLiteStore(paths["cops"])
    l2 = OutcomeLedger(s2, MemoryGateway(s2, clock=E.CLOCK))
    g2 = G.SandboxGateway(G.GatewayStore(paths["gateway"]))
    mem = [r for r in s2.select("cop_memory_records") if r["record_id"] == rec_id] if rec_id else []
    after = {"ledger": sorted((e["kind"], e["outcome"]) for e in l2.for_case(case_id)),
             "gateway_history": g2.history(gateway_action_id) if gateway_action_id else [],
             "memory_validation_status": mem[0]["validation_status"] if mem else None,
             "scoring_history": [h["feedback_id"] for h in l2.scoring_history(COP)]}
    tr.add("restart_check", **after)
    s2.conn.close()

    summary = {"variant": variant, "case_id": case_id, "version": SCENARIO_VERSION, "simulated": {
                   "coding_agent": "synthetic event generator", "model_replies": "scripted (FixtureProvider)",
                   "action_targets": "local sandbox (loopback endpoint, SQLite, mock runner)",
                   "reviewer": "named test identity answering in code"},
               "initial": {"label": first["label"], **_vs(first), "quotient": first["quotient"],
                           "tier": first["tier"], "action": first["selected_action"]},
               "revised": {"label": last["label"], **_vs(last), "quotient": last["quotient"],
                           "tier": last["tier"], "action": last["selected_action"]},
               "resolution": inv["resolution"]["status"], "final_tier": final["tier"],
               "referrals": [(r["receiver"], r["status"]) for r in inv["responses"]],
               "final_action": pa["typed_action"], "execution_status": pa["execution_status"],
               "gateway_state": state, "review_task": bool(review_task),
               "review": {"outcome": verdict, "eligible_for_scoring": rv["eligible_for_scoring"],
                          "applied_to_memory": rv["applied_to_memory"]},
               "after_restart": after, "spent_usd": res["spent_usd"], "cop_runs": res["runs"] + 1}
    return {"summary": summary, "trace": tr.events}


def write_trace(result, path):
    with open(path, "w") as fh:
        json.dump(result, fh, indent=1, default=str, sort_keys=False)
    return path


def main(argv=None):
    import argparse
    import tempfile
    ap = argparse.ArgumentParser(description="Run the sandbox coding-agent scenario and save each trace as JSON.")
    ap.add_argument("--out", required=True)
    ap.add_argument("--variant", action="append", choices=VARIANTS)
    a = ap.parse_args(argv)
    os.makedirs(a.out, exist_ok=True)
    for v in a.variant or VARIANTS:
        with tempfile.TemporaryDirectory() as d:
            r = run_scenario(v, d)
        write_trace(r, os.path.join(a.out, f"{v}.json"))
        s = r["summary"]
        print(f"{v:17s} {s['initial']['support']} -> {s['revised']['support']}  {s['resolution']:10s} "
              f"{s['final_tier']:10s} {s['final_action']:20s} {s['execution_status']:22s} review "
              f"{s['review']['outcome']} eligible={s['review']['eligible_for_scoring']}")


if __name__ == "__main__":
    main()
