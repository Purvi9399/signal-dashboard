#!/usr/bin/env python3
"""
Signal live forwarder

Tails the collectors' output and appends each new event to a Redis
stream, keeping only the last few seconds. Collectors are not modified:
they keep writing JSONL, and this reads what they write.

    python3 signal_live.py              forward live events
    python3 signal_live.py --peek       print what is in the window now
    python3 signal_live.py --window 30  keep 30 seconds instead of 20
"""

import argparse
import glob
import json
import os
import sys
import time

import redis

HOME = os.path.expanduser("~")
STREAM = "signal:live"
DIRS = [os.path.join(HOME, ".signal", "sessions")] + [
    os.path.join(HOME, d, "signal_sessions") for d in (
        "bench-claude", "bench-cursor", "bench-codex", "bench-copilot",
        "bench-antigravity", "sec-test", "idp-test",
        "agent-marketplace/signal-coding-agent-collector")
]

TOOL = {"claude": "claude-code", "claude-code": "claude-code",
        "cursor": "cursor", "codex": "codex", "gh": "copilot",
        "copilot": "copilot", "antigravity": "antigravity"}


def files():
    out, seen = [], set()
    for d in DIRS:
        for f in glob.glob(os.path.join(d, "*.jsonl")):
            name = os.path.basename(f)
            # raw payloads and wire logs duplicate the normalised rows
            if name.endswith("-RAW.jsonl") or name.endswith(".wire.jsonl"):
                continue
            real = os.path.realpath(f)
            if real in seen:
                continue
            seen.add(real)
            out.append(real)
    return out


def flatten(row):
    """A stream entry holds strings only, so keep the fields the live
    checks need and nothing else."""
    d = row.get("detail") or {}
    tool = TOOL.get(str(row.get("agent_tool", "")).lower(),
                    row.get("agent_tool") or "unknown")
    f = {
        "at":        row.get("timestamp") or "",
        "session":   row.get("agent_id") or "",
        "tool":      tool,
        "collector": row.get("collector") or "",
        "type":      row.get("interaction_type") or "",
        "tool_name": d.get("tool") or d.get("tool_name") or "",
        "command":   str(d.get("command") or "")[:200],
        "file":      str(d.get("file_path") or d.get("path") or "")[:200],
        "tokens":    str(row.get("tokens_total") or 0),
        "tier":      str(row.get("sensitivity_tier") or 1),
        "success":   "0" if row.get("success") is False else "1",
        "exit":      str(d.get("exit_code") if d.get("exit_code") is not None else ""),
        "rule":      str(d.get("matched_rule") or ""),
        "error":     str(row.get("error_type") or "")[:120],
    }
    return {k: v for k, v in f.items() if v not in ("", None)}


LOCK = "signal:live:forwarder"


def forward(r, window):
    if not r.set(LOCK, os.getpid(), nx=True, ex=10):
        holder = r.get(LOCK)
        sys.exit(f"another forwarder is already running (pid {holder.decode() if holder else '?'}); "
                 f"stop it first, or wait ten seconds if it has just exited")
    offsets = {}
    # begin at the end of every existing file: live means from now on
    for f in files():
        try:
            offsets[f] = os.path.getsize(f)
        except OSError:
            pass

    print(f"forwarding to {STREAM}, keeping the last {window} seconds")
    print(f"watching {len(offsets)} files across {len(DIRS)} folders\n")

    while True:
        for f in files():
            try:
                size = os.path.getsize(f)
            except OSError:
                continue
            start = offsets.get(f, 0)
            if size < start:          # file was truncated or replaced
                start = 0
            if size == start:
                continue
            with open(f, "r", errors="replace") as fh:
                fh.seek(start)
                chunk = fh.read()
                offsets[f] = fh.tell()
            for line in chunk.splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                fields = flatten(row)
                if not fields:
                    continue
                cutoff = int(time.time() * 1000) - window * 1000
                r.xadd(STREAM, fields, minid=cutoff, approximate=False)
                print(f"  {fields.get('tool','?'):<12} "
                      f"{fields.get('type','?'):<22} "
                      f"{(fields.get('tool_name') or fields.get('file') or fields.get('command') or '')[:48]}")

        r.set(LOCK, os.getpid(), ex=10)

        # trim even when quiet, so an idle window empties itself
        cutoff = int(time.time() * 1000) - window * 1000
        r.xtrim(STREAM, minid=cutoff, approximate=False)
        time.sleep(0.5)


def peek(r):
    entries = r.xrange(STREAM)
    if not entries:
        print("the window is empty")
        return
    now = time.time() * 1000
    print(f"{len(entries)} events in the window\n")
    for eid, f in entries:
        age = (now - int(eid.decode().split("-")[0])) / 1000
        f = {k.decode(): v.decode() for k, v in f.items()}
        print(f"  {age:5.1f}s ago  {f.get('tool','?'):<12} {f.get('type','?'):<22} "
              f"{(f.get('tool_name') or f.get('file') or f.get('command') or '')[:40]}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--window", type=int, default=20)
    ap.add_argument("--peek", action="store_true")
    a = ap.parse_args()
    r = redis.Redis(host="localhost", port=6379)
    r.ping()
    if a.peek:
        peek(r)
    else:
        try:
            forward(r, a.window)
        except KeyboardInterrupt:
            print("\nstopped")
        finally:
            if r.get(LOCK) == str(os.getpid()).encode():
                r.delete(LOCK)
    return 0


if __name__ == "__main__":
    sys.exit(main())
