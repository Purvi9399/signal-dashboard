#!/usr/bin/env python3
"""
Signal Collect — agent identity resolution

Reads captured session data and answers two questions Sundar asked for:

  1. Is this session single-agent or multi-agent?
  2. What are all the agent IDs involved, and how do they relate?

Two ways a session becomes multi-agent:

  PLATFORM-DETERMINED  the tool spins up subagents on its own. Only traceable
                       as one tool triggering another, so we look for spawn
                       events, nested identities, and delegation tool calls.

  HUMAN-DETERMINED     someone configured several agents deliberately. Shows up
                       as separate sessions sharing a workspace and overlapping
                       in time, or as named roles in the config.

    python3 signal_agents.py                     # scan the usual places
    python3 signal_agents.py <dir> [<dir> ...]   # scan specific folders
    python3 signal_agents.py --json              # machine readable

Reads raw hook payloads (*RAW.jsonl), observable rows (*.jsonl), and agent
transcripts where a path was captured.
"""

import glob
import json
import os
import re
import sys
from collections import defaultdict
from datetime import datetime

HOME = os.path.expanduser("~")
DEFAULT_DIRS = [os.path.join(HOME, ".signal", "sessions"), "signal_sessions",
                "signal_sessions/cursor", "."]

# tools whose invocation means "this agent is delegating to another agent"
DELEGATION_TOOLS = {"task", "agent", "invoke_subagent", "spawn_agent",
                    "dispatch_agent", "run_subagent"}

# hook events that mark a spawn or a spawn ending
SPAWN_EVENTS = {"subagentstart", "subagentstop", "subagent_start",
                "subagent_stop", "subagent_completed", "taskcreated"}

# overlap window for treating separate sessions as one concurrent setup
CONCURRENT_WINDOW_S = 300


def parse_ts(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def load(dirs):
    """Return {session_key: {...facts...}} from every captured source."""
    sessions = defaultdict(lambda: {
        "tool": "unknown", "workspace": "", "operator": "",
        "agent_ids": set(), "agent_types": {}, "parent_of": {},
        "spawn_events": [], "delegation_calls": [], "transcripts": set(),
        "first_ts": None, "last_ts": None, "event_count": 0,
        "conversation_ids": set(), "evidence": [],
    })

    def touch(s, ts):
        t = parse_ts(ts)
        if not t:
            return
        if s["first_ts"] is None or t < s["first_ts"]:
            s["first_ts"] = t
        if s["last_ts"] is None or t > s["last_ts"]:
            s["last_ts"] = t

    for d in dirs:
        if not os.path.isdir(d):
            continue
        for path in glob.glob(os.path.join(d, "**", "*.jsonl"), recursive=True):
            if path.endswith(".wire.jsonl"):
                continue
            base = os.path.basename(path).lower()
            for line in open(path, errors="replace"):
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue

                # ---- raw hook payload ----
                if "payload" in rec and isinstance(rec["payload"], dict):
                    p = rec["payload"]
                    sid = (p.get("session_id") or p.get("conversation_id") or "?")
                    s = sessions[sid]
                    s["event_count"] += 1
                    touch(s, rec.get("received_at"))
                    tool = "cursor" if "cursor" in base else "claude-code"
                    s["tool"] = tool
                    roots = p.get("workspace_roots") or []
                    if roots:
                        s["workspace"] = roots[0]
                    elif p.get("cwd"):
                        s["workspace"] = p["cwd"]
                    if p.get("user_email"):
                        s["operator"] = p["user_email"]
                    if p.get("conversation_id"):
                        s["conversation_ids"].add(p["conversation_id"])

                    ev = str(p.get("hook_event_name", "")).lower()
                    aid = p.get("agent_id") or ""
                    if aid:
                        s["agent_ids"].add(aid)
                        if p.get("agent_type"):
                            s["agent_types"][aid] = p["agent_type"]
                    if ev in SPAWN_EVENTS:
                        s["spawn_events"].append({
                            "event": p.get("hook_event_name"),
                            "agent_id": aid,
                            "agent_type": p.get("agent_type", ""),
                            "parent_prompt": p.get("prompt_id", ""),
                        })
                        if aid:
                            s["evidence"].append(
                                f"{p.get('hook_event_name')} for agent {aid[:10]}")
                    if p.get("agent_transcript_path"):
                        s["transcripts"].add(p["agent_transcript_path"])

                    tname = str(p.get("tool_name", "")).lower()
                    if tname in DELEGATION_TOOLS:
                        s["delegation_calls"].append({
                            "tool": p.get("tool_name"),
                            "input": p.get("tool_input", {}),
                        })
                        s["evidence"].append(f"delegation tool call: {p.get('tool_name')}")
                    continue

                # ---- normalized observable row ----
                if "observable_id" in rec:
                    sid = rec.get("agent_id") or "?"
                    s = sessions[sid]
                    s["event_count"] += 1
                    touch(s, rec.get("timestamp"))
                    if rec.get("agent_tool"):
                        s["tool"] = rec["agent_tool"]
                    if rec.get("workspace"):
                        s["workspace"] = rec["workspace"]
                    if rec.get("cwd") and not s["workspace"]:
                        s["workspace"] = rec["cwd"]
                    if rec.get("operator_email") or rec.get("operator"):
                        s["operator"] = rec.get("operator_email") or rec["operator"]
                    d_ = rec.get("detail") or {}
                    pa = rec.get("parent_agent_id") or d_.get("agent_id") or ""
                    if pa:
                        s["agent_ids"].add(pa)
                        if d_.get("agent_type"):
                            s["agent_types"][pa] = d_["agent_type"]
                    it = str(rec.get("interaction_type", "")).lower()
                    if "subagent" in it:
                        s["spawn_events"].append({
                            "event": rec.get("interaction_type"),
                            "agent_id": pa, "agent_type": d_.get("agent_type", ""),
                            "parent_prompt": rec.get("prompt_id", ""),
                        })
                    if d_.get("agent_transcript_path"):
                        s["transcripts"].add(d_["agent_transcript_path"])
                    if str(d_.get("tool", "")).lower() in DELEGATION_TOOLS:
                        s["delegation_calls"].append({"tool": d_.get("tool"),
                                                      "input": d_.get("arguments", {})})
    return sessions


def scan_transcripts(session):
    """A subagent transcript is independent evidence of a second agent."""
    found = []
    for path in session["transcripts"]:
        if not path or not os.path.exists(path):
            found.append({"path": path, "exists": False})
            continue
        agents, lines = set(), 0
        try:
            for line in open(path, errors="replace"):
                lines += 1
                for m in re.findall(r'"agent(?:_id|Id)"\s*:\s*"([^"]+)"', line):
                    agents.add(m)
        except OSError:
            pass
        found.append({"path": path, "exists": True, "lines": lines,
                      "agent_ids": sorted(agents)})
    return found


def classify(sid, s):
    """Decide single vs multi agent, and say why."""
    reasons, mode = [], "single_agent"

    spawns = [e for e in s["spawn_events"]
              if "start" in str(e["event"]).lower()]
    if spawns:
        mode = "multi_agent"
        reasons.append(f"{len(spawns)} subagent spawn events")
    if s["delegation_calls"]:
        mode = "multi_agent"
        reasons.append(f"{len(s['delegation_calls'])} delegation tool calls")
    if len(s["transcripts"]) > 0:
        mode = "multi_agent"
        reasons.append(f"{len(s['transcripts'])} separate agent transcripts")
    if len(s["agent_ids"]) > 1:
        mode = "multi_agent"
        reasons.append(f"{len(s['agent_ids'])} distinct agent ids")
    elif len(s["agent_ids"]) == 1 and mode == "single_agent":
        reasons.append("one agent id present, no spawn evidence")

    if mode == "single_agent" and not reasons:
        reasons.append("no spawn, delegation or nested identity found")

    return {
        "session_id": sid,
        "tool": s["tool"],
        "mode": mode,
        "attribution": "platform_determined" if mode == "multi_agent" else "n/a",
        "agent_ids": sorted(s["agent_ids"]),
        "agent_types": s["agent_types"],
        "spawn_count": len(spawns),
        "delegation_calls": len(s["delegation_calls"]),
        "transcripts": scan_transcripts(s),
        "workspace": s["workspace"],
        "operator": s["operator"],
        "events": s["event_count"],
        "started": s["first_ts"].isoformat() if s["first_ts"] else None,
        "ended": s["last_ts"].isoformat() if s["last_ts"] else None,
        "reasons": reasons,
    }


def find_concurrent(results):
    """Human-determined multi-agent: separate sessions, same workspace, overlapping."""
    groups = defaultdict(list)
    for r in results:
        if r["workspace"] and r["started"]:
            groups[r["workspace"]].append(r)

    out = []
    for ws, rs in groups.items():
        if len(rs) < 2:
            continue
        rs.sort(key=lambda x: x["started"])
        cluster = [rs[0]]
        for r in rs[1:]:
            prev_end = parse_ts(cluster[-1]["ended"] or cluster[-1]["started"])
            this_start = parse_ts(r["started"])
            if prev_end and this_start and \
               (this_start - prev_end).total_seconds() < CONCURRENT_WINDOW_S:
                cluster.append(r)
            else:
                if len(cluster) > 1:
                    out.append((ws, list(cluster)))
                cluster = [r]
        if len(cluster) > 1:
            out.append((ws, cluster))
    return out


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    as_json = "--json" in sys.argv
    dirs = args or DEFAULT_DIRS

    sessions = load(dirs)
    if not sessions:
        print("No captured sessions found. Looked in:")
        for d in dirs:
            print("   " + d)
        return 1

    results = [classify(sid, s) for sid, s in sessions.items()
               if s["event_count"] > 0]
    results.sort(key=lambda r: (-r["events"], r["session_id"]))
    concurrent = find_concurrent(results)

    if as_json:
        print(json.dumps({"sessions": results,
                          "concurrent_groups": [
                              {"workspace": ws,
                               "session_ids": [r["session_id"] for r in rs]}
                              for ws, rs in concurrent]},
                         indent=2, default=str))
        return 0

    multi = [r for r in results if r["mode"] == "multi_agent"]
    print(f"\n{len(results)} sessions   "
          f"{len(multi)} multi-agent   {len(results)-len(multi)} single-agent\n")

    print(f"{'session':<14} {'tool':<12} {'mode':<13} {'agents':>6} "
          f"{'spawns':>7} {'events':>7}  why")
    print("-" * 108)
    for r in results:
        print(f"{r['session_id'][:12]:<14} {r['tool'][:11]:<12} {r['mode']:<13} "
              f"{len(r['agent_ids']):>6} {r['spawn_count']:>7} {r['events']:>7}  "
              f"{'; '.join(r['reasons'])[:44]}")

    if multi:
        print("\n\nMulti-agent sessions in detail\n")
        for r in multi:
            print(f"  session {r['session_id'][:16]}  ({r['tool']})")
            print(f"    attribution   {r['attribution']}")
            print(f"    agent ids     {', '.join(a[:16] for a in r['agent_ids']) or 'none recorded'}")
            if r["agent_types"]:
                for a, t in r["agent_types"].items():
                    print(f"                  {a[:16]} -> type '{t or 'unnamed'}'")
            print(f"    spawns        {r['spawn_count']}")
            print(f"    delegations   {r['delegation_calls']}")
            for t in r["transcripts"]:
                if t["exists"]:
                    print(f"    transcript    {t['lines']} lines, "
                          f"agent ids inside: {', '.join(a[:12] for a in t['agent_ids']) or 'none'}")
                else:
                    print(f"    transcript    referenced but missing: {t['path'][:60]}")
            for why in r["reasons"]:
                print(f"    evidence      {why}")
            print()

    if concurrent:
        print("\nPossible human-determined multi-agent setups")
        print("(separate sessions, same workspace, overlapping in time)\n")
        for ws, rs in concurrent:
            print(f"  workspace {ws}")
            for r in rs:
                print(f"    {r['session_id'][:16]}  {r['tool']:<12} "
                      f"{r['started']} -> {r['ended']}")
            print()
    else:
        print("\nNo concurrent same-workspace sessions found "
              "(no evidence of a human-configured multi-agent setup).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
