"""
Deliverable A, generated rather than typed: the COP implementation matrix.

    python -m cops.runtime.matrix [results.json]      # from collector/

Every column is read from code: specs, profiles, memory policies, the tool
registry, the rule table, the verifier registry and the evaluation results.
Missing dependencies are found by calling each COP's tools on a bare
synthetic case with no artifacts and collecting what they report missing.
"""

import json, os, sys

from ..specs import BY_KEY, topological_order
from ..authority import TIER
from .case import CaseData
from .memory import POLICIES
from .profiles import build_profiles, allowlists, LLM_COPS
from .rules import RULES, DETERMINISTIC_TOOLS
from .tools import TOOLS, ToolGateway
from .verifiers import CHECKS
from . import synthetic as S

FINDING_CHECKS = [k for k, v in CHECKS.items() if v[2] == "BLOCKS_FINDING"]
ACTION_CHECKS = [k for k, v in CHECKS.items() if v[2] == "BLOCKS_ACTION"]


def missing_dependencies():
    rows = S.baseline_estate(3) + S.normal_session("bare", 9)
    case = CaseData("matrix", S.TENANT, "bare", rows, synthetic=True)
    prof = build_profiles()
    gw = ToolGateway(case, allowlists(prof), None)
    out = {}
    for cop, p in prof.items():
        deps = set()
        for t in sorted(set(p.tools) | set(DETERMINISTIC_TOOLS.get(cop, ()))):
            args = {}
            for k, spec in TOOLS[t][1].items():
                if spec.get("required"):
                    args[k] = (spec["values"][0] if spec["type"] == "enum" else
                               BY_KEY[cop].metric_keys[0] if spec["type"] == "metric" else None)
            r = gw.execute(cop, t, args, "matrix", {"tool_calls": 0, "max_tool_calls": 99},
                           {"case_id": "matrix", "tenant_id": S.TENANT, "agent_scope": case.agent_scope,
                            "upstream": {}})
            md = (r.get("result") or {}).get("missing_dependency")
            if md and md["dependency"] not in ("memory_gateway",):
                deps.add(md["dependency"])
        out[cop] = sorted(deps)
    return out


def scenario_index(results):
    by = {}
    for r in results:
        for cop, c in (r["view"].get("cops") or {}).items():
            by.setdefault(cop, []).append((r["id"], c["label"], c["status"], c["verified"]))
    return by


def rows(results=None):
    prof = build_profiles()
    miss = missing_dependencies()
    idx = scenario_index(results or [])
    out = []
    for cop in topological_order():
        s, p = BY_KEY[cop], prof[cop]
        seen = idx.get(cop, [])
        attn = sorted({lab for _, lab, st, v in seen if v and st == "SUBSTANTIVE" and lab != s.interpretations[0]})
        out.append({
            "cop": cop, "graph": "cops/runtime/graph.py build_cop_graph (shared subgraph), profile "
                                 f"profiles.build_profiles()['{cop}']",
            "question": s.behavioural_question, "metrics": list(s.metric_keys),
            "classification": p.classification,
            "llm_nodes": "llm_plan_investigation, llm_assess_evidence" if p.classification == "llm" else "none",
            "provider": "LLMGateway(FixtureProvider|AnthropicProvider opt-in), prompt " + p.prompt_version
                        if p.classification == "llm" else "none",
            "tools": list(p.tools), "rule": f"rules.{RULES[cop].__name__}"
                     + (" (decision)" if p.classification == "deterministic" else " (computational baseline)"),
            "verifiers": FINDING_CHECKS + ACTION_CHECKS + ["MU-01", "RP-01 (diagnostic)"],
            "working_state": list(p.working_state),
            "memory_writes": list(POLICIES[cop]["writes"]), "memory_reads": list(POLICIES[cop]["reads"]),
            "memory_scope": POLICIES[cop]["scope"],
            "actions": [f"{a} ({TIER[a]})" for a in s.permitted_actions],
            "approval_in_supervised": [a for a in s.permitted_actions if a in p.approval_actions],
            "consumes": list(s.consumes_decisions_from), "consumers": list(s.downstream_cops),
            "scenarios": sorted({sid for sid, *_ in seen}),
            "verified_attention_labels_seen": attn,
            "labels_not_exercised_in_evaluation": [l for l in s.interpretations[1:] if l not in attn],
            "missing_dependencies": miss[cop],
        })
    return out


def markdown(rs):
    lines = ["| COP | Mode | Question | Metrics | Tools | Rule | Memory (writes / reads, scope) | "
             "Actions (approval in supervised) | Consumes → consumers | Scenarios | Verified attention "
             "labels seen | Needs |", "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in rs:
        lines.append(
            f"| `{r['cop']}` | {r['classification']} | {r['question']} | {', '.join(r['metrics']) or '-'} | "
            f"{', '.join(r['tools'])} | `{r['rule']}` | {', '.join(r['memory_writes'])} / "
            f"{', '.join(r['memory_reads'])}, {r['memory_scope']} | {', '.join(r['actions'])}"
            f" ({', '.join(r['approval_in_supervised']) or 'none'}) | {', '.join(r['consumes']) or '-'} → "
            f"{', '.join(r['consumers']) or '-'} | {', '.join(r['scenarios']) or '-'} | "
            f"{', '.join(r['verified_attention_labels_seen']) or '-'} | "
            f"{', '.join(r['missing_dependencies']) or '-'} |")
    return "\n".join(lines)


def main(argv=None):
    argv = argv or sys.argv[1:]
    here = os.path.dirname(os.path.abspath(__file__))
    path = argv[0] if argv else os.path.join(here, "..", "..", "..", "docs", "cops", "evaluation", "results.json")
    results = json.load(open(path)) if os.path.exists(path) else []
    rs = rows(results)
    out = os.path.join(here, "..", "..", "..", "docs", "cops", "evaluation", "matrix.json")
    with open(out, "w") as f:
        json.dump(rs, f, indent=1)
    print(markdown(rs))


if __name__ == "__main__":
    main()
