"""
The 18-row fleet results matrix, generated from runs rather than typed.

    python -m cops.runtime.fleet_report       # from collector/; writes docs/cops/evaluation/FLEET_MATRIX.md

Sources, each labelled in the output:
    offline   the 25 evaluation scenarios (evaluation.run_all). Deterministic COPs
              run their rules for real and make no model call. LLM COPs run on
              scripted replies, so their findings, tokens and latency are SIMULATED.
    live      docs/cops/evaluation/live_results.json, written only by a live run of
              live_eval.py. Without that file every live column reads "not run".
"""

import json, os, statistics, sys

from ..specs import BY_KEY, topological_order
from . import evaluation as E
from .graph import build_cop_graph
from .profiles import build_profiles, LLM_COPS

DOCS = os.path.join(os.path.dirname(__file__), "..", "..", "..", "docs", "cops", "evaluation")
NODES = None


def _nodes():
    global NODES
    if NODES is None:
        NODES = set(build_cop_graph().get_graph().nodes) - {"__start__", "__end__"}
    return NODES


def implementation_status(cop, p):
    """Input loading, interpretation, verification, authority and typed output:
    the shared subgraph's nodes, plus what this COP must supply to them."""
    from .rules import RULES
    from .tools import TOOLS
    need = {"receive_case", "load_inputs", "validate_evidence", "retrieve_memory", "computational_baseline",
            "independent_verify", "authority_check", "record_decision", "persist_and_publish"}
    if p.classification == "llm":
        need |= {"llm_plan_investigation", "llm_assess_evidence", "validate_tool_request",
                 "execute_authorised_tool", "validate_tool_result", "formulate_finding"}
    gaps = [f"node {n}" for n in sorted(need - _nodes())]
    if cop not in RULES:
        gaps.append("no rule")
    gaps += [f"unregistered tool {t}" for t in p.tools if t not in TOOLS]
    if p.classification == "llm" and not (p.prompt and p.detail_schema and p.tools):
        gaps.append("prompt, detail schema or tools missing")
    return "complete" if not gaps else "gaps: " + ", ".join(gaps)


def offline_index(results):
    by = {}
    for r in results:
        failed = [c["expectation"] for c in r["checks"] if not c["passed"]]
        for cop, c in (r["view"].get("cops") or {}).items():
            by.setdefault(cop, []).append({"scenario": r["id"], "c": c, "scenario_failed": failed})
    return by


def _lat(xs):
    return f"{statistics.median(xs):.0f}" if xs else "n/a"


def rows(offline, live):
    prof = build_profiles()
    idx = offline_index(offline)
    live_by = {}
    for r in (live or {}).get("results", []):
        live_by.setdefault(r["cop"], []).append(r)
    out = []
    for cop in topological_order():
        p = prof[cop]
        seen = idx.get(cop, [])
        labels = {}
        for s in seen:
            labels[s["c"]["label"]] = labels.get(s["c"]["label"], 0) + 1
        verified = sum(1 for s in seen if s["c"]["verified"])
        rejected = sum(1 for s in seen if s["c"]["status"] == "REJECTED_BY_VERIFIER")
        inconc = sum(1 for s in seen if s["c"]["status"] == "INCONCLUSIVE")
        failures = sorted({x for s in seen for x in s["scenario_failed"]})
        row = {"cop": cop, "classification": p.classification,
               "implementation": implementation_status(cop, p),
               "offline_scenarios": len(seen), "offline_findings": labels,
               "offline_verified": verified, "offline_rejected": rejected, "offline_inconclusive": inconc,
               "offline_scenario_failures": failures,
               "offline_median_ms": _lat([s["c"]["total_ms"] for s in seen]),
               "offline_llm_calls": sum(s["c"]["llm_calls"] for s in seen),
               "offline_actions": sorted({f"{s['c'].get('requested_action')}→{s['c']['action']}" for s in seen}),
               "allowed_actions": list(p.spec.permitted_actions) if hasattr(p, "spec") else
               list(BY_KEY[cop].permitted_actions)}
        lv = live_by.get(cop, [])
        ran = [r for r in lv if not r.get("skipped")]
        inv = [i for r in ran for i in r["invocations"]]
        row.update({
            "live_cases": len(ran) if cop in LLM_COPS else None,
            "live_findings": {r["id"].split(":")[1]: r["label"] for r in ran},
            "live_actions": {r["id"].split(":")[1]: r.get("action") for r in ran},
            "live_outcomes": {r["id"].split(":")[1]: r["outcome"] for r in ran},
            "live_verified": sum(1 for r in ran if r["verified"]),
            "live_errors": sum(1 for i in inv if i["error"]),
            "live_latency_ms": round(sum(i["latency_ms"] or 0 for i in inv), 1) if inv else None,
            "live_tokens": (sum(i["input_tokens"] or 0 for i in inv), sum(i["output_tokens"] or 0 for i in inv))
                           if inv else None,
            "live_cost_usd": round(sum(i["cost_usd"] or 0 for i in inv), 6) if inv else None,
            "live_simulated_records": sum(1 for i in inv if i["simulated"]),
        })
        out.append(row)
    return out


def markdown(rs, live):
    live_note = ("live columns from `live_results.json`, model `%s`" % live.get("model")) if live else \
        "**no live run yet**: every live column reads `not run`"
    L = ["# COPS fleet results matrix", "",
         "Offline columns come from the 25 evaluation scenarios. Deterministic COPs run their rules for real "
         "and make no model call. LLM COPs run on scripted replies there, so their offline findings are "
         "**simulated**. " + live_note + ".", "",
         "| COP | Implementation | Type | Test scenarios | Findings | Verification | Actions (requested→final) | Failures | Latency | Token usage | Cost |",
         "|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in rs:
        llm = r["classification"] == "llm"
        find = ", ".join(f"{k} x{v}" for k, v in sorted(r["offline_findings"].items()))
        scen = f"{r['offline_scenarios']} offline" + (f" + {r['live_cases']} live" if llm and r["live_cases"]
                                                       else (" + 3 live (not run)" if llm else ""))
        if llm:
            find += " (simulated)"
        if llm and r["live_cases"]:
            find += "; live: " + ", ".join(f"{k}={v}" for k, v in r["live_findings"].items())
        ver = (f"{r['offline_verified']} verified, {r['offline_rejected']} rejected, "
               f"{r['offline_inconclusive']} inconclusive")
        if llm and r["live_cases"]:
            ver += f"; live {r['live_verified']}/{r['live_cases']} verified"
        fails = (f"{len(r['offline_scenario_failures'])} scenario expectations failed"
                 if r["offline_scenario_failures"] else "0")
        if llm and r["live_cases"]:
            bad = [k for k, v in r["live_outcomes"].items() if v != "agree"]
            fails += f"; live: {r['live_errors']} call errors, disagreed on {', '.join(bad) or 'none'}"
        lat = f"median {r['offline_median_ms']} ms per run" + (
            f" (simulated); live {r['live_latency_ms']:.0f} ms in model calls" if llm and r["live_cases"]
            else " (simulated)" if llm else "")
        tok = ("none (no model call)" if not llm else
               f"live {r['live_tokens'][0]} in / {r['live_tokens'][1]} out" if r["live_tokens"] else
               f"{r['offline_llm_calls']} simulated calls, estimated only; live not run")
        cost = ("$0 (no model call)" if not llm else
                f"${r['live_cost_usd']:.4f} (configured price x measured tokens)" if r["live_tokens"] else
                "live not run")
        acts = ", ".join(r["offline_actions"]) + ("; live: " + ", ".join(
            f"{k}={v}" for k, v in r["live_actions"].items()) if llm and r.get("live_actions") else "")
        L.append(f"| `{r['cop']}` | {r['implementation']} | {'LLM' if llm else 'deterministic'} | {scen} | "
                 f"{find} | {ver} | {acts} | {fails} | {lat} | {tok} | {cost} |")
    return "\n".join(L) + "\n"


def main(argv=None):
    offline = E.run_all()
    live_path = os.path.join(DOCS, "live_results.json")
    live = json.load(open(live_path)) if os.path.exists(live_path) else None
    if live and not live.get("live"):
        live = None
    rs = rows(offline, live)
    md = markdown(rs, live)
    with open(os.path.join(DOCS, "FLEET_MATRIX.md"), "w") as fh:
        fh.write(md)
    with open(os.path.join(DOCS, "fleet_matrix.json"), "w") as fh:
        json.dump({"live_source": live_path if live else None, "rows": rs,
                   "offline_scenarios": len(offline),
                   "offline_passed": sum(1 for r in offline if r["passed"])}, fh, indent=1)
    print(md)
    return 0


if __name__ == "__main__":
    sys.exit(main())
