#!/usr/bin/env python3
"""
Signal paths

Turns each session into the route the agent took: an ordered sequence of
steps drawn from one shared vocabulary, so paths from different coding
agents can be compared at all. Only actions the agent chose are steps;
file watcher rows are effects on disk and are left out here.

    python3 signal_paths.py                   coverage of the vocabulary
    python3 signal_paths.py --show 8          print eight sessions as paths
    python3 signal_paths.py --show 5 --tool cursor
    python3 signal_paths.py --unmapped        actions the vocabulary misses
"""

import argparse, json, os, re, sys, urllib.request
from collections import Counter, defaultdict

URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
KEY = os.environ.get("SUPABASE_KEY", "")
AGENTS = ("claude-code", "cursor", "codex", "copilot", "antigravity")
COLS = ",".join(["session_id", "tool", "collector", "observable_type",
    "occurred_at", "sequence_num", "turn_id", "agent_id", "tool_use_id",
    "tool_name", "command", "mcp_server", "success", "exit_code",
    "hung_seconds", "operator_username"])

TEST    = re.compile(r"\b(pytest|unittest|jest|vitest|mocha|go test|cargo test|npm (run )?test|yarn test|pnpm test|tftest|terraform (validate|test)|rspec|phpunit)\b", re.I)
INSTALL = re.compile(r"\b(pip3? install|npm (i|install|ci)|yarn add|pnpm add|brew install|apt(-get)? install|go get|cargo add)\b", re.I)
BUILD   = re.compile(r"\b(npm run build|yarn build|make\b|tsc\b|cargo build|go build|mvn|gradle|docker build|terraform (plan|fmt)|vite build)", re.I)
NET_CMD = re.compile(r"\b(curl|wget|ssh|scp|nc|ftp)\b", re.I)
DEL_CMD = re.compile(r"(^|[\s;&|])(rm|rmdir|unlink)\s", re.I)
VCS     = re.compile(r"^\s*git\b", re.I)
LOOK_C  = re.compile(r"^\s*(cat|head|tail|less|more|bat|wc)\b", re.I)
SRCH_C  = re.compile(r"^\s*(ls|find|grep|rg|tree|fd|locate)\b", re.I)

DELEGATE = re.compile(r"^(task|agent)$|spawn_agent|invoke_subagent|send_message|delegate", re.I)
PLAN     = re.compile(r"todo|update_plan|task_?create|plan", re.I)
NETWORK  = re.compile(r"web|fetch|browse|http|url", re.I)
SHELL    = re.compile(r"bash|shell|terminal|run_command|exec|command|stdin", re.I)
CHANGE   = re.compile(r"edit|replace|patch|apply|update", re.I)
CREATE   = re.compile(r"write|create|new_?file|notebook", re.I)
DELETE   = re.compile(r"delete|remove", re.I)
SEARCH   = re.compile(r"grep|glob|search|find|list|\bls\b|tree|codebase", re.I)
LOOK     = re.compile(r"read|view|open|get_?file|cat", re.I)

EVENT_STEP = {
    "session_start": "START", "session_end": "END",
    "user_prompt": "PROMPT", "permission_request": "ASK",
    "agent_reasoning": "THINK", "agent_response": "RESPOND",
    "message_displayed": "RESPOND", "turn_end": "RESPOND",
    "plan_created": "PLAN", "plan_completed": "PLAN",
    "compaction": "COMPACT", "shell_stall": "STALL",
    "subagent_start": "DELEGATE", "subagent_stop": "RETURN",
    "delegation": "DELEGATE",
}

def command_step(c):
    for rx, s in ((TEST, "TEST"), (INSTALL, "INSTALL"), (BUILD, "BUILD"),
                  (NET_CMD, "NETWORK"), (DEL_CMD, "DELETE"), (VCS, "VCS"),
                  (LOOK_C, "LOOK"), (SRCH_C, "SEARCH")):
        if rx.search(c):
            return s
    return "RUN"

def tool_step(name, mcp):
    for rx, s in ((DELEGATE, "DELEGATE"), (PLAN, "PLAN"), (NETWORK, "NETWORK"),
                  (SHELL, "RUN"), (CHANGE, "CHANGE"), (CREATE, "CREATE"),
                  (DELETE, "DELETE"), (SEARCH, "SEARCH"), (LOOK, "LOOK")):
        if rx.search(name):
            return s
    return "EXTERNAL" if mcp else "OTHER"

def step_of(r):
    if r.get("hung_seconds"):
        return "STALL"
    cmd, name = (r.get("command") or "").strip(), (r.get("tool_name") or "").strip()
    if cmd:  return command_step(cmd)
    if name: return tool_step(name, r.get("mcp_server"))
    if r.get("mcp_server"): return "EXTERNAL"
    return EVENT_STEP.get(r.get("observable_type") or "")

def failed(r):
    if r.get("success") is False:
        return True
    code = r.get("exit_code")
    return code not in (None, "") and str(code) not in ("0", "0.0")

def load():
    if not (URL and KEY):
        sys.exit("set SUPABASE_URL and SUPABASE_KEY (source ~/.signal-env)")
    rows, start = [], 0
    while True:
        req = urllib.request.Request(
            f"{URL}/rest/v1/observables?select={COLS}&order=occurred_at.asc",
            headers={"apikey": KEY, "Authorization": f"Bearer {KEY}",
                     "Range": f"{start}-{start + 999}"})
        with urllib.request.urlopen(req, timeout=90) as resp:
            batch = json.load(resp)
        rows += batch
        print(f"\r  loaded {len(rows):,} records", end="", file=sys.stderr)
        if len(batch) < 1000:
            break
        start += 1000
    print(file=sys.stderr)
    return rows

def build(rows):
    by_session = defaultdict(list)
    for r in rows:
        if r.get("session_id") and r.get("collector") != "fs_watcher":
            by_session[r["session_id"]].append(r)
    paths = {}
    for sid, events in by_session.items():
        events.sort(key=lambda r: (r.get("occurred_at") or "", r.get("sequence_num") or 0))
        steps, by_use = [], {}
        tool = next((r["tool"] for r in events if r.get("tool") in AGENTS), None)
        for r in events:
            s = step_of(r)
            if not s:
                continue
            bad, uid = failed(r), r.get("tool_use_id")
            if uid and uid in by_use:
                steps[by_use[uid]]["failed"] |= bad
                continue
            if (r.get("observable_type") == "tool_result" and not uid
                    and steps and steps[-1]["step"] == s):
                steps[-1]["failed"] |= bad
                continue
            if s == "RESPOND" and steps and steps[-1]["step"] == "RESPOND":
                continue
            steps.append({"step": s, "failed": bad, "name": r.get("tool_name"),
                          "at": r.get("occurred_at"), "turn": r.get("turn_id"),
                          "agent": r.get("agent_id")})
            if uid:
                by_use[uid] = len(steps) - 1
        if steps:
            paths[sid] = {"tool": tool, "steps": steps}
    return paths

def render(steps, limit=40):
    out, prev, n = [], None, 0
    for s in steps:
        label = s["step"] + ("!" if s["failed"] else "")
        if label == prev:
            n += 1; continue
        if prev: out.append(prev + (f" x{n}" if n > 1 else ""))
        prev, n = label, 1
    if prev: out.append(prev + (f" x{n}" if n > 1 else ""))
    return " -> ".join(out[:limit]) + (" -> ..." if len(out) > limit else "")

def coverage(paths):
    per = defaultdict(Counter)
    for p in paths.values():
        for s in p["steps"]:
            per[p["tool"] or "unattributed"][s["step"]] += 1
    print(f"\n{len(paths)} sessions turned into paths\n")
    print(f"{'agent':<14}{'sessions':>9}{'steps':>8}{'unmapped':>10}   most common steps")
    print("-" * 96)
    for tool in sorted(per, key=lambda t: -sum(per[t].values())):
        c = per[tool]; total = sum(c.values())
        n = sum(1 for p in paths.values() if (p["tool"] or "unattributed") == tool)
        top = ", ".join(f"{k} {v}" for k, v in c.most_common(6))
        print(f"{tool:<14}{n:>9}{total:>8}{c['OTHER'] / total:>9.0%}   {top}")

def unmapped(paths):
    names = defaultdict(Counter)
    for p in paths.values():
        for s in p["steps"]:
            if s["step"] == "OTHER":
                names[p["tool"] or "unattributed"][s["name"] or "?"] += 1
    if not names:
        print("\nevery action maps onto the vocabulary"); return
    print("\nactions the vocabulary does not yet cover\n")
    for tool, c in names.items():
        print(f"{tool}: " + ", ".join(f"{k} ({v})" for k, v in c.most_common(15)))

def show(paths, n, tool):
    chosen = sorted(((sid, p) for sid, p in paths.items() if not tool or p["tool"] == tool),
                    key=lambda x: -len(x[1]["steps"]))
    step = max(1, len(chosen) // max(n, 1))
    for sid, p in chosen[::step][:n]:
        print(f"\n{p['tool'] or 'unattributed'}  {sid[:12]}  {len(p['steps'])} steps")
        print("  " + render(p["steps"]))

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--show", type=int, default=0)
    ap.add_argument("--tool")
    ap.add_argument("--unmapped", action="store_true")
    a = ap.parse_args()
    paths = build(load())
    coverage(paths)
    if a.unmapped: unmapped(paths)
    if a.show: show(paths, a.show, a.tool)

if __name__ == "__main__":
    main()
