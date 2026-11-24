#!/usr/bin/env python3
"""
Real, runnable demo of the Collector Framework.

- Runs detect() for real on this machine (no mocking).
- activate()s whatever collectors actually detect() True.
- For capture(): live hook traffic isn't available in this offline demo
  (no Claude Code / Cursor session is actively running against this
  process), so we substitute the real captured JSONL payloads checked into
  the repo as the collectors' event source and clearly label that as
  SIMULATED FROM CAPTURED DATA, vs. LIVE for anything that really streamed.
- Prints real RawEvent objects, including raw_payload, to show Claude's
  and Cursor's differently-shaped payloads passing through unmodified.

Data sources used (read-only, never modified):
  projectsetup/backend/events/*.jsonl          -> Claude Code hook payloads
  projectsetup/events/*.jsonl (event_type=...) -> Cursor hook payloads
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import RawEvent
from core.registry import CollectorRegistry
from collectors.claude_code.claude_code_hooks import ClaudeCodeHooksCollector
from collectors.cursor.cursor_hooks import CursorHooksCollector

REPO_ROOT = Path(__file__).resolve().parents[3]
CLAUDE_EVENTS_DIR = REPO_ROOT / "projectsetup" / "backend" / "events"
MIXED_EVENTS_DIR = REPO_ROOT / "projectsetup" / "events"


def load_jsonl(path: Path) -> list[dict]:
    events = []
    if not path.is_file():
        return events
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return events


def load_claude_replay_events() -> list[dict]:
    """Real captured Claude Code hook payloads (have 'hook_event_name')."""
    events: list[dict] = []
    for f in sorted(CLAUDE_EVENTS_DIR.glob("*.jsonl")):
        events.extend(load_jsonl(f))
    # Also pull any Claude-shaped events out of the mixed events dir.
    for f in sorted(MIXED_EVENTS_DIR.glob("*.jsonl")):
        for e in load_jsonl(f):
            if "hook_event_name" in e:
                events.append(e)
    return events


def load_cursor_replay_events() -> list[dict]:
    """Real captured Cursor hook payloads (have 'event_type')."""
    events: list[dict] = []
    for f in sorted(MIXED_EVENTS_DIR.glob("*.jsonl")):
        for e in load_jsonl(f):
            if "event_type" in e:
                events.append(e)
    return events


def banner(text: str) -> None:
    print()
    print("=" * 78)
    print(text)
    print("=" * 78)


def main() -> None:
    banner("COLLECTOR FRAMEWORK — REAL DEMO RUN")

    claude_replay = load_claude_replay_events()
    cursor_replay = load_cursor_replay_events()
    print(f"Loaded {len(claude_replay)} real captured Claude Code events from "
          f"{CLAUDE_EVENTS_DIR.relative_to(REPO_ROOT)} (+ mixed dir)")
    print(f"Loaded {len(cursor_replay)} real captured Cursor events from "
          f"{MIXED_EVENTS_DIR.relative_to(REPO_ROOT)}")

    claude_collector = ClaudeCodeHooksCollector(replay_events=claude_replay)
    cursor_collector = CursorHooksCollector(replay_events=cursor_replay)

    registry = CollectorRegistry()
    registry.register(claude_collector)
    registry.register(cursor_collector)

    banner("STEP 1: detect() — real, on this machine, right now")
    detection = registry.run_detection()
    for cid, detected in detection.items():
        print(f"  {cid:24s} detect() -> {detected}")

    banner("STEP 2: activate() — only collectors that were detected")
    activated = registry.activate_detected(detection)
    print(f"  Activated: {activated}")

    banner("STEP 3: capture() — SIMULATED FROM CAPTURED DATA (see note below)")
    print("  NOTE: no live Claude Code / Cursor session is currently emitting")
    print("  hooks against this demo process, so capture() below is fed from")
    print("  real captured JSONL files on disk rather than a live listener.")
    print("  Each collector is explicitly labeled LIVE or SIMULATED below;")
    print("  in this run both are SIMULATED FROM CAPTURED DATA.")

    mode_labels = {
        "claude_code.hooks": "SIMULATED FROM CAPTURED DATA" if claude_replay else "LIVE (no data)",
        "cursor.hooks": "SIMULATED FROM CAPTURED DATA" if cursor_replay else "LIVE (no data)",
    }
    for cid in activated:
        print(f"  {cid:24s} mode -> {mode_labels.get(cid, 'LIVE')}")

    banner("STEP 4: merged capture() stream — real RawEvent objects")
    count_by_agent: dict[str, int] = {}
    printed = 0
    MAX_PRINT = 14  # keep terminal output representative, not a full dump
    for event in registry.merged_capture():
        count_by_agent[event.agent] = count_by_agent.get(event.agent, 0) + 1
        if printed < MAX_PRINT:
            print(f"\n--- RawEvent #{printed + 1} "
                  f"[{mode_labels.get(event.collector_id, 'LIVE')}] ---")
            print(f"  timestamp     : {event.timestamp}")
            print(f"  collector_id  : {event.collector_id}")
            print(f"  agent         : {event.agent}")
            print(f"  raw_payload   : {json.dumps(event.raw_payload)[:500]}")
            printed += 1

    if sum(count_by_agent.values()) > MAX_PRINT:
        print(f"\n  ... ({sum(count_by_agent.values()) - MAX_PRINT} more RawEvents "
              f"omitted from printout, all captured) ...")

    banner("STEP 5: summary")
    for agent, n in count_by_agent.items():
        print(f"  {agent:14s} -> {n} RawEvents captured")

    if registry.failures:
        print("\n  Failures recorded (isolated, did not crash the registry):")
        for f in registry.failures:
            print(f"    [{f.stage}] {f.collector_id}: {f.error}")
    else:
        print("\n  No collector failures recorded.")

    banner("STEP 6: deactivate()")
    registry.deactivate_all()
    print("  All active collectors deactivated cleanly.")

    banner("DONE")


if __name__ == "__main__":
    main()
