"""
Codex MCP Proxy collector.

Grounded in the real, empirical capture performed for this project -- the
richest of the four Codex evidence folders used so far, including a
cross-referenced correlation with Hooks. Primary sources (read-only, never
modified):

  Codex_MCPProxy/RESULTS.md                   -- the full write-up this file is built from
  Codex_MCPProxy/mcp_proxy.py                 -- real transparent stdio proxy, exact JSONL
                                                  record shape this collector's replay mode
                                                  consumes ({timestamp, direction, raw, parsed})
  Codex_MCPProxy/server/mcp_server.py          -- the real FastMCP demo server proxied
  Codex_MCPProxy/captured_mcp_events_baseline.jsonl    -- tool discovery + 2 real tool calls
  Codex_MCPProxy/captured_mcp_events_connfail.jsonl    -- 2 unanswered initialize attempts
  Codex_MCPProxy/captured_mcp_events_delegation.jsonl  -- subagent delegation MCP traffic
  Codex_MCPProxy/hooks_crossref_delegation.jsonl       -- simultaneous Hooks capture used to
                                                          correlate subagent MCP calls to
                                                          agent_id (see correlate_with_hooks())
  Codex_MCPProxy/hooks_crossref_forward.py,
  Codex_MCPProxy/hooks_crossref_capture_server.py,
  Codex_MCPProxy/hooks_crossref/hooks.json     -- the scoped Hooks setup used only for that
                                                  cross-reference capture

This collector performs NO normalization: each captured JSON-RPC line keeps
exactly the {timestamp, direction, raw, parsed} shape mcp_proxy.py's own
log_event() writes (raw string plus best-effort parsed dict, `parsed: null`
on decode failure -- preserved as-is, never silently coerced).
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Iterator, List, Optional

import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parents[2]))
from collectors.live_http import LiveEventFeed  # noqa: E402
from collectors.live_mcp import LiveStdioProxy  # noqa: E402
from core import Collector, RawEvent

AGENT_NAME = "codex"
COLLECTOR_ID = "codex.mcp_proxy"

# UNDOCUMENTED, SECURITY-RELEVANT FINDING (Codex_MCPProxy/RESULTS.md section
# 4): every single tools/call request Codex sends carries this block,
# unconditionally, to every configured MCP server -- including third-party
# ones with no trust relationship to OpenAI/Codex's own telemetry pipeline.
# Confirmed real key names from a captured tools/call params._meta:
#   x-codex-turn-metadata: {session_id, thread_id, turn_id, thread_source,
#                            sandbox, turn_started_at_unix_ms, model,
#                            reasoning_effort}
# This collector does not strip/redact this -- raw_payload stays exactly
# what was captured on the wire.
CODEX_META_KEY = "x-codex-turn-metadata"

# CONFIRMED (Codex_MCPProxy/RESULTS.md section 3): unlike Antigravity (where
# subagent delegation is purely native and invisible to an MCP proxy), Codex
# subagents' MCP tool calls ARE fully visible on the proxied stdio channel.
# The only way to attribute a given tools/call to a specific subagent from
# the MCP stream ALONE is by tight temporal proximity to the Hooks stream's
# PreToolUse/PostToolUse events for the matching tool name
# (mcp__<server>__<tool>) tagged with that subagent's agent_id -- there is
# no shared session/subagent identifier embedded in the raw MCP JSON-RPC
# messages themselves that would let you do this from MCP traffic alone.


def find_codex_home() -> Path:
    return Path.home() / ".codex"


class CodexMCPProxyCollector(Collector):
    """Transparent stdio MCP proxy collector for the Codex CLI (OpenAI),
    acting as an MCP *client* (Codex calling out to external MCP servers
    it's configured to use -- the opposite direction from `codex
    mcp-server` mode, which is covered by codex_otel.py's captures instead,
    per Codex_MCPProxy/RESULTS.md section 0 item 5).

    Live mode (real deployment): activate() would register a proxied server
    entry via `codex mcp add <name> -- python3 mcp_proxy.py -- <real-server
    command>` (confirmed to work exactly as documented, Codex_MCPProxy/
    RESULTS.md section 1 -- the first Codex config mechanism in this whole
    series that matched documentation on the first try), which writes a
    plain `[mcp_servers.<name>]` table to config.toml. mcp_proxy.py itself
    spawns the real server as a child process and shuttles newline-delimited
    JSON-RPC both directions, appending every message to a JSONL log.
    capture() would then tail that log.

    This build supports feeding capture() from already-captured JSONL
    records (Codex_MCPProxy/captured_mcp_events_baseline.jsonl and friends)
    to exercise it deterministically against real sample data.
    """

    def __init__(
        self,
        replay_events: Optional[List[dict]] = None,
        server_command: Optional[List[str]] = None,
        client_in=None,
        client_out=None,
    ) -> None:
        self._replay_events = replay_events
        self._server_command = server_command
        self._client_in = client_in
        self._client_out = client_out
        self._activated = False
        self._live_feed: Optional[LiveEventFeed] = None
        self._proxy: Optional[LiveStdioProxy] = None

    @property
    def collector_id(self) -> str:
        return COLLECTOR_ID

    def detect(self) -> bool:
        """Real, cheap heuristics for "is Codex CLI present and configured
        to use at least one MCP server on this machine", per
        Codex_MCPProxy/RESULTS.md:
          1. `codex` binary on PATH (consistent with the other Codex
             collectors).
          2. ~/.codex directory exists (CODEX_HOME).
          3. ~/.codex/config.toml contains an `[mcp_servers.` table header
             (proof at least one MCP server -- proxied or direct -- is
             actually configured, mirroring codex_hooks.py's hooks.json
             check and codex_otel.py's `[otel]` check). `codex mcp list
             --json` is the documented, authoritative way to enumerate
             these live (confirmed working in RESULTS.md section 1), but a
             cheap file scan is sufficient and side-effect-free for
             detect(), consistent with detect()'s "cheap, no subprocess
             calls" contract in core.py.
        """
        found_any = False

        if shutil.which("codex") is not None:
            found_any = True

        codex_home = find_codex_home()
        if codex_home.is_dir():
            found_any = True

        config_path = codex_home / "config.toml"
        if config_path.is_file():
            try:
                text = config_path.read_text()
                if "[mcp_servers." in text:
                    found_any = True
            except OSError:
                pass

        return found_any

    def activate(self) -> None:
        """No precondition-checking trap here (unlike codex_hooks.py's
        trust gate) -- MCP server config has no equivalent silent-failure
        gate; a missing/broken server surfaces proactively in-session
        (Codex_MCPProxy/RESULTS.md section 5, "proactive diagnosis"
        matching Antigravity's pattern, not Copilot's silent-hallucination
        pattern), so there's nothing here worth raising loudly about ahead
        of time.
        """
        self._activated = True
        if self._replay_events is not None:
            return
        self._start_live()

    def _start_live(self) -> None:
        feed = LiveEventFeed()
        self._live_feed = feed
        if self._server_command is None:
            return
        proxy = LiveStdioProxy(
            self._server_command, feed, self._client_in, self._client_out
        )
        proxy.start()
        self._proxy = proxy

    def capture(self) -> Iterator[RawEvent]:
        if not self._activated:
            raise RuntimeError(f"{self.collector_id}: capture() called before activate()")
        source = self._event_source()
        if source is None:
            return
        for payload in source:
            yield RawEvent(
                timestamp=RawEvent.now_iso(),
                collector_id=self.collector_id,
                agent=AGENT_NAME,
                raw_payload=payload,
            )

    def _event_source(self):
        if self._replay_events is not None:
            return self._replay_events
        return self._live_feed

    def deactivate(self) -> None:
        self._activated = False
        proxy = self._proxy
        self._proxy = None
        if proxy is not None:
            proxy.stop()
            return
        feed = self._live_feed
        if feed is not None:
            feed.stop()


def correlate_with_hooks(
    mcp_events: List[dict],
    hooks_events: List[dict],
    tolerance_seconds: float = 2.0,
) -> List[dict]:
    """Best-effort correlation between raw MCP proxy traffic and a
    simultaneously-captured Hooks stream, porting the real cross-reference
    methodology from Codex_MCPProxy/RESULTS.md section 3 (NOT a live,
    production-grade join -- a direct port of the same "line up by tool name
    + tight time window" technique the real investigation used manually).

    This is intentionally a SEPARATE, explicit, opt-in utility function --
    NOT wired into capture() -- because core.py's RawEvent.raw_payload must
    stay completely unmodified/unnormalized (hard design constraint); this
    function returns a list of correlation-hint dicts alongside the raw
    events, never mutates or enriches raw_payload itself.

    Real basis: Codex_MCPProxy/RESULTS.md section 3 shows the subagent's two
    tool calls (mcp__proxied_demo__get_current_time,
    mcp__proxied_demo__list_directory) in the Hooks stream correspond 1:1,
    within a few hundred ms, with the two tools/call messages the MCP proxy
    saw on the wire -- both tagged to the same subagent's agent_id in Hooks'
    PreToolUse/PostToolUse payloads. There is no shared ID in the raw MCP
    JSON-RPC messages themselves (see module docstring) -- correlation is
    necessarily by (tool short-name substring match) + (timestamp
    proximity), same as the real investigation's own method, with the same
    limitation: this is heuristic, not a guaranteed-correct join.

    Args:
        mcp_events: parsed records shaped like captured_mcp_events_delegation.jsonl
        hooks_events: parsed records shaped like hooks_crossref_delegation.jsonl
    Returns:
        list of {"mcp_event": ..., "hook_event": ..., "delta_seconds": ...}
        for each MCP tools/call request matched to a Hooks PreToolUse event
        within tolerance_seconds and a tool-name substring match.
    """
    from datetime import datetime

    def _parse_ts(raw_ts: str):
        try:
            return datetime.fromisoformat(raw_ts.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            return None

    correlations: List[dict] = []

    mcp_calls = []
    for ev in mcp_events:
        parsed = ev.get("parsed") or {}
        if parsed.get("method") == "tools/call":
            tool_name = (parsed.get("params") or {}).get("name")
            ts = _parse_ts(ev.get("timestamp"))
            if tool_name and ts is not None:
                mcp_calls.append((ts, tool_name, ev))

    for ev in hooks_events:
        payload = (ev.get("payload") or {}).get("payload") or {}
        if payload.get("hook_event_name") != "PreToolUse":
            continue
        hook_tool_name = payload.get("tool_name") or ""
        ts = _parse_ts(ev.get("received_at"))
        if ts is None:
            continue
        for mcp_ts, mcp_tool_name, mcp_ev in mcp_calls:
            # Real hook tool_name for MCP-routed calls is "mcp__<server>__<tool>"
            # (Codex_MCPProxy/RESULTS.md section 3) -- substring match against
            # the bare MCP tool name is the same technique the original
            # investigation used by eye.
            if mcp_tool_name in hook_tool_name:
                delta = abs((mcp_ts - ts).total_seconds())
                if delta <= tolerance_seconds:
                    correlations.append({
                        "mcp_event": mcp_ev,
                        "hook_event": ev,
                        "delta_seconds": delta,
                    })

    return correlations


def _load_sample_mcp_jsonl(filename: str, max_events: Optional[int] = None) -> List[dict]:
    """Best-effort loader for real captured MCP proxy JSONL records."""
    candidates = [
        Path(__file__).resolve().parents[4] / "Codex_MCPProxy" / filename,
        Path("/Users/malharshinde/Desktop/SOAISEC/Project_Signal/Codex_MCPProxy") / filename,
    ]
    for path in candidates:
        if path.is_file():
            events = []
            try:
                with path.open() as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        events.append(json.loads(line))
                        if max_events is not None and len(events) >= max_events:
                            break
            except (json.JSONDecodeError, OSError):
                continue
            return events
    return []


if __name__ == "__main__":
    collector = CodexMCPProxyCollector()

    print(f"[{collector.collector_id}] detect() -> {collector.detect()}")

    sample_events = _load_sample_mcp_jsonl("captured_mcp_events_baseline.jsonl", max_events=5)
    if sample_events:
        print(f"[{collector.collector_id}] loaded {len(sample_events)} real captured MCP "
              f"JSON-RPC records from Codex_MCPProxy/captured_mcp_events_baseline.jsonl")
        collector = CodexMCPProxyCollector(replay_events=sample_events)
    else:
        print(f"[{collector.collector_id}] captured_mcp_events_baseline.jsonl not reachable "
              f"from this checkout -- capture() will yield nothing (no synthetic fallback).")

    collector.activate()
    print(f"[{collector.collector_id}] activate() ok")

    count = 0
    for event in collector.capture():
        count += 1
        label = "SIMULATED FROM CAPTURED DATA"
        print(f"\n--- RawEvent #{count} ({label}) ---")
        print(f"timestamp:    {event.timestamp}")
        print(f"collector_id: {event.collector_id}")
        print(f"agent:        {event.agent}")
        print(f"direction:    {event.raw_payload.get('direction')}")
        parsed = event.raw_payload.get("parsed") or {}
        print(f"method/result/error: "
              f"{parsed.get('method') or ('result' if 'result' in parsed else parsed.get('error'))}")
        if count >= 5:
            break

    collector.deactivate()
    print(f"\n[{collector.collector_id}] deactivate() ok. Total events yielded: {count}")

    # Real correlation demo, using the actual delegation cross-reference
    # capture files (Codex_MCPProxy/RESULTS.md section 3).
    print("\n--- correlate_with_hooks() demo (SIMULATED FROM CAPTURED DATA) ---")
    mcp_delegation = _load_sample_mcp_jsonl("captured_mcp_events_delegation.jsonl")
    hooks_candidates = [
        Path(__file__).resolve().parents[4] / "Codex_MCPProxy" / "hooks_crossref_delegation.jsonl",
        Path("/Users/malharshinde/Desktop/SOAISEC/Project_Signal/Codex_MCPProxy/hooks_crossref_delegation.jsonl"),
    ]
    hooks_delegation: List[dict] = []
    for hp in hooks_candidates:
        if hp.is_file():
            with hp.open() as f:
                hooks_delegation = [json.loads(l) for l in f if l.strip()]
            break

    if mcp_delegation and hooks_delegation:
        results = correlate_with_hooks(mcp_delegation, hooks_delegation)
        print(f"Correlated {len(results)} MCP tools/call <-> Hooks PreToolUse pairs "
              f"(tolerance=2.0s):")
        for r in results:
            tool = (r["mcp_event"].get("parsed") or {}).get("params", {}).get("name")
            print(f"  tool={tool!r} delta_seconds={r['delta_seconds']:.3f}")
    else:
        print("delegation cross-reference files not reachable from this checkout -- skipped.")
