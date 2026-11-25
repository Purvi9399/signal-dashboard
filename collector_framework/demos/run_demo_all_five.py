#!/usr/bin/env python3
"""
Real, runnable demo of the Collector Framework -- all FIVE collectors:
Claude Code, Cursor, Copilot, Antigravity, Codex.

Same pattern as run_demo.py:
- Runs detect() for real on this machine (no mocking). Most of the new
  three will be False on a machine that isn't actually running that
  agent's CLI/IDE -- that is the CORRECT, expected result, not a bug.
- activate()s whatever collectors actually detect() True.
- For capture(): live hook traffic isn't available in this offline demo,
  so each collector is fed from real captured JSONL/log data checked into
  the repo's per-agent research folders, clearly labeled SIMULATED FROM
  CAPTURED DATA.
- Prints real RawEvent objects, including raw_payload, to show each
  agent's differently-shaped payloads passing through completely
  unmodified -- including Antigravity's PascalCase toolCall.args inside a
  camelCase envelope, Codex's inconsistent field presence across event
  types, and Copilot's three-payload-shape situation.

Data sources used (read-only, never modified):
  projectsetup/backend/events/*.jsonl     -> Claude Code hook payloads
  projectsetup/events/*.jsonl             -> Cursor hook payloads
  Copilot/captured_events.jsonl           -> Copilot hook payloads (all 3 shapes)
  Antigravity/captured_events.jsonl       -> Antigravity hook payloads
  Codex/captured_events.jsonl             -> Codex hook payloads
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
from collectors.copilot.copilot_hooks import CopilotHooksCollector
from collectors.antigravity.antigravity_hooks import AntigravityHooksCollector
from collectors.codex.codex_hooks import CodexHooksCollector

REPO_ROOT = Path(__file__).resolve().parents[3]
CLAUDE_EVENTS_DIR = REPO_ROOT / "projectsetup" / "backend" / "events"
MIXED_EVENTS_DIR = REPO_ROOT / "projectsetup" / "events"
COPILOT_EVENTS_FILE = REPO_ROOT / "Copilot" / "captured_events.jsonl"
ANTIGRAVITY_EVENTS_FILE = REPO_ROOT / "Antigravity" / "captured_events.jsonl"
CODEX_EVENTS_FILE = REPO_ROOT / "Codex" / "captured_events.jsonl"


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
    events: list[dict] = []
    for f in sorted(CLAUDE_EVENTS_DIR.glob("*.jsonl")):
        events.extend(load_jsonl(f))
    for f in sorted(MIXED_EVENTS_DIR.glob("*.jsonl")):
        for e in load_jsonl(f):
            if "hook_event_name" in e:
                events.append(e)
    return events


def load_cursor_replay_events() -> list[dict]:
    events: list[dict] = []
    for f in sorted(MIXED_EVENTS_DIR.glob("*.jsonl")):
        for e in load_jsonl(f):
            if "event_type" in e:
                events.append(e)
    return events


def load_copilot_replay_events() -> list[dict]:
    """Copilot/captured_events.jsonl lines are capture-server envelopes
    with the real raw payload under "payload" -- extract that so
    raw_payload holds the exact shape Copilot itself sent (Shape A, B, or C
    per copilot_hooks.py), same convention as the other collectors."""
    events: list[dict] = []
    for e in load_jsonl(COPILOT_EVENTS_FILE):
        payload = e.get("payload", e)
        events.append(payload)
    return events


def load_antigravity_replay_events() -> list[dict]:
    """Antigravity/captured_events.jsonl lines are capture-server envelopes:
    {"received_at":..., "headers":..., "raw_body": "<json string>",
    "parsed_body": {"event_type":..., "cwd":..., "raw_stdin": "<json
    string>", "payload": {...real hook stdin JSON...}}}. The actual hook
    payload Antigravity sent on stdin (with envelope fields like
    conversationId/stepIdx/toolCall) is under parsed_body.payload -- unwrap
    to that, same convention as the Codex/Copilot loaders below."""
    events: list[dict] = []
    for e in load_jsonl(ANTIGRAVITY_EVENTS_FILE):
        parsed_body = e.get("parsed_body", e)
        payload = parsed_body.get("payload", parsed_body) if isinstance(parsed_body, dict) else parsed_body
        events.append(payload)
    return events


def load_codex_replay_events() -> list[dict]:
    """Codex/captured_events.jsonl lines are capture-server envelopes:
    {"received_at":..., "headers":..., "payload": {"event_type":...,
    "payload": {...real hook JSON...}}} -- unwrap to the innermost payload,
    which is the exact JSON Codex posted for that hook event."""
    events: list[dict] = []
    for e in load_jsonl(CODEX_EVENTS_FILE):
        outer_payload = e.get("payload", e)
        inner_payload = outer_payload.get("payload", outer_payload) if isinstance(outer_payload, dict) else outer_payload
        events.append(inner_payload)
    return events


def banner(text: str) -> None:
    print()
    print("=" * 78)
    print(text)
    print("=" * 78)


def main() -> None:
    banner("COLLECTOR FRAMEWORK — REAL DEMO RUN (ALL FIVE COLLECTORS)")

    claude_replay = load_claude_replay_events()
    cursor_replay = load_cursor_replay_events()
    copilot_replay = load_copilot_replay_events()
    antigravity_replay = load_antigravity_replay_events()
    codex_replay = load_codex_replay_events()

    print(f"Loaded {len(claude_replay):3d} real captured Claude Code events from "
          f"{CLAUDE_EVENTS_DIR.relative_to(REPO_ROOT)} (+ mixed dir)")
    print(f"Loaded {len(cursor_replay):3d} real captured Cursor events from "
          f"{MIXED_EVENTS_DIR.relative_to(REPO_ROOT)}")
    print(f"Loaded {len(copilot_replay):3d} real captured Copilot events from "
          f"{COPILOT_EVENTS_FILE.relative_to(REPO_ROOT)}")
    print(f"Loaded {len(antigravity_replay):3d} real captured Antigravity events from "
          f"{ANTIGRAVITY_EVENTS_FILE.relative_to(REPO_ROOT)}")
    print(f"Loaded {len(codex_replay):3d} real captured Codex events from "
          f"{CODEX_EVENTS_FILE.relative_to(REPO_ROOT)}")

    claude_collector = ClaudeCodeHooksCollector(replay_events=claude_replay)
    cursor_collector = CursorHooksCollector(replay_events=cursor_replay)
    copilot_collector = CopilotHooksCollector(replay_events=copilot_replay)
    antigravity_collector = AntigravityHooksCollector(replay_events=antigravity_replay)
    # require_trust=False: this offline demo has no ~/.codex/config.toml
    # with a persisted hooks.state.*.trusted_hash entry (trust was never
    # persisted in the real test either -- the bypass flag was used
    # instead, see Codex/RESULTS.md section 1), so we explicitly
    # acknowledge that instead of tripping CodexTrustNotGrantedError.
    codex_collector = CodexHooksCollector(replay_events=codex_replay, require_trust=False)

    registry = CollectorRegistry()
    registry.register(claude_collector)
    registry.register(cursor_collector)
    registry.register(copilot_collector)
    registry.register(antigravity_collector)
    registry.register(codex_collector)

    banner("STEP 1: detect() — real, on this machine, right now")
    detection = registry.run_detection()
    for cid, detected in detection.items():
        print(f"  {cid:24s} detect() -> {detected}")
    print(
        "\n  NOTE: detect()==False for an agent just means that agent's "
        "CLI/IDE/config isn't\n  present on THIS machine right now -- "
        "expected and correct, not a framework bug."
    )

    banner("STEP 2: activate() — only collectors that were detected")
    activated = registry.activate_detected(detection)
    print(f"  Activated: {activated}")
    print(
        "\n  NOTE: this demo also explicitly force-activates every collector below\n"
        "  (bypassing the detect() gate) purely so its replay data can be shown —\n"
        "  a real deployment would only ever activate() a detected collector."
    )
    all_collectors = {
        "claude_code.hooks": claude_collector,
        "cursor.hooks": cursor_collector,
        "copilot.hooks": copilot_collector,
        "antigravity.hooks": antigravity_collector,
        "codex.hooks": codex_collector,
    }
    for cid, collector in all_collectors.items():
        if cid not in activated:
            try:
                collector.activate()
                activated.append(cid)
                print(f"  Force-activated for demo purposes: {cid}")
            except Exception as exc:  # noqa: BLE001
                print(f"  Could not force-activate {cid}: {type(exc).__name__}: {exc}")
    registry._active_ids = activated  # noqa: SLF001 - demo-only convenience

    banner("STEP 3: capture() — SIMULATED FROM CAPTURED DATA (see note below)")
    print("  NOTE: no live agent session is currently emitting hooks against this")
    print("  demo process, so capture() below is fed from real captured JSONL files")
    print("  on disk (per-agent research folders) rather than a live listener.")
    print("  Every collector below is SIMULATED FROM CAPTURED DATA in this run.")

    replay_counts = {
        "claude_code.hooks": len(claude_replay),
        "cursor.hooks": len(cursor_replay),
        "copilot.hooks": len(copilot_replay),
        "antigravity.hooks": len(antigravity_replay),
        "codex.hooks": len(codex_replay),
    }
    mode_labels = {
        cid: ("SIMULATED FROM CAPTURED DATA" if replay_counts.get(cid) else "LIVE (no data)")
        for cid in all_collectors
    }
    for cid in activated:
        print(f"  {cid:24s} mode -> {mode_labels.get(cid, 'LIVE')}")

    banner("STEP 4: merged capture() stream — real RawEvent objects")
    print("  Printing one representative RawEvent per agent to prove distinct raw")
    print("  shapes pass through unmodified, then a broader sample.")
    count_by_agent: dict[str, int] = {}
    printed = 0
    MAX_PRINT = 20
    seen_agent_once: set[str] = set()

    all_events = list(registry.merged_capture())
    # First pass: guarantee at least one printed example per agent so the
    # distinct-shape proof doesn't depend on interleaving order.
    priority_idx: list[int] = []
    for i, e in enumerate(all_events):
        if e.agent not in seen_agent_once:
            seen_agent_once.add(e.agent)
            priority_idx.append(i)
    priority_idx_set = set(priority_idx)
    ordered = [all_events[i] for i in priority_idx] + [
        e for i, e in enumerate(all_events) if i not in priority_idx_set
    ]

    for event in ordered:
        count_by_agent[event.agent] = count_by_agent.get(event.agent, 0) + 1
        if printed < MAX_PRINT:
            print(f"\n--- RawEvent #{printed + 1} "
                  f"[{mode_labels.get(event.collector_id, 'LIVE')}] ---")
            print(f"  timestamp     : {event.timestamp}")
            print(f"  collector_id  : {event.collector_id}")
            print(f"  agent         : {event.agent}")
            print(f"  raw_payload   : {json.dumps(event.raw_payload)[:600]}")
            printed += 1

    if len(ordered) > MAX_PRINT:
        print(f"\n  ... ({len(ordered) - MAX_PRINT} more RawEvents omitted from printout, all captured) ...")

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
    for collector in all_collectors.values():
        collector.deactivate()
    print("  All active collectors deactivated cleanly.")

    banner("DONE")


if __name__ == "__main__":
    main()
