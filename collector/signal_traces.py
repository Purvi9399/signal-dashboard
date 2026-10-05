#!/usr/bin/env python3
"""
Signal traces

Builds the tree for every session and writes it to Supabase.

A path is one agent's steps in order. A trace is the whole execution:
the session, its turns, each step, the subagent lanes branching off the
delegation that created them, and hanging off each step the effects it
caused on disk or in version control.

Paths become a view over this rather than a second store, so the two
cannot drift apart.

Three things this does that the path builder does not:

  links each lane to the delegation step that opened it, which turns a
  flat list of lanes into a tree;

  attaches effects to the step that caused them, by matching a file
  change on disk to a step that touched the same path moments earlier.
  What is left over is change no agent accounts for, which is usually a
  person editing directly;

  joins the terminal wrapper's session to the agent's own, since the
  wrapper assigns its own id and one run otherwise looks like two.

    python3 signal_traces.py --dry-run     build and summarise, write nothing
    python3 signal_traces.py               build and write
    python3 signal_traces.py --session ID  one session, printed as a tree
"""

import argparse, json, os, sys, urllib.error, urllib.request
from collections import Counter, defaultdict
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from signal_paths import step_of, failed, AGENTS          # one vocabulary, not two

URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
KEY = os.environ.get("SUPABASE_KEY", "")

COLS = ",".join([
    "observable_id", "session_id", "tool", "collector", "observable_type",
    "occurred_at", "sequence_num", "turn_id", "agent_id", "agent_type",
    "tool_use_id", "tool_name", "tool_arguments", "tool_duration_ms",
    "command", "exit_code", "mcp_server", "success", "hung_seconds",
    "operator_username", "operator_email", "workspace", "cwd", "repo_name",
    "file_path", "file_change_kind", "chars_added", "chars_removed",
    "content_hash", "reverted_to_earlier", "attribution", "sensitivity_tier",
    "secret_detected", "outside_workspace", "policy_rule_matched",
    "permission_decision", "total_tokens", "cost_usd", "cost_is_estimated",
    "status", "git_commit",
])

# how long after a step a file change may still belong to it
EFFECT_WINDOW_S = 20
EFFECT_LEAD_S = 3          # allow a little clock skew between collectors
FILE_EVENTS = {"file_create", "file_modify", "file_delete", "file_rename"}
VCS_EVENTS = {"commit", "pull_request", "repo_state", "repo_metadata"}


def ts(v):
    if not v:
        return None
    try:
        return datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except ValueError:
        return None


def get(path, params):
    rows, start = [], 0
    while True:
        req = urllib.request.Request(
            f"{URL}/rest/v1/{path}?{params}",
            headers={"apikey": KEY, "Authorization": f"Bearer {KEY}",
                     "Range": f"{start}-{start + 999}"})
        with urllib.request.urlopen(req, timeout=120) as r:
            batch = json.load(r)
        rows += batch
        print(f"\r  loaded {len(rows):,}", end="", file=sys.stderr)
        if len(batch) < 1000:
            break
        start += 1000
    print(file=sys.stderr)
    return rows


def post(table, rows, conflict):
    """Upsert in batches; ids are stable so rebuilding is safe."""
    sent = 0
    for i in range(0, len(rows), 500):
        body = json.dumps(rows[i:i + 500], default=str).encode()
        req = urllib.request.Request(
            f"{URL}/rest/v1/{table}?on_conflict={conflict}", data=body,
            headers={"apikey": KEY, "Authorization": f"Bearer {KEY}",
                     "Content-Type": "application/json",
                     "Prefer": "resolution=merge-duplicates,return=minimal"},
            method="POST")
        try:
            urllib.request.urlopen(req, timeout=120).read()
            sent += len(rows[i:i + 500])
        except urllib.error.HTTPError as e:
            print(f"\n  {table}: {e.code} {e.read()[:300].decode()}", file=sys.stderr)
            break
        print(f"\r  {table}: {sent:,}", end="", file=sys.stderr)
    print(file=sys.stderr)
    return sent


# ------------------------------------------------------------------ build

def build(rows):
    agent_rows, effects, wrapper = [], [], []
    for r in rows:
        t = r.get("observable_type") or ""
        if t in FILE_EVENTS or (r.get("collector") == "fs_watcher" and r.get("file_path")):
            effects.append(r)
            continue
        if t in VCS_EVENTS or r.get("collector") == "webhook":
            effects.append(r)
            continue
        if r.get("collector") == "fs_watcher":      # transcripts, not workspace effects
            continue
        agent_rows.append(r)

    # ---- lanes, as in the path builder ----
    by_lane = defaultdict(list)
    for r in agent_rows:
        if not r.get("session_id"):
            continue
        t = r.get("observable_type") or ""
        lane = "main" if "subagent" in t else (r.get("agent_id") or "main")
        by_lane[(r["session_id"], lane)].append(r)

    sessions = {}
    steps = []
    for (sid, lane), events in sorted(by_lane.items()):
        events.sort(key=lambda r: (r.get("occurred_at") or "", r.get("sequence_num") or 0))
        s = sessions.setdefault(sid, {"id": sid, "lanes": {}, "rows": []})
        s["rows"] += events
        lane_steps, by_use, seq = [], {}, 0
        for r in events:
            t = r.get("observable_type") or ""
            if t == "subagent_start":
                if not lane_steps or lane_steps[-1]["step"] != "DELEGATE":
                    seq += 1
                    lane_steps.append(mk_step(sid, lane, seq, "DELEGATE", r))
                continue
            kind = step_of(r)
            if not kind:
                continue
            bad, uid = failed(r), r.get("tool_use_id")
            if uid and uid in by_use:
                prev = by_use[uid]
                prev["failed"] = prev["failed"] or bad
                prev["ended_at"] = r.get("occurred_at")
                prev["exit_code"] = prev["exit_code"] or r.get("exit_code")
                prev["duration_ms"] = prev["duration_ms"] or r.get("tool_duration_ms")
                continue
            if t == "tool_result" and not uid and lane_steps and lane_steps[-1]["step"] == kind:
                lane_steps[-1]["failed"] = lane_steps[-1]["failed"] or bad
                lane_steps[-1]["ended_at"] = r.get("occurred_at")
                continue
            if kind in ("RESPOND", "THINK") and lane_steps and lane_steps[-1]["step"] == kind:
                continue
            seq += 1
            st = mk_step(sid, lane, seq, kind, r)
            st["failed"] = bad
            lane_steps.append(st)
            if uid:
                by_use[uid] = st
        if lane_steps:
            s["lanes"][lane] = lane_steps
            steps += lane_steps

    link_lanes(sessions)
    approval_waits(sessions)
    fx = attach_effects(steps, effects)
    link_wrapper(sessions)
    return sessions, steps, fx


def mk_step(sid, lane, seq, kind, r):
    return {
        "step_id": f"{sid}:{lane}:{seq}",
        "session_id": sid, "lane": lane, "seq": seq, "step": kind,
        "tool_name": r.get("tool_name"), "turn_id": r.get("turn_id"),
        "agent_id": r.get("agent_id"), "parent_step_id": None,
        "started_at": r.get("occurred_at"), "ended_at": r.get("occurred_at"),
        "duration_ms": r.get("tool_duration_ms"), "failed": False,
        "exit_code": str(r.get("exit_code")) if r.get("exit_code") is not None else None,
        "command": (r.get("command") or "")[:400] or None,
        "file_path": r.get("file_path"), "mcp_server": r.get("mcp_server"),
        "sensitivity_tier": r.get("sensitivity_tier"),
        "policy_rule_matched": r.get("policy_rule_matched"),
        "approval_wait_ms": None, "decided_by": None,
        "tokens": r.get("total_tokens"), "cost_usd": r.get("cost_usd"),
        "collector": r.get("collector"), "observable_id": r.get("observable_id"),
    }


def link_lanes(sessions):
    """Point each subagent lane at the delegation step that opened it."""
    for s in sessions.values():
        main = s["lanes"].get("main") or []
        handoffs = [x for x in main if x["step"] == "DELEGATE"]
        for lane, lane_steps in s["lanes"].items():
            if lane == "main" or not lane_steps:
                continue
            start = ts(lane_steps[0]["started_at"])
            best = None
            for h in handoffs:
                h_at = ts(h["started_at"])
                if h_at and start and h_at <= start:
                    if best is None or h_at > ts(best["started_at"]):
                        best = h
            if best is None and handoffs:
                best = handoffs[0]
            if best:
                for st in lane_steps:
                    st["parent_step_id"] = best["step_id"]


def approval_waits(sessions):
    """How long a person took between the agent asking and the action.

    A short wait is not an approval read carefully. Where nothing follows
    the request in the same turn, the request was most likely declined,
    since no agent emits a denial event.
    """
    for s in sessions.values():
        for lane_steps in s["lanes"].values():
            for i, st in enumerate(lane_steps):
                if st["step"] != "ASK":
                    continue
                nxt = next((x for x in lane_steps[i + 1:]
                            if x["step"] not in ("ASK", "RESPOND", "THINK")), None)
                if nxt and nxt.get("turn_id") == st.get("turn_id"):
                    a, b = ts(st["started_at"]), ts(nxt["started_at"])
                    if a and b:
                        st["approval_wait_ms"] = int((b - a).total_seconds() * 1000)
                        st["decided_by"] = "human_approved"
                        nxt["decided_by"] = "human_approved"
                else:
                    st["decided_by"] = "declined_inferred"
            for st in lane_steps:
                if st["policy_rule_matched"]:
                    st["decided_by"] = "policy_blocked"
                elif st["decided_by"] is None:
                    st["decided_by"] = "agent_alone"


def attach_effects(steps, effects):
    """Match a change on disk to the step that touched the same path.

    The watcher records what changed, never who changed it. A step that
    touched the same file moments earlier is the cause. What matches
    nothing is change no agent accounts for.
    """
    index = defaultdict(list)
    for st in steps:
        if st["file_path"]:
            index[st["file_path"]].append(st)
        elif st["command"]:
            index[("cmd", st["session_id"])].append(st)
    for v in index.values():
        v.sort(key=lambda x: x["started_at"] or "")

    out = []
    for e in effects:
        at = ts(e.get("occurred_at"))
        path = e.get("file_path")
        owner = None
        if at and path:
            for st in index.get(path, []):
                s_at = ts(st["started_at"])
                if not s_at:
                    continue
                lag = (at - s_at).total_seconds()
                if -EFFECT_LEAD_S <= lag <= EFFECT_WINDOW_S:
                    owner = st
                elif lag < -EFFECT_LEAD_S:
                    break
        t = e.get("observable_type") or ""
        out.append({
            "effect_id": e.get("observable_id"),
            "session_id": (owner or {}).get("session_id") or e.get("session_id"),
            "step_id": (owner or {}).get("step_id"),
            "kind": "commit" if t in VCS_EVENTS else (e.get("file_change_kind") or t or "change"),
            "file_path": path, "content_hash": e.get("content_hash"),
            "chars_added": e.get("chars_added"), "chars_removed": e.get("chars_removed"),
            "reverted": bool(e.get("reverted_to_earlier")),
            "sensitivity_tier": e.get("sensitivity_tier"),
            "secret_detected": bool(e.get("secret_detected")),
            "outside_workspace": bool(e.get("outside_workspace")),
            "git_commit": e.get("git_commit"),
            "occurred_at": e.get("occurred_at"),
            "lag_s": round((at - ts(owner["started_at"])).total_seconds(), 1) if owner and at else None,
            "attributed_to": "agent" if owner else "unaccounted",
            "observable_id": e.get("observable_id"),
        })
    return out


def link_wrapper(sessions):
    """The wrapper names its own session, so one run looks like two."""
    info = {}
    for sid, s in sessions.items():
        times = [ts(r.get("occurred_at")) for r in s["rows"]]
        times = [t for t in times if t]
        cw = next((r.get("cwd") or r.get("workspace") for r in s["rows"]
                   if r.get("cwd") or r.get("workspace")), None)
        colls = {r.get("collector") for r in s["rows"]}
        info[sid] = {"start": min(times) if times else None,
                     "end": max(times) if times else None,
                     "cwd": cw, "wrapper_only": colls == {"cli_wrapper"}}
    wrappers = [k for k, v in info.items() if v["wrapper_only"]]
    for w in wrappers:
        a = info[w]
        for sid, b in info.items():
            if sid == w or b["wrapper_only"] or not (a["start"] and b["start"]):
                continue
            overlap = a["start"] <= b["end"] and b["start"] <= a["end"]
            same = a["cwd"] and b["cwd"] and a["cwd"] == b["cwd"]
            if overlap and same:
                sessions[sid]["wrapper_session_id"] = w
                sessions[w]["wrapper_session_id"] = sid
                break


def session_rows(sessions, steps, fx):
    by_session_steps = defaultdict(list)
    for st in steps:
        by_session_steps[st["session_id"]].append(st)
    by_session_fx = defaultdict(list)
    for e in fx:
        if e["session_id"]:
            by_session_fx[e["session_id"]].append(e)

    out = []
    for sid, s in sessions.items():
        rows = s["rows"]
        sts = by_session_steps[sid]
        times = [ts(r.get("occurred_at")) for r in rows]
        times = [t for t in times if t]
        tool = next((r["tool"] for r in rows if r.get("tool") in AGENTS), None)
        out.append({
            "session_id": sid, "tool": tool,
            "operator": next((r.get("operator_username") or r.get("operator_email")
                              for r in rows if r.get("operator_username") or r.get("operator_email")), None),
            "workspace": next((r.get("workspace") for r in rows if r.get("workspace")), None),
            "repo_name": next((r.get("repo_name") for r in rows if r.get("repo_name")), None),
            "started_at": min(times).isoformat() if times else None,
            "ended_at": max(times).isoformat() if times else None,
            "duration_s": round((max(times) - min(times)).total_seconds(), 1) if times else None,
            "turns": len({r.get("turn_id") for r in rows if r.get("turn_id")}),
            "lanes": len(s["lanes"]), "steps": len(sts),
            "agents": len({r.get("agent_id") for r in rows if r.get("agent_id")}),
            "failed_steps": sum(1 for x in sts if x["failed"]),
            "approvals": sum(1 for x in sts if x["step"] == "ASK"),
            "declined_inferred": sum(1 for x in sts if x["decided_by"] == "declined_inferred"),
            "blocked": sum(1 for x in sts if x["decided_by"] == "policy_blocked"),
            "effects": len(by_session_fx[sid]),
            "unaccounted_effects": sum(1 for e in by_session_fx[sid]
                                       if e["attributed_to"] == "unaccounted"),
            "tokens": sum(int(r.get("total_tokens") or 0) for r in rows),
            "cost_usd": round(sum(float(r.get("cost_usd") or 0) for r in rows), 4),
            "wrapper_session_id": s.get("wrapper_session_id"),
        })
    return out


def print_tree(sessions, steps, fx, sid):
    s = sessions.get(sid)
    if not s:
        print(f"no session {sid}")
        return
    per_step = defaultdict(list)
    for e in fx:
        if e["step_id"]:
            per_step[e["step_id"]].append(e)
    print(f"\nsession {sid}   {len(s['lanes'])} lanes")
    for lane in ["main"] + [l for l in s["lanes"] if l != "main"]:
        if lane not in s["lanes"]:
            continue
        print(f"\n  lane {lane}")
        for st in s["lanes"][lane]:
            mark = "!" if st["failed"] else " "
            wait = f"  waited {st['approval_wait_ms']}ms" if st["approval_wait_ms"] else ""
            print(f"    {st['seq']:>3} {st['step']:<9}{mark} {(st['tool_name'] or st['command'] or '')[:40]:<42}"
                  f"{st['decided_by'] or '':<18}{wait}")
            for e in per_step[st["step_id"]]:
                print(f"        -> {e['kind']:<12} {(e['file_path'] or '')[:46]:<48}"
                      f"+{e['chars_added'] or 0}/-{e['chars_removed'] or 0}  {e['lag_s']}s")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--session")
    a = ap.parse_args()
    if not (URL and KEY):
        sys.exit("set SUPABASE_URL and SUPABASE_KEY (source ~/.signal-env)")

    print("reading observables", file=sys.stderr)
    rows = get("observables", f"select={COLS}&order=occurred_at.asc")
    sessions, steps, fx = build(rows)
    srows = session_rows(sessions, steps, fx)

    attached = sum(1 for e in fx if e["attributed_to"] == "agent")
    print(f"\n{len(sessions):,} sessions, {len(steps):,} steps, "
          f"{sum(len(s['lanes']) for s in sessions.values()):,} lanes")
    print(f"{len(fx):,} effects, {attached:,} attached to a step "
          f"({attached / max(len(fx), 1):.0%}), "
          f"{len(fx) - attached:,} unaccounted for")
    waits = [x["approval_wait_ms"] for x in steps if x["approval_wait_ms"]]
    if waits:
        waits.sort()
        quick = sum(1 for w in waits if w < 1000)
        print(f"{len(waits):,} approvals measured, median "
              f"{waits[len(waits)//2] / 1000:.1f}s, {quick} under a second")
    print("decided by:", dict(Counter(x["decided_by"] for x in steps).most_common()))
    linked = sum(1 for s in srows if s["wrapper_session_id"])
    print(f"{linked} sessions joined to a terminal session")

    if a.session:
        print_tree(sessions, steps, fx, a.session)
        return
    if a.dry_run:
        print("\ndry run, nothing written")
        return

    print("\nwriting", file=sys.stderr)
    post("trace_sessions", srows, "session_id")
    post("trace_steps", steps, "step_id")
    post("trace_effects", [e for e in fx if e["effect_id"]], "effect_id")


if __name__ == "__main__":
    main()
