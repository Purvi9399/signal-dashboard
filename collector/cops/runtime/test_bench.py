"""
The 18-COP test bench: 54 cases (three per COP) and two fleet scenarios.

    python -m cops.runtime.test_bench                   # from collector/: no model call, no cost
    python -m cops.runtime.test_bench --trace recovery_resilience:abnormal
    python -m cops.runtime.test_bench --trace anomaly_detection:abnormal   # replay of a real reply

What each source of a result is, and how it is labelled in the output:

    deterministic   the 24 cases for the eight rule-based COPs (bench_cases.DET_CASES).
                    Run for real here; no model is called (UnavailableProvider, and
                    any record of a call fails the case).
    live            the 30 LLM cases (live_eval.CASES), from the recorded live run in
                    docs/cops/evaluation/live_results.json: real provider calls. The
                    label, tokens, latency and cost are the run's own.
    replay          the same 30 cases re-run with the recorded real model replies fed
                    back in (FixtureProvider over each invocation's structured_response).
                    Tools, verifiers and authority checks execute again under the current
                    code; the model is not called. Used for the trace, and to show
                    whether a later verifier fix changes the outcome. Simulated.
    fleet           two multi-COP scenarios (bench_cases.FLEET). Offline they run on the
                    generic scripted reply (simulated). With --live-fleet they make real
                    calls for the LLM COPs, under the same preconditions and total cap as
                    live_eval.

Every case is judged on separate dimensions: execution, evidence access, model
investigation (LLM COPs), claim validity, verification, ground truth,
uncertainty handling and authority. A case PASSes when it executed, matched
its independent expected outcome and stayed within its authority.
"""

import argparse, json, os, sys, time, traceback

from ..specs import BY_KEY, topological_order
from . import live_eval as LE
from .bench_cases import DET_CASES, DET_EXPECTED, FLEET
from .evaluation import make_runtime, summarise
from .graph import run_case
from .llm import AnthropicProvider, FixtureProvider, UnavailableProvider
from .persistence import SQLiteStore
from .profiles import LLM_COPS, DETERMINISTIC_COPS
from .diagnosis import diagnose

DOCS = os.path.join(os.path.dirname(__file__), "..", "..", "..", "docs", "cops", "evaluation")
INCONCLUSIVE = "INCONCLUSIVE"
QUIET = LE.QUIET
CLAIM_CHECKS = ("EV-01", "EV-02", "ID-01", "ID-02", "MC-01", "SC-01", "SC-02")
EXECUTED = ("EXECUTED_MOCK", "EXECUTION_FAILED")


# ====================================================================== one investigation

def _structured(i):
    return i.get("structured_response") or {}


def trace_of(cop, r, view_c):
    """What the COP looked at, asked, concluded and was allowed to do."""
    f = r["finding"]
    pa = r["proposed_action"]
    llm = []
    for i in r["llm_invocations"]:
        s = _structured(i)
        fin = s.get("finding") or {}
        llm.append({"purpose": i["purpose"], "simulated": i["simulated"], "model": i["model"],
                    "hypothesis": s.get("hypothesis"), "decision": s.get("decision"),
                    "known": s.get("known"), "unknown": s.get("unknown"),
                    "tool_requests": [{"tool": t.get("tool"), "reason": t.get("reason")}
                                      for t in (s.get("tool_requests") or [])],
                    "candidate_label": fin.get("label"), "rationale": fin.get("rationale"),
                    "missing_evidence": s.get("missing_evidence"),
                    "error": i["error"], "validation": i.get("validation"),
                    "input_tokens": i["input_tokens"], "output_tokens": i["output_tokens"],
                    "latency_ms": i["latency_ms"]})
    return {
        "signals": [{"metric": x["metric"], "state": x["state"], "value": x["value"],
                     "evidence": x["evidence"]} for x in r["evidence"]["rows"]],
        "evidence_requirements": f.get("evidence_requirements", []),
        "missing_evidence": f.get("missing_evidence", []),
        "missing_dependencies": f.get("missing_dependencies", []),
        "upstream_used": f.get("upstream_used", []),
        "tools": [{"tool": t["tool"], "by": t["requested_by"], "status": t["status"],
                   "rejection": t["rejection_reason"]} for t in r["tool_calls"]],
        "llm": llm,
        "interpretation": {"rule_baseline": (f.get("baseline") or {}).get("label") if f.get("baseline") else None,
                           "gated_label": f.get("gated_label"),
                           "rejected_candidate": (f.get("rejected_candidate") or {}).get("label"),
                           "rationale": f.get("rationale"), "reason": f.get("reason")},
        "claims": [{"statement": c.get("statement"), "kind": c.get("kind"),
                    "refs": c.get("evidence_refs", []), "asserted_value": c.get("asserted_value"),
                    "cites": [x for x in (
                        c.get("metric_id") and f"metric:{c['metric_id']}" +
                        (f" = {c.get('asserted_state') or c.get('asserted_value')}"),
                        c.get("tool_call_id") and f"tool_call:{c['tool_call_id'][:8]}.{c.get('result_field')}"
                        f" = {c.get('asserted_value')!r}",
                        c.get("upstream_cop") and f"finding:{c['upstream_cop']} = {c.get('upstream_label')}",
                        c.get("record_id") and f"memory:{c['record_id']}") if x]}
                   for c in f.get("claims", [])],
        "verification": [{"check": x["check_id"], "result": x["result"], "reason": x.get("reason_code"),
                          "detail": str(x.get("detail") or "")[:400], "failures": x.get("failures") or [],
                          "subject": x.get("subject", "")} for x in r["verifier_results"]],
        "sufficiency": r.get("sufficiency"),
        "recovery": r.get("recovery"),
        "reason_codes": f.get("reason_codes", []),
        "tool_results": [{"tool": t["tool"], "by": t["requested_by"], "status": t["status"],
                          "tool_call_id": t["tool_call_id"], "result": _short(t.get("result"))}
                         for t in r.get("_tool_results", [])],
        "authority": {"requested": pa.get("requested_action"), "final": pa.get("action"),
                      "authorised": (pa.get("final_authority") or {}).get("authorised"),
                      "reason": pa.get("authority_reason"), "execution_status": pa.get("execution_status"),
                      "permitted": list(BY_KEY[cop].permitted_actions),
                      "original_proposal": pa.get("original_proposal"),
                      "lifecycle": pa.get("lifecycle")},
        "decision": {"label": f["label"], "status": f["status"], "confidence": f["confidence"],
                     "verified": f["verified"]},
    }


def _short(v, n=900):
    """A tool result small enough to show in a report."""
    s = json.dumps(v, default=str)
    return v if len(s) <= n else s[:n] + " ..."


def dimensions(cop, kind, accept, r, label, outcome, err):
    """Execution, verification and ground truth are judged separately."""
    if err or r is None:
        return {"execution": False, "note": "the run raised"}
    f, pa = r["finding"], r["proposed_action"]
    inv = r["llm_invocations"]
    vr = {x["check_id"]: x["result"] for x in r["verifier_results"]}
    is_llm = cop in LLM_COPS
    d = {}
    prechecked = (f.get("cop_specific") or {}).get("stop_reason") == "precheck_required_evidence_unavailable"
    d["execution"] = bool(label) and f["status"] != "ERROR" and (not is_llm or bool(inv) or prechecked) \
        and (is_llm or not inv)
    d["evidence_accessed"] = bool(r["tool_calls"] or r["evidence"]["rows"])
    if is_llm:
        d["llm_investigation"] = None if prechecked else bool(inv) and all(i["error"] is None for i in inv)
    d["claims_valid"] = all(vr.get(k) != "FAIL" for k in CLAIM_CHECKS)
    d["verified"] = bool(f["verified"]) if label != INCONCLUSIVE else None   # n/a for INCONCLUSIVE
    d["ground_truth"] = outcome == "agree"
    if kind == "missing":
        d["uncertainty"] = label in accept and label != QUIET[cop]
    else:
        d["uncertainty"] = not (label == QUIET[cop] and f.get("missing_evidence"))
    fa = pa.get("final_authority") or {}
    d["authority"] = (pa.get("execution_status") not in EXECUTED
                      and pa.get("action") in BY_KEY[cop].permitted_actions
                      and fa.get("authorised", True) is not False
                      and not any(t["requested_by"] == "llm" and t["status"] == "executed"
                                  and t["rejection_reason"] for t in r["tool_calls"]))
    return d


def inconclusive_by(label, status):
    """Who held back: the COP itself, or a verifier that rejected its candidate."""
    if label != INCONCLUSIVE:
        return None
    return "verifier" if status == "REJECTED_BY_VERIFIER" else "cop"


def verdict(dims, outcome):
    if outcome in ("not_run", None):
        return "UNTESTED"
    if not dims.get("execution") or not dims.get("authority", True):
        return "FAIL"
    if outcome == "agree":
        return "PASS"
    if outcome == "inconclusive":
        return "INCONCLUSIVE"
    return "FAIL"


def run_investigation(case, provider, budget=None, cops=None, prefix="bench"):
    """prefix "live" reproduces the live run's case id, so the tool-call ids a
    recorded reply cites are the ids this run's tool calls get."""
    sid = case["id"].replace(":", "-").replace("_", "")[:24]
    b = case["build"](sid)
    st = b["store"] or SQLiteStore()
    rt = make_runtime(f"{prefix}-{sid}", sid, b["rows"], b["art"], provider=provider, budget=budget,
                      store=st, decision_store=st if b["decision_store"] else None)
    cops = cops or LE.deterministic_upstream(case["cop"])
    t0 = time.perf_counter()
    err = None
    try:
        out = run_case(rt, cops=cops)
    except Exception:
        out, err = {"order": cops, "results": {}}, traceback.format_exc()
    wall = round((time.perf_counter() - t0) * 1000, 1)
    return out, rt, err, wall


def _calls_with_results(rt, cop):
    """Tool calls with their results, from the tool gateway's log (the stored record drops results)."""
    res = getattr(rt.tools, "results", {})
    return [{**c, "result": res.get(c["tool_call_id"])} for c in getattr(rt.tools, "log", [])
            if c.get("cop") == cop]


def record(case, out, rt, err, wall, source):
    cop = case["cop"]
    r = (out.get("results") or {}).get(cop)
    if r is not None:
        r = {**r, "_tool_results": _calls_with_results(rt, cop)}
    label = r["finding"]["label"] if r else None
    inv = r["llm_invocations"] if r else []
    outcome = LE.classify(case, label, inv) if label else "error"
    others = sum(len(v["llm_invocations"]) for k, v in (out.get("results") or {}).items() if k != cop)
    dims = dimensions(cop, case["kind"], case["accept"], r, label, outcome, err)
    if others:
        dims["execution"] = False
        dims["note"] = f"{others} model calls by COPs other than the target"
    return {"id": case["id"], "cop": cop, "kind": case["kind"], "title": case["title"],
            "ground_truth": case["ground_truth"], "expected": list(case["accept"]), "source": source,
            "label": label, "status": r["finding"]["status"] if r else None, "outcome": outcome,
            "inconclusive_by": inconclusive_by(label, r["finding"]["status"] if r else None),
            "verdict": verdict(dims, outcome), "dimensions": dims, "wall_ms": wall, "error": err,
            "cops_run": out.get("order", []),
            "usage": _usage(inv), "trace": trace_of(cop, r, None) if r else None}


def _usage(inv):
    if not inv:
        return None
    return {"calls": len(inv), "simulated": all(i["simulated"] for i in inv),
            "provider": inv[0]["provider"], "model": inv[0]["model"],
            "input_tokens": sum(i["input_tokens"] or 0 for i in inv),
            "output_tokens": sum(i["output_tokens"] or 0 for i in inv),
            "latency_ms": round(sum(i["latency_ms"] or 0 for i in inv), 1),
            "cost_usd": round(sum(i["cost_usd"] or 0 for i in inv), 6) if all(
                i["cost_usd"] is not None for i in inv) else None,
            "request_ids": [i.get("request_id") for i in inv]}


# ====================================================================== the three sources

def run_deterministic():
    out = []
    for case in DET_CASES:
        o, rt, err, wall = run_investigation(case, UnavailableProvider())
        rec = record(case, o, rt, err, wall, "deterministic")
        rec["result_source"] = "deterministic_rerun"
        out.append(rec)
    return out


def load_live(path=None):
    path = path or os.path.join(DOCS, "live_results.json")
    if not os.path.exists(path):
        return None
    d = json.load(open(path))
    return d if d.get("live") else None


def replay_provider(live_rec):
    """Feeds the recorded real replies back, in order. Simulated by construction."""
    replies = [i["structured_response"] for i in live_rec["invocations"]]

    def script(cop, purpose, payload, n):
        if n < len(replies) and replies[n] is not None:
            return replies[n]
        return RuntimeError("replay: no recorded reply for this call")
    return FixtureProvider(script, label="replay-of-live", replay=True)


def live_record(case, lr, replay):
    """The current result of a live case: its recorded real replies replayed under the
    current code (tools, verifiers and authority re-executed; no model call). The live
    run's own label, tokens and cost are kept beside it, unchanged, in live_recorded and
    usage. A replayed reply is never relabelled as a new live call."""
    inv = lr["invocations"]
    usage = _usage(inv)
    live = {"label": lr["label"], "status": lr["status"], "outcome": lr["outcome"],
            "verifier_fail": lr.get("verifier_fail", []), "rejected_candidate": lr.get("rejected_candidate"),
            "wall_ms": lr.get("wall_ms"), "source": "live_results.json (real provider calls, 2026-11-02)"}
    if replay is None:
        return {"id": case["id"], "cop": case["cop"], "kind": case["kind"], "title": case["title"],
                "ground_truth": case["ground_truth"], "expected": list(case["accept"]), "source": "live",
                "result_source": "live_recorded", "label": lr["label"], "status": lr["status"],
                "outcome": lr["outcome"], "inconclusive_by": inconclusive_by(lr["label"], lr["status"]),
                "verdict": verdict({"execution": bool(lr["label"]), "authority": True}, lr["outcome"]),
                "dimensions": {}, "usage": usage, "live_recorded": live, "trace": None}
    rec = dict(replay)
    dims = dict(rec["dimensions"])
    if dims.get("llm_investigation") is not None:
        dims["llm_investigation"] = bool(inv) and all(i["error"] is None and i["simulated"] is False for i in inv)
    rec.update(source="live", result_source="recorded_reply_replay", usage=usage, live_recorded=live,
               dimensions=dims, verdict=verdict(dims, rec["outcome"]),
               current_model_calls={"calls": len((replay.get("trace") or {}).get("llm") or []),
                                    "answered_by": "recorded real replies (no new model call)"},
               replay={"label": replay["label"], "outcome": replay["outcome"], "status": replay["status"],
                       "same_label": replay["label"] == lr["label"]},
               trace_source="replay of the recorded real replies (tools and verifiers re-executed; "
                            "model not called)")
    return rec


def run_llm(live):
    out = []
    by = {r["id"]: r for r in (live or {}).get("results", [])}
    for case in LE.CASES:
        lr = by.get(case["id"])
        if not lr or lr.get("skipped"):
            out.append({"id": case["id"], "cop": case["cop"], "kind": case["kind"], "title": case["title"],
                        "ground_truth": case["ground_truth"], "expected": list(case["accept"]),
                        "source": "live", "label": None, "outcome": "not_run", "verdict": "UNTESTED",
                        "dimensions": {}, "usage": None, "trace": None})
            continue
        o, rt, err, wall = run_investigation(case, replay_provider(lr), LE.LIVE_BUDGET, prefix="live")
        rep = record(case, o, rt, err, wall, "replay")
        out.append(live_record(case, lr, rep))
    return out


# ====================================================================== fleet scenarios

def fleet_checks(sc, per, consumption):
    """Computed from the per-COP records (labels, traces, actions) and the
    consumption log, so a recorded live run can be re-checked without a call."""
    checks = []
    for cop, acc in sc["expect"].items():
        lab = (per.get(cop) or {}).get("label")
        checks.append({"check": f"{cop} reads {' or '.join(acc)}", "passed": lab in acc, "actual": lab})

    def res(k, check):
        return next((v["result"] for v in per[k]["trace"]["verification"] if v["check"] == check), None)
    attention = [k for k, v in per.items() if v["label"] not in (QUIET[k], INCONCLUSIVE)]
    unbacked = [k for k in attention
                if not (per[k]["verified"] and res(k, "EV-02") == "PASS" and res(k, "SC-01") == "PASS")]
    checks.append({"check": "every attention finding is verified, its references resolve (EV-02) and its "
                            "claims match the data they cite (SC-01)", "passed": not unbacked,
                   "actual": unbacked or {k: per[k]["label"] for k in attention}})
    cons = [c for c in consumption if c.get("producer") in per and c.get("consumer") in per]
    checks.append({"check": "downstream COPs consumed upstream findings (correlated, not re-derived)",
                   "passed": bool(cons), "actual": [f"{c['consumer']} <- {c['producer']} ({c.get('label')}, "
                                                    f"verified={c.get('verified')})" for c in cons]})
    dbl = [k for k in per if res(k, "DS-02") == "FAIL"]
    checks.append({"check": "no upstream finding counted twice as independent evidence (DS-02)",
                   "passed": not dbl, "actual": dbl or "none"})
    executed = [k for k, v in per.items() if v["execution_status"] in EXECUTED]
    checks.append({"check": "nothing executed: enforcement stays dry-run", "passed": not executed,
                   "actual": {k: v["execution_status"] for k, v in per.items()}})
    unauth = [k for k, v in per.items() if v["action"] not in BY_KEY[k].permitted_actions
              or v["trace"]["authority"]["authorised"] is False]
    checks.append({"check": "every final action is within the COP's authority", "passed": not unauth,
                   "actual": unauth or "all within authority"})
    # a conflict: one COP intervenes (BLOCK, REQUEST_APPROVAL) on the event while
    # Policy Enforcement, which owns permission, reads it as ALLOWED
    hard = {k: v["action"] for k, v in per.items() if v["action"] in ("BLOCK", "REQUEST_APPROVAL")}
    pe = (per.get("policy_enforcement") or {}).get("label")
    checks.append({"check": "no conflicting enforcement (an intervention on an event Policy Enforcement "
                            "allows)", "passed": not (hard and pe == "ALLOWED"),
                   "actual": {k: v["action"] for k, v in per.items()}})
    return checks


def _per_cop(sc, res):
    return {k: {"label": v["finding"]["label"], "status": v["finding"]["status"],
                "verified": v["finding"]["verified"], "action": v["proposed_action"]["action"],
                "requested_action": v["proposed_action"].get("requested_action"),
                "execution_status": v["proposed_action"]["execution_status"],
                "classification": v["classification"], "usage": _usage(v["llm_invocations"]),
                "expected": sc["expect"].get(k), "trace": trace_of(k, v, None),
                "replies": [i.get("structured_response") for i in v["llm_invocations"]]}
            for k, v in res.items()}


def run_fleet(live=False, cap=None):
    results, spent = [], 0.0
    for sc in FLEET:
        n_llm = sum(1 for c in sc["cops"] if c in LLM_COPS)
        if live and cap is not None and cap - spent < n_llm * LE.LIVE_BUDGET.max_cost_usd:
            results.append({"id": sc["id"], "title": sc["title"], "skipped": f"total cap: ${spent:.4f} of "
                            f"${cap:.2f} spent", "verdict": "UNTESTED"})
            continue
        prov = AnthropicProvider() if live else LE.offline_provider()
        case = {"id": f"fleet:{sc['id']}", "build": sc["build"]}
        o, rt, err, wall = run_investigation(case, prov, LE.LIVE_BUDGET if live else None, cops=sc["cops"])
        res = o.get("results") or {}
        inv = [i for v in res.values() for i in v["llm_invocations"]]
        spent += sum(i["cost_usd"] or 0 for i in inv)
        per = _per_cop(sc, res)
        checks = fleet_checks(sc, per, o.get("consumption", [])) if not err else \
            [{"check": "ran", "passed": False, "actual": err[-400:]}]
        results.append({"id": sc["id"], "title": sc["title"], "event": sc["event"], "cops": sc["cops"],
                        "source": "live" if live else "simulated (scripted replies for LLM COPs)",
                        "per_cop": per, "checks": checks, "error": err, "wall_ms": wall,
                        "consumption": o.get("consumption", []), "usage": _usage(inv),
                        "verdict": "PASS" if not err and all(c["passed"] for c in checks) else "FAIL"})
    return results


# ====================================================================== totals and main

def replay_fleet(rec):
    """Re-run a recorded live fleet scenario on its recorded replies: the trace
    (including the action lifecycle) under the current code, with no model call.
    Labels, usage and request ids stay the live run's own."""
    sc = next(x for x in FLEET if x["id"] == rec["id"])
    replies = {k: v.get("replies") or [] for k, v in rec["per_cop"].items()}

    def script(cop, purpose, payload, n):
        r = replies.get(cop) or []
        return r[n] if n < len(r) and r[n] is not None else RuntimeError("replay: no recorded reply")
    case = {"id": f"fleet:{sc['id']}", "build": sc["build"]}
    o, rt, err, _ = run_investigation(case, FixtureProvider(script, label="replay-of-live", replay=True),
                                      LE.LIVE_BUDGET, cops=sc["cops"])
    if err:
        return rec
    per = _per_cop(sc, o["results"])
    for k, v in rec["per_cop"].items():
        if k in per and per[k]["label"] == v["label"] and (v["classification"] != "llm" or replies.get(k)):
            v.update(trace=per[k]["trace"], action=per[k]["action"],
                     requested_action=per[k]["requested_action"], execution_status=per[k]["execution_status"],
                     trace_source="replay of the recorded real replies" if v["classification"] == "llm" else
                     "deterministic re-run")
        else:
            v["trace_source"] = ("recorded at run time; this run predates reply recording, so it cannot be "
                                 "replayed. Lifecycle derived from the recorded authority fields."
                                 if not replies.get(k) else "recorded at run time (replay label differed)")
            a = v["trace"]["authority"]
            if not a.get("lifecycle"):
                a["lifecycle"] = lifecycle_from_recorded(v, rt.mode)
    return rec


def lifecycle_from_recorded(v, mode):
    """The lifecycle from fields a run recorded before lifecycles existed. The
    candidate action is the verified one: a verified finding had nothing withdrawn."""
    from .actions import lifecycle
    a = v["trace"]["authority"]
    finding = {"status": v["status"], "proposed_action": {"action": a["requested"]}}
    if v["status"] == "REJECTED_BY_VERIFIER":   # the candidate's own action was not recorded then
        finding["rejected_candidate"] = {"proposed_action": {"action": "not recorded"}}
    pa = {"requested_action": a["requested"], "action": a["final"], "execution_status": a["execution_status"],
          "final_authority": {"authorised": a["authorised"]}, "authority_reason": a.get("reason"),
          "original_verification": {"result": "PASS" if a["requested"] == a["final"] else "FAIL"}}
    out = lifecycle(finding, pa, mode)
    out["derived_from_recorded_fields"] = True
    return out


# ---------------------------------------------------------------- action lifecycle demonstrations
# Dry-run never executes, so EXECUTED and FAILED_OR_UNCONFIRMED can only be shown
# in supervised mode. These run Policy Enforcement in supervised mode against the
# MOCK enforcement gateway (simulated: it records, it applies nothing), with a
# scripted human decision. They show the lifecycle states, not a real effect.

LIFECYCLE_DEMOS = [
    {"id": "L1", "title": "Proposed BLOCK awaiting a human decision", "answer": None, "unconfirmed": ()},
    {"id": "L2", "title": "BLOCK approved by a reviewer and applied by the mock gateway",
     "answer": {"decision": "approve", "reviewer": "security-lead@tenant-a.example"}, "unconfirmed": ()},
    {"id": "L3", "title": "BLOCK approved, but the mock gateway cannot confirm the outcome",
     "answer": {"decision": "approve", "reviewer": "security-lead@tenant-a.example"}, "unconfirmed": ("BLOCK",)},
    {"id": "L4", "title": "BLOCK rejected by the reviewer",
     "answer": {"decision": "reject", "reviewer": "security-lead@tenant-a.example"}, "unconfirmed": ()},
]


def run_lifecycle_demos():
    import sqlite3
    from langgraph.checkpoint.sqlite import SqliteSaver
    from .evaluation import _estate, c05_art, sensitive_session
    from .graph import MockEnforcementGateway, resume_case
    out = []
    cops = ["runtime_monitoring", "interaction_discovery", "policy_enforcement"]
    for d in LIFECYCLE_DEMOS:
        rows = _estate(sensitive_session("lc", 9))
        gw = MockEnforcementGateway(unconfirmed=d["unconfirmed"])
        rt = make_runtime(f"lifecycle-{d['id']}", "lc", rows, c05_art(), provider=UnavailableProvider(),
                          mode="supervised", live=True, enforcement=gw)
        saver = SqliteSaver(sqlite3.connect(":memory:", check_same_thread=False))
        o = run_case(rt, cops=cops, checkpointer=saver)
        interrupted = [i["cop"] for i in o.get("interrupted", [])]
        if d["answer"] is not None and interrupted:
            o = resume_case(rt, d["answer"], cops=cops, checkpointer=saver)
        res = (o.get("results") or {}).get("policy_enforcement")
        if res is None:      # still waiting: the graph holds the state at the approval interrupt
            st = saver.get_tuple({"configurable": {"thread_id": rt.case.case_id}})
            pend = (o.get("interrupted") or [{}])[0]
            out.append({"id": d["id"], "title": d["title"], "source": "simulated (mock gateway, supervised mode)",
                        "stage": "AWAITING_APPROVAL", "label": pend.get("finding", {}).get("label"),
                        "action": pend.get("action"), "approval_request": pend,
                        "mock_applied": gw.applied, "trace": None})
            continue
        pa = res["proposed_action"]
        out.append({"id": d["id"], "title": d["title"], "source": "simulated (mock gateway, supervised mode)",
                    "stage": pa["lifecycle"]["stage"], "label": res["finding"]["label"], "action": pa["action"],
                    "execution_status": pa["execution_status"], "approval": pa.get("approval"),
                    "execution_outcome": pa.get("execution_outcome"), "mock_applied": gw.applied,
                    "trace": trace_of("policy_enforcement", res, None)})
    return out


BEFORE_FILE = "test_bench_results_before_fixes.json"


def load_before(out_dir=DOCS):
    """The results as recorded before the root-cause fixes (kept for comparison)."""
    path = os.path.join(out_dir, BEFORE_FILE)
    if not os.path.exists(path):
        return {"cases": {}, "fleet": {}, "file": None}
    d = json.load(open(path))
    keep = ("label", "status", "verdict", "outcome", "source", "inconclusive_by")
    return {"file": BEFORE_FILE, "generated": d.get("generated"),
            "cases": {c["id"]: {k: c.get(k) for k in keep} | {
                "rejected_candidate": ((c.get("trace") or {}).get("interpretation") or {}).get("rejected_candidate"),
                "result_source": "live_recorded" if c.get("source") == "live" else "deterministic_rerun"}
                for c in d.get("cases", [])},
            "fleet": {f["id"]: {"verdict": f.get("verdict"),
                                "labels": {k: v.get("label") for k, v in (f.get("per_cop") or {}).items()}}
                      for f in d.get("fleet", [])}}


def summary(cases, fleet):
    out = {v: sum(1 for c in cases if c["verdict"] == v) for v in ("PASS", "FAIL", "INCONCLUSIVE", "UNTESTED")}
    out["cases"] = len(cases)
    for dim in ("execution", "verified", "ground_truth", "authority"):
        out[f"{dim}_true"] = sum(1 for c in cases if c["dimensions"].get(dim) is True)
    out["real_llm_calls"] = sum(c["usage"]["calls"] for c in cases
                                if c.get("usage") and c["usage"]["simulated"] is False)
    out["real_llm_cost_usd"] = round(sum(c["usage"]["cost_usd"] or 0 for c in cases
                                         if c.get("usage") and c["usage"]["simulated"] is False), 6)
    out["fleet"] = {f["id"]: f["verdict"] for f in fleet}
    out["fleet_real_llm_calls"] = sum(f["usage"]["calls"] for f in fleet
                                      if f.get("usage") and f["usage"]["simulated"] is False)
    out["fleet_real_cost_usd"] = round(sum(f["usage"]["cost_usd"] or 0 for f in fleet
                                           if f.get("usage") and f["usage"]["simulated"] is False), 6)
    per = {}
    for c in cases:
        p = per.setdefault(c["cop"], {"PASS": 0, "FAIL": 0, "INCONCLUSIVE": 0, "UNTESTED": 0, "executed": 0})
        p[c["verdict"]] += 1
        p["executed"] += 1 if c["dimensions"].get("execution") else 0
    out["per_cop"] = per
    stages = {}
    for c in cases:
        st = (((c.get("trace") or {}).get("authority") or {}).get("lifecycle") or {}).get("stage")
        stages[st or "unknown"] = stages.get(st or "unknown", 0) + 1
    out["action_lifecycle"] = stages
    out["correct_inconclusive_forced_by_verifier"] = sorted(
        c["id"] for c in cases if c["verdict"] == "PASS" and c.get("inconclusive_by") == "verifier")
    out["cops_executing_all_three"] = sum(1 for p in per.values() if p["executed"] == 3)
    inc = [c for c in cases if c["label"] == INCONCLUSIVE]
    out["inconclusive_breakdown"] = {
        "expected": sum(1 for c in inc if INCONCLUSIVE in c["expected"]),
        "unexpected": sum(1 for c in inc if INCONCLUSIVE not in c["expected"]),
        "unexpected_by_cause": _count_causes([c for c in inc if INCONCLUSIVE not in c["expected"]]),
        "expected_held_back_by_cop": sum(1 for c in inc if INCONCLUSIVE in c["expected"]
                                         and c.get("inconclusive_by") == "cop"),
        "expected_forced_by_verifier": sum(1 for c in inc if INCONCLUSIVE in c["expected"]
                                           and c.get("inconclusive_by") == "verifier")}
    out["result_sources"] = {k: sum(1 for c in cases if c.get("result_source", "deterministic_rerun") == k)
                             for k in ("deterministic_rerun", "recorded_reply_replay", "live_new_call",
                                       "live_recorded")}
    out["changed_since_before"] = sorted(c["id"] for c in cases if c.get("changed"))
    return out


def _count_causes(cases):
    n = {}
    for c in cases:
        for k in (c.get("diagnosis") or {}).get("root_causes", []):
            n[k] = n.get(k, 0) + 1
    return n


def print_trace(rec):
    t = rec["trace"]
    print(f"\n{rec['id']}  [{rec['source']}]  {rec['title']}")
    print(f"  ground truth: {rec['ground_truth']}  (accepts {', '.join(rec['expected'])})")
    if rec.get("trace_source"):
        print(f"  trace from: {rec['trace_source']}")
    if not t:
        print("  no trace")
        return
    print("  signals:")
    for s in t["signals"]:
        print(f"    {s['metric']:32s} {s['state']:15s} value={s['value']}  ({s['evidence']})")
    for k in ("missing_evidence", "missing_dependencies"):
        if t[k]:
            print(f"  {k.replace('_', ' ')}: {t[k]}")
    print("  tools:")
    for x in t["tools"]:
        print(f"    {x['tool']:34s} by {x['by']:13s} {x['status']}" + (f"  ({x['rejection']})" if x["rejection"] else ""))
    for c in t["llm"]:
        print(f"  model {c['purpose']}: decision={c['decision']} candidate={c['candidate_label']} "
              f"simulated={c['simulated']}")
        if c["hypothesis"]:
            print(f"    hypothesis: {c['hypothesis']}")
        for q in c["tool_requests"]:
            print(f"    asks for {q['tool']}" + (f": {q['reason']}" if q['reason'] else ""))
    i = t["interpretation"]
    print(f"  interpretation: rule baseline={i['rule_baseline']} rejected candidate={i['rejected_candidate']}")
    print(f"    {i['rationale']}")
    print("  claims:")
    for c in t["claims"]:
        print(f"    - {c['statement']}")
        for x in (c["cites"] + c["refs"][:3]):
            print(f"        cites {x}")
    print("  verification: " + ", ".join(f"{v['check']}={v['result']}" for v in t["verification"]))
    for v in t["verification"]:
        if v["result"] == "FAIL":
            print(f"    {v['check']} FAIL {v['reason']}: {v['detail']}")
    a = t["authority"]
    print(f"  authority: requested {a['requested']} -> final {a['final']} (authorised={a['authorised']}), "
          f"{a['execution_status']}" + (f"; {a['reason']}" if a["reason"] else ""))
    d = t["decision"]
    print(f"  decision: {d['label']} ({d['status']}, confidence {d['confidence']}, verified={d['verified']})")
    print(f"  verdict: {rec['verdict']}  outcome: {rec['outcome']}")
    if rec.get("usage"):
        u = rec["usage"]
        print(f"  model usage: {u['calls']} calls, simulated={u['simulated']}, {u['input_tokens']} in / "
              f"{u['output_tokens']} out, {u['latency_ms']} ms, cost {u['cost_usd']}")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--trace", help="print one investigation's trace, e.g. recovery_resilience:abnormal")
    ap.add_argument("--live-fleet", action="store_true", help="real model calls for the fleet scenarios")
    ap.add_argument("--out", default=DOCS)
    a = ap.parse_args(argv)
    live = load_live()
    if a.trace:
        case = next((c for c in DET_CASES + LE.CASES if c["id"] == a.trace), None)
        if not case:
            print("unknown case; ids look like recovery_resilience:abnormal")
            return 2
        if case["cop"] in DETERMINISTIC_COPS:
            o, rt, err, wall = run_investigation(case, UnavailableProvider())
            print_trace(record(case, o, rt, err, wall, "deterministic"))
        else:
            lr = next((r for r in (live or {}).get("results", []) if r["id"] == a.trace), None)
            if not lr:
                print("no live record for this case")
                return 2
            o, rt, err, wall = run_investigation(case, replay_provider(lr), LE.LIVE_BUDGET, prefix="live")
            print_trace(live_record(case, lr, record(case, o, rt, err, wall, "replay")))
        return 0
    cap = None
    if a.live_fleet:
        cap, probs = LE.check_live_config()
        if probs:
            print("live fleet run refused; nothing was called:\n  - " + "\n  - ".join(probs))
            return 2
    cases = run_deterministic() + run_llm(live)
    order = {k: i for i, k in enumerate(topological_order())}
    kinds = {"normal": 0, "abnormal": 1, "missing": 2}
    cases.sort(key=lambda c: (order[c["cop"]], kinds[c["kind"]]))
    prev = None
    fleet_path = os.path.join(a.out, "test_bench_results.json")
    if not a.live_fleet and os.path.exists(fleet_path):
        prev = [f for f in json.load(open(fleet_path)).get("fleet", []) if f.get("source") == "live"]
    fleet = run_fleet(a.live_fleet, cap)
    prev = [replay_fleet(f) for f in prev or []]
    for f in prev or []:                       # re-check a recorded live run with the current checks
        sc = next(x for x in FLEET if x["id"] == f["id"])
        f["checks"] = fleet_checks(sc, f["per_cop"], f.get("consumption", []))
        f["verdict"] = "PASS" if not f.get("error") and all(c["passed"] for c in f["checks"]) else "FAIL"
    if prev:                                   # keep a recorded live fleet run next to the offline one
        # a recorded live run stands for its scenario; the scripted run is only a harness check
        fleet = prev + [f for f in fleet if f["id"] not in {p["id"] for p in prev}]
    before = load_before(a.out)
    for c in cases:
        o = before["cases"].get(c["id"])
        c["original"] = o
        c["changed"] = bool(o) and (o["label"] != c["label"] or o["verdict"] != c["verdict"])
        c["diagnosis"] = diagnose(c)
    for f in fleet:
        o = before["fleet"].get(f["id"])
        f["original"] = o
        for k, v in (f.get("per_cop") or {}).items():
            if v.get("expected") and v["label"] not in v["expected"]:
                v["diagnosis"] = diagnose({"id": f"fleet:{f['id']}", "label": v["label"], "status": v["status"],
                                           "expected": v["expected"], "trace": v.get("trace"),
                                           "verdict": "FAIL" if v["label"] != INCONCLUSIVE else "INCONCLUSIVE",
                                           "outcome": None})
    s = summary(cases, fleet)
    cops = [{"key": k, "name": BY_KEY[k].name, "question": BY_KEY[k].behavioural_question,
             "classification": "llm" if k in LLM_COPS else "deterministic",
             "labels": list(BY_KEY[k].interpretations), "permitted_actions": list(BY_KEY[k].permitted_actions)}
            for k in topological_order()]
    doc = {"generated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
           "live_model": (live or {}).get("model"), "live_generated_from": "live_results.json" if live else None,
           "summary": s, "cops": cops, "cases": cases, "fleet": fleet,
           "lifecycle_demos": run_lifecycle_demos(),
           "expected_det": {k: list(v) for k, v in DET_EXPECTED.items()},
           "expected_llm": LE.EXPECTED_JSON()}
    with open(fleet_path, "w") as fh:
        json.dump(doc, fh, indent=1, default=str)
    print(json.dumps({k: v for k, v in s.items() if k != "per_cop"}, indent=1))
    for c in cases:
        if c["verdict"] != "PASS":
            print(f"  {c['verdict']:12s} {c['id']:42s} got {c['label']} expected {c['expected']} ({c['source']})")
    for f in fleet:
        print(f"  fleet {f['id']} {f['verdict']}: " + "; ".join(
            f"{x['check']}={'ok' if x['passed'] else 'NO ' + str(x['actual'])[:120]}" for x in f.get("checks", [])
            if not x["passed"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
