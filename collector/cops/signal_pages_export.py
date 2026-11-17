"""
Data for the two COPS pages inside the Signal Dashboard app (src/pages/CopsNetwork.jsx and
src/pages/CopsDecisions.jsx).

    python3 -m cops.signal_pages_export --out ../src/data/cops        # from collector/

Writes
    network.json     Page 1: 26 calculated metrics plus the declared inputs of the three COPs that
                     have no calculated metric, the 18 COPs in specification order, and every
                     metric-to-COP connection (contract signal-cops-network/1)
    decisions.json   Page 2: every recorded case grouped by source, and for every recorded COP
                     decision the answer to each of the 13 questions, taken from the stored
                     run record (contract signal-cops-decisions/1)

This module reuses cops.dashboard_export: the network, the decisions of the bench, scenario,
gateway and Harbor recordings, and the per-decision record builder. It adds only:
    - the declared inputs of context_memory, governance_intelligence and knowledge_policy_management
      (specs.py missing_metrics), whose details are MISSING with the reason NO_CALC
    - the five activation-router cases (docs/cops/evaluation/activation). Their stored traces keep
      only each COP's outcome, so the cases are re-run once, deterministically, dry-run, with no
      model and no database; the re-run is accepted only if every activation record id matches the
      stored trace, and it is labelled as a re-run
    - the 13-question explanation of each decision

Where a stored record has no value, the field is {"missing": "<one-line reason>"}. No value is
written by hand and no explanation is generated.
"""

import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))          # collector/
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from cops import dashboard_export as DX                                      # noqa: E402
from cops import specs as SP                                                 # noqa: E402
from cops.runtime import activation as A                                     # noqa: E402
from cops.runtime import activation_scenario as AS                           # noqa: E402
from cops.runtime import router as R                                         # noqa: E402
from cops.runtime.decision_record import run_detail                         # noqa: E402

NETWORK_CONTRACT = "signal-cops-network/1"
DECISIONS_CONTRACT = "signal-cops-decisions/1"
NO_CALC = "no calculation recorded in tests"        # wording chosen by the project owner, 2026-11-17
EMPTY_COPS = ("context_memory", "governance_intelligence", "knowledge_policy_management")


def M(reason):
    return {"missing": reason}


def _used_in(x):
    """Metrics a decision actually used: the export's exercised rule (dashboard_export.EXERCISED_RULE)."""
    inv = x.get("investigation") or {}
    return {m["metric"] for m in inv.get("metrics_inspected") or []
            if m.get("evidence") == "OBSERVED" or m.get("sufficiency") in ("RETRIEVED_AND_VALIDATED",
                                                                            "PRESENT_BUT_UNVERIFIED")}


# ====================================================================== page 1

def build_network(net, decisions):
    by_spec = SP.BY_KEY
    order = [c["id"] for c in net["cops"]]                       # specs.py order = the owner's 1-18 list
    cop_name = {c["id"]: c["name"] for c in net["cops"]}
    cop_class = {c["id"]: c["classification"] for c in net["cops"]}
    rule_edges = {(e.cop, e.metric_id): e for e in A.cop_metric_edges().values()}
    doc_edges = {(e["cop_id"], e["metric_id"]): e for e in net["metric_edges"]}
    used = {}
    for x in decisions:
        for m in _used_in(x):
            used.setdefault((x["cop"], m), []).append(x["decision_id"])

    metrics = []
    for m in net["metrics"]:
        metrics.append({
            "id": m["id"], "name": m["name"], "family": m["category"], "declared_only": False,
            "calculation": {"function": m["computed_by"], "code": m["code_ref"],
                            "applies_when": m["applies_when"], "basis": m["basis"]},
            "raw_fields": m["required_fields"],
            "collector_source": m["collector_source"],
            "thresholds": m["thresholds"] or M("no thresholds in metrics_config.json"),
        })
    known = {m["id"] for m in metrics}

    cops, edges = [], []
    for i, cid in enumerate(order, 1):
        spec = by_spec[cid]
        if spec.metric_keys:
            mids, declared = list(spec.metric_keys), False
        else:
            mids, declared = list(spec.missing_metrics), True
        cops.append({"id": cid, "name": cop_name[cid], "order": i, "question": spec.behavioural_question,
                     "metrics": mids, "source": f"collector/cops/specs.py {cid} "
                                                + ("missing_metrics" if declared else "metric_keys")})
        for mid in mids:
            if declared:
                if mid not in known:
                    metrics.append({"id": mid, "name": mid, "family": cop_name[cid], "declared_only": True,
                                    "calculation": M(NO_CALC), "raw_fields": M(NO_CALC),
                                    "collector_source": M(NO_CALC), "thresholds": M(NO_CALC)})
                    known.add(mid)
                edges.append({"metric_id": mid, "cop_id": cid,
                              "meaning": M("no meaning recorded under this name"),
                              "interpretation": M(NO_CALC), "implemented": M(NO_CALC), "used": M(NO_CALC)})
                continue
            de = doc_edges.get((cid, mid)) or {}
            why = de.get("why_relevant") or {}
            re_ = rule_edges.get((cid, mid))
            ids = sorted(set(used.get((cid, mid), [])))
            edges.append({
                "metric_id": mid, "cop_id": cid,
                "meaning": ({"text": why["text"], "source": why.get("source"), "status": why.get("status"),
                             "context_changes": why.get("context_changes") or []}
                            if why.get("text") else M("no documented meaning in docs/cops/cops_topology.json")),
                "interpretation": ({"rule": re_.interpretation_rule, "where": re_.interpretation_ref,
                                    "role": re_.role}
                                   if re_ and re_.interpretation_status == "DEFINED"
                                   else M("no rule in collector/cops/runtime/rules.py reads this metric for this "
                                          "COP" + ("; the metric is given to the model" if cop_class[cid] == "llm"
                                                   else ""))),
                "implemented": {"value": "yes", "basis": "listed in specs.py metric_keys, so the COP receives it",
                                "source": "collector/cops/specs.py"},
                "used": {"count": len(ids), "decision_ids": ids, "rule": DX.EXERCISED_RULE.split(";")[0]},
            })
    fam_order = []
    for m in metrics:
        if m["family"] not in fam_order:
            fam_order.append(m["family"])
    return {"contract": NETWORK_CONTRACT, "cops": cops, "metrics": metrics, "edges": edges,
            "families": fam_order,
            "counts": {"cops": len(cops), "metrics": len(metrics), "edges": len(edges),
                       "calculated_metrics": sum(1 for m in metrics if not m["declared_only"])}}


# ====================================================================== activation re-run

def activation_decisions(cfg):
    """Re-run each recorded activation case once and accept it only if it reproduces the stored trace."""
    out, files = [], []
    reg = AS.approved_registry()
    for name in AS.VARIANTS:
        path = os.path.join(DX.ACTIVATION_DIR, f"{name}.json")
        if not os.path.exists(path):
            continue
        stored = DX.load(path)
        files.append(path)
        rt = R.Router(reg, AS.factory, version=stored["mapping_version"])
        res, _, _ = AS.run_variant(name, registry=reg, router=rt)
        same = [r.record_id for r in res["records"]] == [r["record_id"] for r in stored["records"]]
        if not same:
            raise RuntimeError(f"activation re-run of {name} does not reproduce {DX.rel(path)}")
        recs = stored["records"]
        for i, r in enumerate(recs):
            if r["transition"] != A.COMPLETED:
                continue
            state = rt.states[(res["case_id"], r["cop"])]
            if (state.get("finding") or {}).get("label") != dict(r.get("detail") or {}).get("label"):
                raise RuntimeError(f"re-run state of {r['cop']} in {name} does not match the stored outcome")
            det = run_detail(state, r["decision_id"], f"activation:{name}:{r['cop']}")
            elig = next(e for e in recs if e["cop"] == r["cop"] and e["transition"] in (A.ELIGIBLE, A.UNKNOWN))
            j = recs.index(elig)
            trig = {"id": elig["record_id"], "kind": "activation_policy",
                    "description": f"router verdict {elig['transition']} under policy {elig['policy_id']}",
                    "condition": "; ".join(elig["reasons"]),
                    "observed": [{"metric": e["metric"], "state": e["state"], "value": e["value"]}
                                 for e in elig["evidence"]],
                    "source": DX.src(path, f"records[{j}]")}
            llm = det.get("classification") == "llm"
            rec = DX._detail_decision(
                det, "activation", path, f"records[{i}]",
                "deterministic re-run of recorded activation case (synthetic session, dry run)"
                + ("; no model: LLM COP stopped at llm_unavailable" if llm else ""),
                [stored["session_id"]], trig, {"value": r["at"], "kind": "activation record time"},
                extra_source={"scenario": name, "case_id": stored["case_id"], "rerun_matches_stored_trace": True},
                cfg=cfg)
            consult = dict(r.get("detail") or {}).get("consultation")
            rec["activation"] = {"consultation": json.loads(consult) if consult else None,
                                 "mapping_version": stored["mapping_version"]}
            out.append(rec)
    return out, files


# ====================================================================== page 2

GROUPS = [
    ("bench", "Bench", "Test-bench case: synthetic session built for one COP"),
    ("scenario", "Scenario", "Sandbox scenario: synthetic coding-agent session, scripted model replies"),
    ("harbor", "Harbor", "Harbor trial: real container run by a scripted, model-free agent"),
    ("gateway_recording", "Gateway", "Gateway recording: real local sandbox gateway execution"),
    ("activation", "Activation", "Activation-router case: synthetic session, dry run, re-run from the recorded case"),
]

EXEC_MEANING = {
    "NOT_EXECUTED_DRY_RUN": "not executed: proposed only (dry run)",
    "NOT_EXECUTED_NO_ACTION": "not executed: no action proposed",
    "EXECUTED_SANDBOX": "executed in the local sandbox gateway only, not in production",
    "APPROVAL_REJECTED": "blocked: the approval was rejected",
}


def _case_key(x):
    s = x["source"]
    k = s["kind"]
    if k == "bench":
        return f"bench:{s['case']}", s["case"]
    if k == "scenario":
        return f"scenario:{s['variant']}", s["variant"]
    if k == "harbor":
        return f"harbor:{s['case']}", f"{s['case']} · {s.get('trial')}"
    if k == "gateway_recording":
        return f"gateway:{s['recording']}", s["recording"]
    return f"activation:{s['scenario']}", s["scenario"]


def _mode_text(x, modes):
    m = x["execution_mode"]
    base = modes.get(m) or m
    note = (x.get("mode_detail") or {}).get("note")
    return base + (f" ({note})" if note and note not in base else "")


def _values(x):
    inv = x.get("investigation") or {}
    trig = x.get("trigger") or {}
    alt = {s["metric"]: s for s in (trig.get("all_signals") or []) + (trig.get("observed") or [])
           if isinstance(s, dict) and s.get("metric")}
    rows = []
    for m in inv.get("metrics_inspected") or []:
        a = alt.get(m["metric"], {})
        st = m.get("state") if m.get("state") is not None else a.get("state")
        val = m.get("value") if m.get("value") is not None else a.get("value")
        rows.append({"metric": m["metric"],
                     "state": st if st is not None else M("state not stored in this run record"),
                     "value": val if val is not None else M("value not stored in this run record"),
                     "evidence": m.get("evidence") or m.get("sufficiency")})
    return rows


def _check(v):
    return {"check": v.get("check_id") or v.get("check"), "result": v.get("result"),
            "reason": v.get("reason_code") or v.get("reason"), "detail": v.get("detail") or None}


def explain(x, modes):
    inv, det, f, act = x.get("investigation") or {}, x.get("determination") or {}, x.get("finding") or {}, \
        x.get("action") or {}
    cls = (x.get("mode_detail") or {}).get("classification")
    trig = x.get("trigger")
    q = {}
    q[1] = {"cop": x["cop"], "decision_id": x["decision_id"], "run": x["source"].get("run_key"),
            "how_it_ran": _mode_text(x, modes), "sessions": x["session_ids"],
            "time": (x.get("timestamp") or {}).get("value") or M("no time recorded for this decision"),
            "record": DX.rel(x["source"]["file"]) if os.path.isabs(x["source"]["file"]) else x["source"]["file"],
            "pointer": x["source"]["pointer"]}
    q[2] = ({"kind": trig.get("kind"), "description": trig.get("description"),
             "condition": trig.get("condition") or M("no activation condition recorded"),
             "metrics_at_activation": [s for s in trig.get("observed") or [] if isinstance(s, dict)]}
            if trig else M("no trigger recorded for this decision"))
    q[3] = {"metrics": [{"metric": m["metric"], "evidence": m.get("evidence") or m.get("sufficiency")}
                        for m in inv.get("metrics_inspected") or []] or M("no metric in this run record"),
            "records": inv.get("records") or [],
            "other_cops_findings": inv.get("cops_consulted") or [],
            "not_available": inv.get("unavailable") or []}
    q[4] = _values(x) or M("no metric values in this run record")
    steps = inv.get("llm_steps") or []
    if steps:
        q[5] = {"model_steps": [{"purpose": s.get("purpose"), "model": s.get("model"), "provider": s.get("provider"),
                                 "simulated": s.get("simulated"), "text": s.get("hypothesis") or s.get("decision")}
                                for s in steps],
                "evidence_requested": inv.get("evidence_requested") or []}
    elif cls == "llm":
        q[5] = M("no model investigation recorded: no model was available in this run (llm_unavailable)")
    else:
        q[5] = {"model_steps": [], "note": "deterministic COP: it runs its rule and tools, with no model steps",
                "evidence_requested": inv.get("evidence_requested") or []}
    q[6] = {"tools": [{k: t.get(k) for k in ("tool", "by", "status", "args", "result")}
                      for t in inv.get("tools") or []],
            "rule_baseline": f.get("baseline") or M("no rule baseline recorded for this decision")}
    refs = [a for a in x.get("resolution_attempts") or [] if a.get("kind") == "cop_referral"]
    cons = (x.get("activation") or {}).get("consultation")
    q[7] = {"referrals": [{k: a.get(k) for k in ("receiver", "status", "label", "delivered_as", "reason", "checks")}
                          for a in refs],
            "activation_consultation": cons,
            "consulted": bool(refs or (cons and cons.get("responses")))}
    q[8] = {k: f.get(k) for k in ("label", "status", "confidence", "verified", "severity")}
    sup = det.get("support") or {}
    q[9] = {"rationale": f.get("rationale") or M("no rationale recorded"),
            "rationale_origin": ("model text: " + _mode_text(x, modes)) if steps else "rule output",
            "support": {"decision": sup.get("decision"), "basis": sup.get("basis"),
                        "limitations": sup.get("limitations") or []} if sup else M("no support assessment recorded"),
            "graded": det.get("graded"),
            "claims": [c.get("statement") for c in det.get("claims") or []]}
    checks = [_check(v) for v in det.get("verifier_results") or []]
    q[10] = {"checks": checks, "passed": sum(1 for c in checks if c["result"] == "PASS"), "total": len(checks)} \
        if checks else M("no verifier results recorded")
    prop, sel, fin, route = act.get("proposed") or {}, act.get("selected") or {}, act.get("final_action") or {}, \
        act.get("route") or {}
    q[11] = {"proposed": prop.get("action"), "reason": prop.get("reason"), "authority_tier": prop.get("authority_tier"),
             "selected": sel.get("typed_action"), "final": fin.get("typed_action"),
             "route": route.get("tier"), "human_needed": route.get("human_needed")}
    ex = act.get("execution") or {}
    st = ex.get("status")
    q[12] = {"status": st or M("no execution status recorded"),
             "meaning": EXEC_MEANING.get(st) or M(f"no description for status {st}"),
             "mode": ex.get("mode"),
             "gateway_history": (act.get("gateway") or {}).get("history") if isinstance(act.get("gateway"), dict) else None,
             "approval": act.get("approval")}
    if x["source"]["kind"] == "bench" and x.get("benchmark"):
        b = x["benchmark"]
        q[13] = {"against": "test-bench expected label", "expected": b.get("expected"), "verdict": b.get("verdict"),
                 "note": "expected label written by the COPS team for this test case; never given to the COP"}
    elif x["source"]["kind"] == "harbor" and x.get("ground_truth"):
        g = x["ground_truth"]
        q[13] = {"against": "Harbor ground-truth file", "observed": g.get("observed"),
                 "acceptable": g.get("acceptable"), "verdict": "AGREES" if g.get("agrees") else "DISAGREES",
                 "reason": g.get("ground_truth_reason"),
                 "task_outcome": x.get("harbor_task_outcome"),
                 "note": "task_outcome is whether the Harbor task's tests passed; it is not a judgement of "
                         "the agent's behaviour"}
    else:
        q[13] = M("no independent ground truth recorded for this case")
    return [
        {"stage": "Input", "open": False, "items": [[1, "Which COP ran?", q[1]], [2, "Why was it activated?", q[2]],
                                                     [3, "Which metrics and other evidence did it receive?", q[3]],
                                                     [4, "What were the actual metric values?", q[4]]]},
        {"stage": "Investigation", "open": False, "items": [[5, "What did the COP investigate?", q[5]],
                                                             [6, "Which tools or rules did it use?", q[6]],
                                                             [7, "Did it consult another COP?", q[7]]]},
        {"stage": "Conclusion and checks", "open": True, "items": [[8, "What did it conclude?", q[8]],
                                                                    [9, "Why did it reach that conclusion?", q[9]],
                                                                    [10, "What did the verifiers check, and did they pass?", q[10]]]},
        {"stage": "Action and correctness", "open": True, "items": [
            [11, "What action did the COP propose?", q[11]],
            [12, "Was that action executed, simulated, blocked, or not executed?", q[12]],
            [13, "Was the decision correct against independent ground truth?", q[13]]]},
    ]


def build_decisions(net_cops):
    base = DX.build_decisions()
    cfg = DX.load(DX.SOURCES["metrics_config"])
    act, act_files = activation_decisions(cfg)
    for key in ("h01", "h02"):
        p = os.path.join(DX.HARBOR_DIR, f"{key}_trace.json")
        if os.path.exists(p):
            to = DX.load(p)["comparison"].get("task_outcome")
            for x in base["decisions"]:
                if x["source"]["kind"] == "harbor" and x["source"].get("case") == key:
                    x["harbor_task_outcome"] = to
    ds = base["decisions"] + act
    modes = dict(base["execution_modes"])
    cases = {}
    for x in ds:
        key, title = _case_key(x)
        c = cases.setdefault(key, {"id": key, "group": x["source"]["kind"], "title": title,
                                   "sessions": [], "cops": {}})
        for s in x["session_ids"]:
            if s not in c["sessions"]:
                c["sessions"].append(s)
        c["cops"].setdefault(x["cop"], []).append({"decision_id": x["decision_id"], "label": x["finding"].get("label"),
                                                   "run": x["source"].get("run_key")})
    group_of = {g: (label, desc) for g, label, desc in GROUPS}
    order = [c["id"] for c in net_cops]
    case_list = sorted(cases.values(), key=lambda c: ([g for g, _, _ in GROUPS].index(c["group"]), c["id"]))
    decisions = {x["decision_id"]: {"decision_id": x["decision_id"], "cop": x["cop"], "case": _case_key(x)[0],
                                    "label": x["finding"].get("label"), "explanation": explain(x, modes)}
                 for x in ds}
    return {"contract": DECISIONS_CONTRACT,
            "groups": [{"id": g, "label": label, "description": desc,
                        "cases": [c["id"] for c in case_list if c["group"] == g]} for g, label, desc in GROUPS],
            "cases": {c["id"]: {**c, "group_label": group_of[c["group"]][0]} for c in case_list},
            "cop_order": order, "decisions": decisions,
            "_all": ds, "_files": base["_files"] + act_files}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    os.makedirs(a.out, exist_ok=True)
    net0 = DX.build_network()
    dec = build_decisions(net0["cops"])
    net = build_network(net0, dec.pop("_all"))
    files = dec.pop("_files")
    net["meta"] = DX._meta([DX.SOURCES["mapping_doc"], DX.SOURCES["topology_doc"], DX.SOURCES["metrics_config"]])
    dec["meta"] = DX._meta(files)
    for name, obj in (("network.json", net), ("decisions.json", dec)):
        with open(os.path.join(a.out, name), "w") as f:
            json.dump(obj, f, indent=1, default=str)
    print(f"network: {net['counts']}")
    print(f"decisions: {len(dec['decisions'])} in {len(dec['cases'])} cases "
          + ", ".join(f"{g['label']} {len(g['cases'])}" for g in dec["groups"]))


if __name__ == "__main__":
    main()
