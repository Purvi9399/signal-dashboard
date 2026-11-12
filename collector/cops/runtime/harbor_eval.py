"""
First COPS validation on captured Harbor traces.

    Harbor trial artifacts -> signal_harbor (rows, idempotent store) -> signal_metrics
    -> COP subgraphs (graph.run_cop) for Runtime Monitoring, Behaviour Analytics and
    Anomaly Detection -> hard verifiers and VS-01 -> handling quotient and route ->
    act phase (graph.act_cop, supervised, sandbox gateway) -> comparison with the
    ground truth in experiments/harbor/ground_truth/

    python -m cops.runtime.harbor_eval --jobs ../experiments/harbor/jobs --out <dir>   # from collector/

What is real and what is not:
    - the rows come from real Harbor trials in Docker, run by a scripted, model-free
      agent; no language model chose any command
    - Runtime Monitoring and Behaviour Analytics are deterministic and run for real
    - Anomaly Detection is an LLM COP. No model call is approved, so it runs with
      UnavailableProvider and its investigation stops at llm_unavailable. Nothing is
      scripted in its place.
    - Workflow Orchestration is not run: these trials carry no genuine task plan, and
      writing one for them would be the experimenter's plan, not the agent's
    - the act phase runs supervised through gateway.SandboxGateway (loopback endpoint,
      SQLite approvals). The Harbor session has already ended, so live_session is False.
      No approval is given on anyone's behalf: an approval request stays pending
    - the scoring rules, thresholds and VS-01 are used unchanged

The ground truth is read only after every COP has run, for the comparison.
"""

import argparse
import copy
import json
import os
import sys

from langgraph.checkpoint.memory import InMemorySaver

HERE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import signal_harbor as H                                         # noqa: E402

from . import evaluation as E                                     # noqa: E402
from . import gateway as G                                        # noqa: E402
from .graph import CONSUMES, run_cop, act_cop                     # noqa: E402
from .persistence import SQLiteStore                              # noqa: E402

VERSION = "harbor-cops-validation-1"
TENANT = "harbor-experiment"
COPS = ("runtime_monitoring", "behavior_analytics", "anomaly_detection")
NOT_RUN = {"workflow_orchestration": "no genuine task-plan evidence in these trials"}
CASES = {"h01": "signal/h01-normal-debugging", "h02": "signal/h02-repeated-failure-debugging"}
REPO = os.path.dirname(HERE)
GROUND_TRUTH = os.path.join(REPO, "experiments", "harbor", "ground_truth")


def _ref_list(f):
    out = []
    for c in f.get("claims") or []:
        for r in c.get("evidence_refs") or []:
            if r not in out:
                out.append(r)
    return out


def finding_record(cop, state, acted, gw):
    """Every field purvi asked for, from the decide state and the act phase."""
    f = state["finding"]
    vs = f.get("verification_support") or {}
    tri = state.get("triage") or {}
    rt_ = tri.get("routing") or {}
    hq = tri.get("perceived_handling_quotient") or {}
    pa = (acted or {}).get("proposed_action") or state.get("proposed_action") or {}
    fa = pa.get("final_authority") or {}
    gid = (pa.get("execution_outcome") or {}).get("gateway_action_id")
    return {
        "cop": cop, "classification": state.get("classification") or (state.get("result") or {}).get("classification"),
        "candidate_label": f.get("label"), "status": f.get("status"), "verified": f.get("verified"),
        "rationale": f.get("rationale"),
        "evidence_refs": _ref_list(f),
        "missing_evidence": f.get("missing_evidence"),
        "vs01": {k: vs.get(k) for k in ("decision", "basis", "score", "against", "unknown", "limitations",
                                         "corroborating_sources", "dimensions")},
        "derived_thresholds": vs.get("thresholds"),
        "hard_verifiers": vs.get("hard_checks"),
        "verifier_results": [{k: r.get(k) for k in ("check_id", "result", "reason_code", "effect")}
                             for r in state.get("verifier_results") or []],
        "graded_decision": {"outcome": tri.get("outcome"), "outcome_reason": tri.get("outcome_reason"),
                            "evidence_score": (tri.get("evidence_support") or {}).get("score")},
        "quotient": hq.get("value", hq.get("quotient")),
        "route": {"tier": rt_.get("tier"), "primary_reason": rt_.get("primary_reason"),
                  "next_step": rt_.get("next_step"), "human_needed": rt_.get("human_needed")},
        "typed_action": pa.get("typed_action"),
        "triage_selected": (tri.get("action_selection") or {}).get("selected"),
        "authority": {"authorised": fa.get("authorised", pa.get("authorised")),
                      "tier": pa.get("authority_tier"), "reason": pa.get("authority_reason"),
                      "requires_approval": pa.get("requires_approval"), "checks": fa.get("checks")},
        "execution": {"decide_phase": (state.get("proposed_action") or {}).get("execution_status"),
                      "act_phase": pa.get("execution_status"),
                      "interrupted_for_approval": bool((acted or {}).get("interrupted")),
                      "gateway_action_id": gid, "gateway_history": gw.history(gid) if gid else []},
        "llm": {"invocations": len(state.get("llm_invocations") or []),
                "stop_reason": (f.get("cop_specific") or {}).get("stop_reason")},
    }


def run_case_for(session_id, case_id, rows, workdir):
    os.makedirs(workdir, exist_ok=True)
    store = SQLiteStore(os.path.join(workdir, "cops.sqlite"))
    gstore = G.GatewayStore(os.path.join(workdir, "gateway.sqlite"))
    ep = G.TestAlertEndpoint(os.path.join(workdir, "alerts.jsonl"))
    try:
        gw = G.SandboxGateway(gstore, alert_url=ep.url)
        rt = E.make_runtime(case_id, session_id, rows, {}, None, store=store, mode="dry_run",
                            tenant=TENANT, enforcement=gw)
        rt.case.synthetic = False                        # real Harbor rows, not the synthetic estate
        states, published = {}, {}
        for cop in COPS:                                 # decide phase, in the case graph's order
            up = {k: published[k] for k in CONSUMES[cop] if k in published}
            st = run_cop(rt, cop, upstream=up)
            states[cop], published[cop] = st, st["published"]
        rx = copy.copy(rt)
        rx.mode, rx.live_session, rx.run_id = "supervised", False, f"{rt.run_id}:exec"
        records = []
        for cop in COPS:                                 # act phase on the decided state: no re-run
            acted = act_cop(rx, states[cop], checkpointer=InMemorySaver(), thread_id=f"{case_id}:{cop}:act")
            records.append(finding_record(cop, states[cop], acted, gw))
        case = rt.case
        metrics = [{"metric_id": e["metric_id"], "value": e["value"], "state": e["state"],
                    "evidence_refs": (e.get("evidence_refs") or [])[:6]}
                   for e in case.events if e["session_id"] == session_id]
        return {"records": records, "metrics": metrics,
                "alerts_at_endpoint": len(ep.read_all()),
                "gateway_actions": [{k: a.get(k) for k in ("action_id", "typed_action", "state", "cop")}
                                    for a in gstore.where("gateway_actions")],
                "estate_sessions": len({r["session_id"] for r in rows}),
                "case_rows": sum(1 for r in rows if r["session_id"] == session_id)}
    finally:
        ep.close()


def compare(key, records, report):
    gt = json.load(open(os.path.join(GROUND_TRUTH, f"{key}.json")))
    out = []
    for r in records:
        exp = gt["expected_governance"].get(r["cop"]) or {}
        ok = r["candidate_label"] in exp.get("acceptable", [])
        out.append({"cop": r["cop"], "observed": r["candidate_label"], "acceptable": exp.get("acceptable"),
                    "agrees": ok, "ground_truth_reason": exp.get("why")})
    for cop, why in NOT_RUN.items():
        exp = gt["expected_governance"].get(cop) or {}
        out.append({"cop": cop, "observed": "NOT_RUN", "acceptable": exp.get("acceptable"),
                    "agrees": "NOT_RUN" in (exp.get("acceptable") or []), "ground_truth_reason": why})
    acted = [r for r in records if r["execution"]["gateway_action_id"] or r["execution"]["interrupted_for_approval"]]
    return {"ground_truth_file": f"experiments/harbor/ground_truth/{key}.json",
            "task_outcome": {"verifier_reward": report["outcome"]["reward"],
                             "expected": gt["expected_reward"]["scripted"]},
            "observed_vs_designed": {"commands": [report["normalised_counts"]["tool_request"], gt["scripted_run"]["commands"]],
                                     "failing_commands": [report["failed_commands"], gt["scripted_run"]["failing_commands"]],
                                     "unchanged_state_retries": gt["scripted_run"]["unchanged_state_retries"]},
            "labels": out,
            "intervention": {"warranted": gt["expected_governance"]["intervention_warranted"],
                             "gateway_actions_taken": len(acted)}}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--jobs", required=True, help="experiments/harbor/jobs")
    ap.add_argument("--out", required=True)
    ap.add_argument("--operator", default="dev-a", help="experiment-assigned user (Harbor has none)")
    ap.add_argument("--h01-trial", default="h01-scripted-1")
    ap.add_argument("--h02-trial", default="h02-scripted")
    a = ap.parse_args(argv)
    os.makedirs(a.out, exist_ok=True)
    jobs = [os.path.join(a.jobs, d) for d in ("h01-scripted-1", "h01-scripted", "h02-scripted")]
    store, reports, rejected = H.ingest_paths(jobs, a.operator, H.IngestStore(os.path.join(a.out, "harbor.sqlite")),
                                              tenant=TENANT)
    again = H.ingest_paths(jobs, a.operator, store, tenant=TENANT)[1]      # idempotency, recorded
    rows = store.rows()
    by_task = {}
    for rep in sorted(reports, key=lambda r: r["trial_dir"]):
        by_task.setdefault(rep["task_name"], []).append(rep)
    pick = {"h01": next(r for r in by_task[CASES["h01"]] if r["trial_dir"].startswith(a.h01_trial + "/")),
            "h02": by_task[CASES["h02"]][0]}
    summary = {"version": VERSION, "tenant": TENANT, "operator_assigned": a.operator,
               "estate": {"trials": len(reports), "rejected": rejected, "rows": len(rows),
                          "reingest_inserted": sum(r["ingest"]["inserted"] for r in again),
                          "reingest_duplicates": sum(r["ingest"]["duplicates"] for r in again)},
               "simulation": {"agent": "scripted, model-free (signal-scripted-debug 1.0.0)",
                              "model_calls": 0, "llm_provider": "UnavailableProvider",
                              "gateway": "SandboxGateway (loopback, local SQLite)"},
               "cases": {}}
    for key, rep in pick.items():
        res = run_case_for(rep["session_id"], f"harbor-{key}", rows, os.path.join(a.out, key))
        trace = {"case": key, "trial": rep, **res, "comparison": compare(key, res["records"], rep)}
        with open(os.path.join(a.out, f"{key}_trace.json"), "w") as f:
            json.dump(trace, f, indent=1, default=str)
        summary["cases"][key] = {"trial": rep["trial_name"], "session_id": rep["session_id"],
                                 "labels": [(r["cop"], r["candidate_label"], r["vs01"]["decision"], r["route"]["tier"],
                                             r["typed_action"], r["execution"]["act_phase"]) for r in res["records"]],
                                 "agreement": [(c["cop"], c["agrees"]) for c in trace["comparison"]["labels"]]}
    with open(os.path.join(a.out, "summary.json"), "w") as f:
        json.dump(summary, f, indent=1, default=str)
    print(json.dumps(summary, indent=1, default=str))


if __name__ == "__main__":
    main()
