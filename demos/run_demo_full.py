"""Full registry demo: every collector for every agent/technique implemented
so far, registered together in one CollectorRegistry.

This supersedes run_demo.py (2 collectors) and run_demo_all_five.py (5
Hooks-only collectors) without replacing them -- both still run standalone
and still pass. This file adds the remaining 14 technique collectors built
across Copilot, Antigravity, and Codex (OTel, CLI Wrapper, MCP Proxy, FS
Watcher, and -- where genuinely distinct from Hooks -- Local Hooks).

Claude Code and Cursor remain Hooks-only: no OTel/CLIWrapper/MCPProxy/
FSWatcher/LocalHooks research folders exist for those two agents in this
project, so there is no real evidence to ground additional collectors on.
Building them would mean guessing payload shapes, which violates this
project's "no fabricated shapes" rule. If that research is done later, add
the files the same way the other 14 were added here.

For collectors whose real capture data lives in a JSONL/JSON file, this demo
reuses each module's own loader helper (or replicates its documented
__main__ replay path) so the exact same real data is used here as when the
file is run standalone. Nothing here is synthesized.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import List

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import RawEvent
from core.registry import CollectorRegistry

# --- Already-proven Hooks collectors (built in earlier passes) -------------
from collectors.claude_code.claude_code_hooks import ClaudeCodeHooksCollector
from collectors.cursor.cursor_hooks import CursorHooksCollector
from collectors.copilot.copilot_hooks import CopilotHooksCollector
from collectors.antigravity.antigravity_hooks import AntigravityHooksCollector
from collectors.codex.codex_hooks import CodexHooksCollector

# Reuse run_demo_all_five.py's exact real-data loaders for the 5 Hooks
# collectors rather than re-deriving replay logic here.
import run_demo_all_five as _hooks_demo

# --- New technique collectors (this pass) -----------------------------------
from collectors.copilot.copilot_otel import CopilotOTelCollector
from collectors.copilot.copilot_cli_wrapper import CopilotCLIWrapperCollector
from collectors.copilot.copilot_mcp_proxy import CopilotMCPProxyCollector
from collectors.copilot.copilot_fs_watcher import CopilotFSWatcherCollector
from collectors.copilot.copilot_local_hooks import CopilotLocalHooksCollector

from collectors.antigravity.antigravity_otel import AntigravityOTelCollector
from collectors.antigravity.antigravity_cli_wrapper import AntigravityCLIWrapperCollector
from collectors.antigravity.antigravity_mcp_proxy import AntigravityMCPProxyCollector
from collectors.antigravity.antigravity_fs_watcher import AntigravityFSWatcherCollector
from collectors.antigravity.antigravity_local_hooks import AntigravityLocalHooksCollector

from collectors.codex.codex_otel import CodexOTelCollector, _load_sample_loki_streams
from collectors.codex.codex_cli_wrapper import CodexCLIWrapperCollector, _load_sample_wrapper_chunks
from collectors.codex.codex_mcp_proxy import CodexMCPProxyCollector, _load_sample_mcp_jsonl
from collectors.codex.codex_fs_watcher import CodexFSWatcherCollector, _load_sample_fs_events
from collectors.codex.codex_local_hooks import CodexLocalHooksCollector, _load_sample_ide_events

from collectors.claude_code.claude_code_otel import ClaudeCodeOTelCollector
from collectors.claude_code.claude_code_cli_wrapper import ClaudeCodeCLIWrapperCollector
from collectors.claude_code.claude_code_mcp_proxy import ClaudeCodeMCPProxyCollector
from collectors.claude_code.claude_code_fs_watcher import ClaudeCodeFSWatcherCollector

from collectors.cursor.cursor_otel import CursorOTelCollector
from collectors.cursor.cursor_cli_wrapper import CursorCLIWrapperCollector
from collectors.cursor.cursor_mcp_proxy import CursorMCPProxyCollector
from collectors.cursor.cursor_fs_watcher import CursorFSWatcherCollector
# cursor_local_hooks.py / claude_code_local_hooks.py deliberately do not
# exist -- both agents' "local hooks" surface is the same mechanism as
# their existing Hooks collector (see README "Local Hooks" notes).

PROJECT_ROOT = Path(__file__).resolve().parents[3]  # .../Project_Signal


def _jsonl(path: Path, limit: int = 12) -> List[dict]:
    out: List[dict] = []
    if not path.is_file():
        return out
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            import json as _json
            try:
                out.append(_json.loads(line))
            except Exception:
                continue
            if len(out) >= limit:
                break
    return out


def _first_real_claude_transcript_lines(limit: int = 8) -> List[dict]:
    """Same real-data discovery as claude_code_fs_watcher.py's own __main__
    block: find a real ~/.claude/projects/**/*.jsonl transcript on this
    machine and return its first few lines, unmodified."""
    import json as _json

    projects_root = Path(os.path.expanduser("~/.claude/projects"))
    if not projects_root.is_dir():
        return []
    for jsonl_path in projects_root.rglob("*.jsonl"):
        try:
            with open(jsonl_path, "r", encoding="utf-8") as f:
                lines = [_json.loads(l) for l in f if l.strip()]
        except (OSError, ValueError):
            continue
        if lines:
            return lines[:limit]
    return []


def build_collectors() -> List:
    """Instantiate every collector, feeding real captured-data replay
    arguments where a module exposes a way to do so. Real file paths only
    -- if a file isn't found, that collector legitimately yields 0 events
    (matches each module's own standalone behavior)."""

    collectors = [
        # Hooks (proven, earlier passes) -- fed with the same real captured
        # data as run_demo_all_five.py, via its own loader functions.
        ClaudeCodeHooksCollector(replay_events=_hooks_demo.load_claude_replay_events()),
        CursorHooksCollector(replay_events=_hooks_demo.load_cursor_replay_events()),
        CopilotHooksCollector(replay_events=_hooks_demo.load_copilot_replay_events()),
        AntigravityHooksCollector(replay_events=_hooks_demo.load_antigravity_replay_events()),
        CodexHooksCollector(replay_events=_hooks_demo.load_codex_replay_events(), require_trust=False),

        # Copilot -- new techniques
        CopilotOTelCollector(),
        CopilotCLIWrapperCollector(
            replay_events=_jsonl(PROJECT_ROOT / "cli_wrapper_demo" / "captured_events.jsonl")
        ),
        CopilotMCPProxyCollector(
            replay_records=_jsonl(
                PROJECT_ROOT / "Copilot_MCP_Proxy" / "captured_mcp_events_run1_success.jsonl"
            )
        ),
        CopilotFSWatcherCollector(),
        CopilotLocalHooksCollector(
            replay_events=_jsonl(
                PROJECT_ROOT / "Copilot_LocalHooks" / "captured_ide_events.jsonl", limit=25
            )
        ),

        # Antigravity -- new techniques
        AntigravityOTelCollector(),
        AntigravityCLIWrapperCollector(
            replay_path=PROJECT_ROOT / "Antigravity_CLIWrapper" / "captured_output_streamjson.jsonl"
        ),
        AntigravityMCPProxyCollector(
            replay_path=PROJECT_ROOT / "Antigravity_MCPProxy" / "captured_mcp_events_baseline.jsonl"
        ),
        AntigravityFSWatcherCollector(
            replay_path=PROJECT_ROOT / "Antigravity_FSWatcher" / "captured_fs_events.jsonl"
        ),
        AntigravityLocalHooksCollector(
            replay_path=PROJECT_ROOT / "Antigravity_LocalHooks" / "captured_ide_events.jsonl"
        ),

        # Codex -- new techniques
        CodexOTelCollector(replay_events=_load_sample_loki_streams(max_events=10), require_configured=False),
        CodexCLIWrapperCollector(replay_events=_load_sample_wrapper_chunks(max_events=10)),
        CodexMCPProxyCollector(
            replay_events=_load_sample_mcp_jsonl("captured_mcp_events_baseline.jsonl", max_events=10)
        ),
        CodexFSWatcherCollector(replay_events=_load_sample_fs_events(max_events=10)),
        CodexLocalHooksCollector(replay_events=_load_sample_ide_events(max_events=10)),

        # Claude Code -- new techniques (each collector auto-loads its own
        # real replay data internally when no explicit replay is passed --
        # e.g. claude_code_fs_watcher.py reads this machine's own real
        # ~/.claude/projects/*.jsonl transcript if present).
        ClaudeCodeOTelCollector(),
        # replay_events=[] (not None/default): None means "no replay data
        # supplied" to this collector, which falls through to its live mode
        # and spawns a real `claude` child process via pty -- correct
        # behavior for a live deployment, but wrong for this offline demo.
        # An explicit empty list keeps capture() honestly empty without
        # spawning anything.
        ClaudeCodeCLIWrapperCollector(replay_events=[]),
        # No Claude-Code-specific MCP wire capture exists in this repo, so
        # (same as the module's own __main__ demo) replay Copilot's real
        # capture purely to exercise the agent-agnostic JSON-RPC/MCP wire
        # parsing this collector implements -- still real data, just not
        # Claude Code's own.
        ClaudeCodeMCPProxyCollector(
            replay_records=_jsonl(
                PROJECT_ROOT / "Copilot_MCP_Proxy" / "captured_mcp_events_run1_success.jsonl"
            )
        ),
        # Real transcript lines from this machine's own actual
        # ~/.claude/projects/*.jsonl session data, same discovery logic as
        # the module's own __main__ block.
        ClaudeCodeFSWatcherCollector(
            replay_events=_first_real_claude_transcript_lines()
        ),

        # Cursor -- new techniques
        CursorOTelCollector(),
        CursorCLIWrapperCollector(),
        CursorMCPProxyCollector(),
        CursorFSWatcherCollector(),
    ]
    return collectors


def main() -> None:
    print("=" * 78)
    print("COLLECTOR FRAMEWORK -- FULL REGISTRY DEMO (all agents x all")
    print("proven techniques implemented so far)")
    print("=" * 78)

    registry = CollectorRegistry()
    for c in build_collectors():
        registry.register(c)

    print(f"\nRegistered {len(registry._collectors)} collectors:")
    for cid in registry._collectors:
        print(f"  - {cid}")

    print("\n" + "=" * 78)
    print("STEP 1: detect()")
    print("=" * 78)
    results = registry.run_detection()
    for cid, ok in results.items():
        print(f"  {cid:32s} detect() -> {ok}")

    print("\n" + "=" * 78)
    print("STEP 2: activate() detected collectors")
    print("=" * 78)
    activated = registry.activate_detected(results)
    print(f"  Activated: {activated}")

    print("\n" + "=" * 78)
    print("STEP 3: capture() merged stream (SIMULATED FROM CAPTURED DATA")
    print("unless noted otherwise inside a given collector's own output)")
    print("=" * 78)
    counts = {}
    total = 0
    sample_shown = {}
    for ev in registry.merged_capture():
        total += 1
        counts[ev.collector_id] = counts.get(ev.collector_id, 0) + 1
        if ev.collector_id not in sample_shown:
            sample_shown[ev.collector_id] = ev
            payload_str = str(ev.raw_payload)
            if len(payload_str) > 260:
                payload_str = payload_str[:260] + "...(truncated)"
            print(f"\n--- first RawEvent for {ev.collector_id} ---")
            print(f"  timestamp    : {ev.timestamp}")
            print(f"  agent        : {ev.agent}")
            print(f"  raw_payload  : {payload_str}")

    print("\n" + "=" * 78)
    print("STEP 4: summary")
    print("=" * 78)
    for cid in sorted(counts):
        print(f"  {cid:32s} -> {counts[cid]} RawEvent(s)")
    print(f"\n  TOTAL: {total} RawEvents across {len(counts)} active collectors")

    if registry.failures:
        print(f"\n  Collector failures (isolated, did not crash the run): {len(registry.failures)}")
        for fail in registry.failures:
            print(f"    {fail}")
    else:
        print("\n  No collector failures recorded.")

    print("\n" + "=" * 78)
    print("STEP 5: deactivate()")
    print("=" * 78)
    registry.deactivate_all()
    print("  All active collectors deactivated cleanly.")

    print("\nDONE")


if __name__ == "__main__":
    main()
