#!/usr/bin/env python3
"""
Signal Collect — coverage measurement

Reads everything the collectors have written and answers one question:
how much are we actually capturing, per tool, per collector?

    python3 signal_coverage.py                    # scan the default places
    python3 signal_coverage.py <dir> [<dir> ...]  # scan specific folders
    python3 signal_coverage.py --gaps             # only what nobody captured

Scans for Signal observable rows (any .jsonl containing rows with an
"observable_id"), groups them by tool and collector, and checks each against
a list of observables we care about.
"""

import glob
import json
import os
import sys
from collections import defaultdict

HOME = os.path.expanduser("~")
DEFAULT_DIRS = [
    os.path.join(HOME, ".signal", "sessions"),
    "signal_sessions",
    "signal_sessions/cursor",
    ".",
]


def g(row, *keys):
    """Get a field from the row or from its detail block."""
    d = row.get("detail") or {}
    for k in keys:
        if row.get(k) not in (None, "", 0, [], {}):
            return row[k]
        if d.get(k) not in (None, "", 0, [], {}):
            return d[k]
    return None


def has_event(row, *names):
    return row.get("interaction_type") in names or row.get("hook_event") in names


# (id, label, test). Kept to the observables that matter for the argument.
CHECKS = [
    (1,   "session id",                  lambda r: bool(r.get("agent_id"))),
    (3,   "turn / generation id",        lambda r: bool(r.get("generation_id"))),
    (5,   "tool call id",                lambda r: bool(g(r, "tool_use_id", "request_id"))),
    (9,   "user identity",               lambda r: bool(r.get("operator") or g(r, "user_email"))),
    (13,  "os username",                 lambda r: bool(r.get("operator"))),
    (18,  "session start",               lambda r: has_event(r, "session_start", "mcp_session_start")),
    (19,  "session end",                 lambda r: has_event(r, "session_end", "mcp_session_end")),
    (20,  "session duration",            lambda r: has_event(r, "session_end") and r.get("latency_ms", 0) > 0),
    (24,  "os / platform",               lambda r: bool(g(r, "shell", "term"))),
    (29,  "shell and version",           lambda r: bool(g(r, "shell"))),
    (30,  "TERM variable",               lambda r: bool(g(r, "term"))),
    (39,  "agent tool version",          lambda r: bool(g(r, "app_version", "version"))),
    (51,  "prompt text",                 lambda r: has_event(r, "user_prompt", "user_input") and bool(g(r, "text"))),
    (52,  "prompt length",               lambda r: bool(g(r, "length", "prompt_length"))),
    (53,  "prompt word count",           lambda r: bool(g(r, "word_count"))),
    (58,  "slash command",               lambda r: g(r, "slash_command") is True),
    (60,  "model chosen",                lambda r: bool(r.get("model"))),
    (68,  "secret-shaped text",          lambda r: has_event(r, "user_prompt") and r.get("sensitivity_tier", 1) >= 3),
    (73,  "model name",                  lambda r: bool(r.get("model"))),
    (75,  "input tokens",                lambda r: bool(g(r, "input_tokens"))),
    (76,  "output tokens",               lambda r: bool(g(r, "output_tokens"))),
    (77,  "cache tokens",                lambda r: bool(g(r, "cache_read_tokens"))),
    (79,  "total tokens",                lambda r: bool(r.get("tokens_total"))),
    (80,  "cost",                        lambda r: bool(r.get("cost_usd"))),
    (81,  "model call latency",          lambda r: has_event(r, "llm_request") and r.get("latency_ms", 0) > 0),
    (95,  "thinking blocks",             lambda r: bool(g(r, "thinking_blocks"))),
    (98,  "loop count",                  lambda r: bool(g(r, "loop_count"))),
    (99,  "plan item created",           lambda r: has_event(r, "plan_item_created", "TaskCreated")),
    (100, "plan item completed",         lambda r: has_event(r, "plan_item_completed", "TaskCompleted")),
    (102, "subagent spawned",            lambda r: has_event(r, "subagent_start")),
    (104, "delegation depth",            lambda r: r.get("cascade_depth", 0) > 0),
    (107, "context compaction",          lambda r: has_event(r, "context_compaction", "compaction_start")),
    (111, "instructions loaded",         lambda r: has_event(r, "instructions_loaded")),
    (116, "tool name",                   lambda r: bool(g(r, "tool", "tool_name"))),
    (117, "built-in vs mcp tool",        lambda r: r.get("connector") in ("shell", "filesystem", "web", "subagent")),
    (118, "full tool arguments",         lambda r: bool(g(r, "arguments"))),
    (119, "full tool result",            lambda r: bool(g(r, "result", "output_preview"))),
    (120, "tool result size",            lambda r: bool(g(r, "result_size", "output_size", "result_bytes"))),
    (121, "tool duration",               lambda r: has_event(r, "mcp_result", "tool_result") and r.get("latency_ms", 0) > 0),
    (122, "tool success or failure",     lambda r: has_event(r, "tool_result", "tool_failure", "mcp_result", "shell_result")),
    (126, "mcp server name",             lambda r: bool(g(r, "server")) or r.get("surface") == "mcp"),
    (130, "mcp tools discovered",        lambda r: has_event(r, "mcp_tools_discovered")),
    (135, "file path read",              lambda r: has_event(r, "file_read") or bool(g(r, "file_path"))),
    (136, "file path written",           lambda r: has_event(r, "file_edit", "file_changed")),
    (141, "chars added / removed",       lambda r: bool(g(r, "chars_added", "chars_removed"))),
    (142, "old content",                 lambda r: bool(g(r, "old_string"))),
    (143, "new content",                 lambda r: bool(g(r, "new_string"))),
    (152, "sensitive file flagged",      lambda r: r.get("sensitivity_tier", 1) >= 3),
    (159, "shell command string",        lambda r: bool(g(r, "command"))),
    (161, "exit code",                   lambda r: g(r, "exit_code") is not None),
    (162, "stdout / output content",     lambda r: bool(g(r, "output_preview", "bytes_printed"))),
    (166, "hung process detected",       lambda r: has_event(r, "stall")),
    (169, "ctrl-c interrupt",            lambda r: has_event(r, "interrupt")),
    (173, "risky command markers",       lambda r: bool(g(r, "risk_markers"))),
    (178, "developer's own commands",    lambda r: has_event(r, "user_input") and r.get("collector") == "cli_wrapper"),
    (188, "workspace opened",            lambda r: has_event(r, "workspace_open")),
    (189, "workspace roots",             lambda r: bool(r.get("workspace"))),
    (195, "permission requested",        lambda r: has_event(r, "permission_request", "permission_decision")),
    (197, "permission denied",           lambda r: r.get("permission_status") in ("denied", "reject", "deny")),
    (198, "denial reason",               lambda r: bool(g(r, "reason", "matched_rule"))),
    (199, "approval scope",              lambda r: r.get("permission_status") in ("user_temporary", "user_permanent", "config")),
    (200, "permission mode change",      lambda r: has_event(r, "permission_mode_change")),
    (205, "session abandoned",           lambda r: has_event(r, "interrupt", "turn_failed")),
    (211, "git branch",                  lambda r: bool(g(r, "gitBranch", "branch"))),
    (243, "outside workspace boundary",  lambda r: g(r, "outside_workspace") is True),
]

BLOCKING = [
    ("can refuse an action", lambda r: r.get("blockable") is True or r.get("permission_status") == "denied"),
]


def load_rows(dirs):
    rows = []
    seen_files = set()
    for d in dirs:
        if not os.path.isdir(d):
            continue
        for path in glob.glob(os.path.join(d, "**", "*.jsonl"), recursive=True):
            real = os.path.realpath(path)
            if real in seen_files or real.endswith(".wire.jsonl") or "RAW" in os.path.basename(real):
                continue
            seen_files.add(real)
            for line in open(real):
                try:
                    r = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(r, dict) and "observable_id" in r:
                    rows.append(r)
    return rows, len(seen_files)


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    gaps_only = "--gaps" in sys.argv
    dirs = args or DEFAULT_DIRS

    rows, nfiles = load_rows(dirs)
    if not rows:
        print("No observable rows found. Looked in:")
        for d in dirs:
            print("   " + d)
        return 1

    groups = defaultdict(list)
    for r in rows:
        tool = r.get("agent_tool", "unknown")
        if tool.startswith("claude"):
            tool = "claude-code"
        groups[(tool, r.get("collector", "unknown"))].append(r)

    tools = sorted({t for t, _ in groups})
    collectors = sorted({c for _, c in groups})

    print(f"\n{len(rows)} observable rows from {nfiles} files")
    print(f"{len(tools)} tools, {len(collectors)} collectors\n")

    print(f"{'tool':<14} {'collector':<14} {'rows':>6} {'events':>7} {'observables':>12}")
    print("-" * 60)
    covered = defaultdict(set)
    for (tool, coll), rs in sorted(groups.items()):
        hits = set()
        for oid, label, test in CHECKS:
            for r in rs:
                try:
                    if test(r):
                        hits.add(oid)
                        covered[(tool, coll)].add(oid)
                        break
                except Exception:
                    pass
        events = len({r.get("interaction_type") for r in rs})
        print(f"{tool:<14} {coll:<14} {len(rs):>6} {events:>7} {len(hits):>9}/{len(CHECKS)}")

    if not gaps_only:
        for tool in tools:
            print(f"\n\n=== {tool} ===")
            cols = [c for t, c in groups if t == tool]
            width = max(len(c) for c in cols) if cols else 10
            header = "  ".join(f"{c[:12]:<12}" for c in cols)
            print(f"{'observable':<28} {header}")
            print("-" * (30 + 14 * len(cols)))
            for oid, label, _ in CHECKS:
                marks = []
                for c in cols:
                    marks.append(f"{'yes' if oid in covered[(tool, c)] else '.':<12}")
                if any("yes" in m for m in marks):
                    print(f"{oid:>4} {label:<23} " + "  ".join(marks))
            missing = [f"{oid} {label}" for oid, label, _ in CHECKS
                       if not any(oid in covered[(tool, c)] for c in cols)]
            if missing:
                print(f"\n  not captured for {tool} ({len(missing)}):")
                for m in missing:
                    print("     " + m)

    all_covered = set().union(*covered.values()) if covered else set()
    nobody = [(oid, label) for oid, label, _ in CHECKS if oid not in all_covered]
    print(f"\n\nCaptured by at least one collector: {len(all_covered)}/{len(CHECKS)}")
    if nobody:
        print(f"Captured by nothing yet ({len(nobody)}):")
        for oid, label in nobody:
            print(f"   {oid:>4} {label}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
