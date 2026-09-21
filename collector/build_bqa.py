#!/usr/bin/env python3
"""
Signal Collect — Behavioral Question Answerability (BQA)

Field coverage is an input, not a result. What matters is how many behavioural
questions become answerable. This computes that directly.

BQA is the fraction of the 14 MAST failure modes for which, at a given
instrumentation level and on a given tool, every required observable is both:

  (i)  available, meaning it arrived in captured records at that level, and
  (ii) joinable at turn resolution, meaning the records carrying it also carry
       a turn identifier, so the field can be related to the other fields the
       mode requires.

The second condition is not decoration. Eleven of the fourteen modes are
defined over a relation between events within a turn rather than over a single
event. A field that arrives without a turn identifier cannot participate in
that relation and therefore does not contribute to answerability, however
faithfully it was captured.

Reported at three instrumentation levels:

  native      the vendor's own record only, meaning the transcript or session
              file the tool writes for itself
  otel        the vendor's emitted OpenTelemetry only
  all         all six collectors

    python3 build_bqa.py
    python3 build_bqa.py --per-mode        # which modes fail and why
    python3 build_bqa.py --latex
"""

import argparse
import importlib.util
import json
import os
import sys
from collections import Counter, defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
HOME = os.path.expanduser("~")
DEFAULT_DIRS = [os.path.join(HOME, ".signal", "sessions"),
                "signal_sessions", "signal_sessions/cursor", "data", ".",
                os.path.join(HOME, "project-signal-dashboard", "signal_sessions")]

TOOLS = ["claude-code", "cursor", "codex", "copilot", "antigravity"]
LABEL = {"claude-code": "Claude Code", "cursor": "Cursor", "codex": "Codex",
         "copilot": "Copilot", "antigravity": "Antigravity"}

# Instrumentation levels, defined by which collectors are admitted.
# "native" is the vendor's own written record, which our file system watcher
# reads but does not create. Admitting it under native rather than under the
# watcher reflects that the data is the vendor's, not ours.
LEVELS = [
    ("native", "Native trace only",
     lambda r: (r.get("collector") == "fs_watcher"
                and r.get("observable_type") in ("transcript_appended",
                                                 "artifact_written"))),
    ("otel", "OpenTelemetry only",
     lambda r: r.get("collector") == "otel"),
    ("all", "All six collectors",
     lambda r: True),
]

# The 14 MAST failure modes and the observables each requires.
# Cemri et al., "Why Do Multi-Agent LLM Systems Fail?", arXiv:2503.13657.
MODES = [
 ("FM-1.1", "Disobey task specification", "System design", 11.8,
  ["prompt_text", "turn_id", "session_id"],
  ["file_path", "command", "tool_name"]),
 ("FM-1.2", "Disobey role specification", "System design", 1.5,
  ["agent_id", "tool_arguments", "turn_id"],
  ["agent_type", "tool_name"]),
 ("FM-1.3", "Step repetition", "System design", 15.7,
  ["tool_name", "tool_arguments", "turn_id", "sequence_num"],
  ["tool_result"]),
 ("FM-1.4", "Loss of conversation history", "System design", 2.8,
  ["turn_id", "session_id", "file_path"],
  ["compaction_reason", "cache_read_tokens"]),
 ("FM-1.5", "Unaware of termination conditions", "System design", 12.4,
  ["turn_count", "session_id", "total_tokens"],
  ["command", "exit_code"]),
 ("FM-2.1", "Conversation reset", "Inter-agent", None,
  ["compaction_reason", "session_id", "turn_id"],
  ["cache_read_tokens"]),
 ("FM-2.2", "Fail to ask for clarification", "Inter-agent", None,
  ["prompt_text", "response_text", "turn_id"],
  ["reasoning_text"]),
 ("FM-2.3", "Task derailment", "Inter-agent", None,
  ["prompt_text", "file_path", "turn_id"],
  ["chars_added", "tool_arguments"]),
 ("FM-2.4", "Information withholding", "Inter-agent", None,
  ["agent_id", "parent_agent_id", "tool_result", "turn_id"],
  ["raw_ref", "response_text"]),
 ("FM-2.5", "Ignored other agent's input", "Inter-agent", None,
  ["agent_id", "parent_agent_id", "tool_result", "turn_id"],
  ["response_text"]),
 ("FM-2.6", "Reasoning-action mismatch", "Inter-agent", None,
  ["reasoning_text", "tool_name", "sequence_num", "turn_id"],
  ["tool_arguments", "command"]),
 ("FM-3.1", "Premature termination", "Task verification", 6.2,
  ["command", "exit_code", "turn_id", "session_id"],
  ["stop_reason", "response_text"]),
 ("FM-3.2", "No or incomplete verification", "Task verification", 8.2,
  ["file_path", "file_change_kind", "command", "turn_id"],
  ["exit_code"]),
 ("FM-3.3", "Incorrect verification", "Task verification", 9.1,
  ["command", "exit_code", "file_path", "turn_id"],
  ["stdout", "stderr"]),
]

# Fields we compute. A derived field is available at a level only if the
# fields it derives from are available at that level. Treating a derivation as
# freely available would credit a level with information it did not carry.
DERIVES_FROM = {
    "turn_count":        ["turn_id", "session_id"],
    "delegation_depth":  ["agent_id"],
    "outside_workspace": ["file_path", "workspace"],
    "output_size":       ["stdout"],
    "sequence_num":      ["occurred_at"],
    "cost_usd":          ["total_tokens"],
    "chars_added":       ["file_path"],
    "chars_removed":     ["file_path"],
    "status":            ["observable_type"],
}

ALWAYS = {"occurred_at", "observable_type"}


def load(dirs):
    p = os.path.join(HERE, "signal_curate.py")
    if not os.path.exists(p):
        sys.exit("signal_curate.py must sit beside this script")
    spec = importlib.util.spec_from_file_location("sc", p)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    loaded, _ = m.load_all(dirs)
    records, _c = m.curate(loaded)
    return records


def field_state(records, field, seen=None):
    """(available, joinable) for one field over one set of records."""
    seen = seen or set()
    if field in ALWAYS:
        return True, True
    if field in DERIVES_FROM and field not in seen:
        # a derived field inherits the availability of what it derives from
        direct = [r for r in records if r.get(field) not in (None, "", [], {})]
        if not direct:
            seen = seen | {field}
            parts = [field_state(records, f, seen) for f in DERIVES_FROM[field]]
            if not parts or not all(p[0] for p in parts):
                return False, False
            return True, all(p[1] for p in parts)

    filled = [r for r in records if r.get(field) not in (None, "", [], {})]
    if not filled:
        return False, False
    joinable = any(r.get("turn_id") for r in filled)
    return True, joinable


def evaluate(records):
    """result[tool][level] = (bqa, weighted_bqa, per-mode detail)."""
    by_tool = defaultdict(list)
    for r in records:
        if r.get("tool") in TOOLS:
            by_tool[r["tool"]].append(r)

    out = {}
    for tool in TOOLS:
        rows = by_tool.get(tool, [])
        per_level = {}
        for key, name, admits in LEVELS:
            subset = [r for r in rows if admits(r)]
            modes = {}
            for mid, mname, cat, share, required, helpful in MODES:
                states = {f: field_state(subset, f) for f in required}
                missing = [f for f, (a, _) in states.items() if not a]
                unjoinable = [f for f, (a, j) in states.items() if a and not j]
                answerable = not missing and not unjoinable
                modes[mid] = {
                    "name": mname, "category": cat, "share": share,
                    "answerable": answerable,
                    "missing": missing, "unjoinable": unjoinable,
                    "helpful_present": sum(
                        1 for f in helpful if field_state(subset, f)[0]),
                    "helpful_total": len(helpful),
                }
            n = sum(1 for m in modes.values() if m["answerable"])
            wshare = sum(m["share"] for m in modes.values()
                         if m["answerable"] and m["share"])
            total_share = sum(m["share"] for m in modes.values() if m["share"])
            per_level[key] = {
                "name": name, "records": len(subset),
                "bqa": n / len(MODES),
                "answerable": n,
                "weighted": (wshare / total_share) if total_share else 0.0,
                "modes": modes,
            }
        out[tool] = per_level
    return out, by_tool


def print_summary(res, by_tool):
    print("\nBehavioral Question Answerability")
    print("Fraction of the 14 MAST failure modes for which every required "
          "observable is\navailable and joinable at turn resolution.\n")

    w = 14
    hdr = f"{'Instrumentation level':<26}" + "".join(
        f"{LABEL[t][:12]:>{w}}" for t in TOOLS)
    print(hdr)
    print("-" * len(hdr))
    for key, name, _ in LEVELS:
        line = f"{name:<26}"
        for t in TOOLS:
            d = res[t][key]
            line += f"{d['answerable']:>6}/14 {d['bqa']*100:>4.0f}%"
        print(line)

    print()
    line = f"{'weighted by failure share':<26}"
    for t in TOOLS:
        line += f"{res[t]['all']['weighted']*100:>13.0f}%"
    print(line)
    print("\nWeighting uses the incidence MAST reports for the nine modes it "
          "quantifies.\nThe five inter-agent modes without a published share "
          "are excluded from the weighted figure only.")

    print(f"\n\n{'records admitted at each level':<26}" +
          "".join(f"{LABEL[t][:12]:>{w}}" for t in TOOLS))
    print("-" * len(hdr))
    for key, name, _ in LEVELS:
        line = f"{name:<26}"
        for t in TOOLS:
            line += f"{res[t][key]['records']:>{w}}"
        print(line)


def print_per_mode(res):
    for tool in TOOLS:
        if res[tool]["all"]["records"] == 0:
            continue
        print(f"\n\n{'='*88}\n{LABEL[tool]}\n{'='*88}")
        for key, name, _ in LEVELS:
            d = res[tool][key]
            print(f"\n{name}   {d['answerable']}/14   "
                  f"({d['bqa']*100:.0f}%)   {d['records']} records")
            print("-" * 88)
            for mid, m in d["modes"].items():
                mark = "yes" if m["answerable"] else "no"
                share = f"{m['share']}%" if m["share"] else "n/a"
                print(f"  {mid}  {m['name'][:34]:<36}{share:>6}  {mark}")
                if m["missing"]:
                    print(f"        missing:    {', '.join(m['missing'])}")
                if m["unjoinable"]:
                    print(f"        not joinable to a turn: "
                          f"{', '.join(m['unjoinable'])}")


def print_latex(res):
    print(r"\begin{table}[t]")
    print(r"\centering\small")
    print(r"\caption{Behavioral Question Answerability: the fraction of the "
          r"14 MAST failure modes for which every required observable is both "
          r"available and joinable at turn resolution. Field coverage is an "
          r"input to this figure, not a substitute for it: an observable that "
          r"arrives without a turn identifier cannot participate in a relation "
          r"defined over a turn, and eleven of the fourteen modes are so "
          r"defined.}")
    print(r"\label{tab:bqa}")
    print(r"\begin{tabular}{l" + "r" * len(TOOLS) + "}")
    print(r"\toprule")
    print(r"Instrumentation level & " +
          " & ".join(LABEL[t] for t in TOOLS) + r" \\")
    print(r"\midrule")
    for key, name, _ in LEVELS:
        cells = []
        for t in TOOLS:
            d = res[t][key]
            cells.append(f"{d['answerable']}/14 ({d['bqa']*100:.0f}\\%)")
        print(f"{name} & " + " & ".join(cells) + r" \\")
    print(r"\midrule")
    cells = [f"{res[t]['all']['weighted']*100:.0f}\\%" for t in TOOLS]
    print(r"All six, weighted by failure share & " + " & ".join(cells) + r" \\")
    print(r"\bottomrule")
    print(r"\end{tabular}")
    print(r"\end{table}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dirs", nargs="*")
    ap.add_argument("--per-mode", action="store_true")
    ap.add_argument("--latex", action="store_true")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    records = load(a.dirs or DEFAULT_DIRS)
    if not records:
        sys.exit("no captured data found")
    res, by_tool = evaluate(records)

    if a.json:
        print(json.dumps(res, indent=2, default=str))
    elif a.latex:
        print_latex(res)
    else:
        print_summary(res, by_tool)
        if a.per_mode:
            print_per_mode(res)
    return 0


if __name__ == "__main__":
    sys.exit(main())
