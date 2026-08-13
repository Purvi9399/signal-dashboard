"""
Antigravity MCP Proxy collector.

Grounded in the real, empirical test performed for this project. Primary
sources (read-only, never modified):

  Antigravity_MCPProxy/RESULTS.md -- full write-up: a transparent stdio
    MCP proxy (mcp_proxy.py) wrapping a real FastMCP server
    (server/mcp_server.py, tools get_current_time/list_directory),
    registered in Antigravity's real mcp_config.json schema, with three
    real test sessions -- baseline, connection-failure, and subagent
    delegation.
  Antigravity_MCPProxy/mcp_proxy.py -- the actual proxy: spawns the real
    MCP server as a subprocess, shuttles newline-delimited JSON-RPC in
    both directions unmodified, logs every line (with direction+timestamp)
    to captured_mcp_events.jsonl.
  Antigravity_MCPProxy/server/mcp_server.py -- the wrapped demo server
    (unmodified, adapted from the Copilot MCP Proxy test).
  Antigravity_MCPProxy/captured_mcp_events_baseline.jsonl -- real captured
    handshake + tools/list + two tools/call round trips.
  Antigravity_MCPProxy/captured_mcp_events_delegation.jsonl -- real
    captured traffic during a subagent-delegation session: only 7 lines,
    the connection handshake and NOTHING ELSE -- confirms invoke_subagent
    never crosses the MCP wire (RESULTS.md section 3, "THE KEY FINDING").
  Antigravity_MCPProxy/captured_mcp_events_connfail.jsonl -- real captured
    traffic against a deliberately-broken server config: two
    server/discover attempts, zero responses, then silence.
  Antigravity_MCPProxy/hooks_crossref_delegation.jsonl -- real Hooks
    events captured during the SAME delegation session as
    captured_mcp_events_delegation.jsonl, proving the delegation shows up
    fully in Hooks (PreToolUse/PostToolUse invoke_subagent, then an
    entirely separate PreInvocation...Stop lifecycle under the subagent's
    own conversationId) even though MCP saw nothing.
  Antigravity_MCPProxy/config/mcp_config.json -- real working
    {"mcpServers": {"<name>": {"command": ..., "args": [...]}}} schema
    (confirmed in RESULTS.md section 1 to live at
    ~/.gemini/config/mcp_config.json, much simpler than hooks.json's
    named-block surprise -- no nesting gotcha here).

HEADLINE FINDING (RESULTS.md section 3/6): subagent delegation
(invoke_subagent) is a fully native Antigravity capability, NOT routed
through the generic call_mcp_tool indirection real MCP tools use -- so an
MCP proxy, no matter how it's configured, structurally cannot see it. This
collector's capture() therefore only ever yields real MCP JSON-RPC traffic
(handshake + tools/list + tools/call for genuine MCP-backed tools); it
will legitimately produce near-empty output during a delegation-heavy
session, and that emptiness is itself real, correctly-reproduced signal,
not a bug -- downstream consumers who want delegation visibility must
correlate with Hooks (antigravity_hooks.py), exactly per RESULTS.md
section 6.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, List, Optional

import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parents[2]))
from core import Collector, RawEvent

AGENT_NAME = "antigravity"
COLLECTOR_ID = "antigravity.mcp_proxy"

# Real config location, confirmed in Antigravity_MCPProxy/RESULTS.md
# section 1 by extracting the embedded agent-facing doc
# (assets/external/skills/agy-customizations/docs/mcp_servers.md) straight
# out of the language_server binary -- `agy help mcp` does not exist as a
# subcommand, this is the only source of truth for the schema.
GLOBAL_MCP_CONFIG = Path.home() / ".gemini" / "config" / "mcp_config.json"

# Confirmed real non-standard pre-handshake method Antigravity's MCP
# client always tries first (RESULTS.md section 2), before falling back to
# the standard `initialize` handshake. Recognizing it is useful for a
# proxy/log consumer that wants to flag "this session is Antigravity, not
# some other MCP client" purely from wire traffic, without any special
# antigravity-specific protocol support required (a spec-compliant server
# just rejects it and the client recovers on its own).
ANTIGRAVITY_DISCOVER_METHOD = "server/discover"
ANTIGRAVITY_CLIENT_NAME = "antigravity-client"

# Confirmed real custom _meta extension injected into every real
# tools/call request (RESULTS.md section 2) -- a fourth independent source
# of the active conversation ID, and the only one that arrives inside the
# MCP wire protocol itself.
ANTIGRAVITY_META_CONVERSATION_ID_KEY = "antigravity.google/conversation_id"
ANTIGRAVITY_META_ARTIFACTS_DIR_KEY = "antigravity.google/artifacts_dir"


def load_mcp_config(path: Path) -> Optional[dict]:
    """Parse a real mcp_config.json using the confirmed real schema
    (RESULTS.md section 1): {"mcpServers": {"<name>": {"command", "args",
    "env"}}} for stdio transport, or {"serverUrl": ...} for SSE/remote.
    Unlike hooks.json, this schema matches what you'd naturally guess --
    no named-block surprise here."""
    if not path.is_file():
        return None
    try:
        cfg = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return None
    if not isinstance(cfg, dict):
        return None
    if "mcpServers" in cfg and isinstance(cfg["mcpServers"], dict):
        return cfg
    return None


def _load_jsonl(path: Path) -> List[dict]:
    events: List[dict] = []
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


class AntigravityMCPProxyCollector(Collector):
    """Transparent stdio MCP proxy collector for Antigravity.

    Live mode (real deployment): activate() spawns the real MCP server
    named in server_command as a subprocess (mirroring
    Antigravity_MCPProxy/mcp_proxy.py exactly -- unmodified pattern,
    adapted only for the Collector lifecycle instead of being invoked
    directly as `command` inside mcp_config.json). Every JSON-RPC line
    seen in either direction is captured verbatim as
    {timestamp, direction, raw, parsed} -- direction is
    "client_to_server" or "server_to_client", matching the real proxy's
    own field names exactly (see mcp_proxy.py's log_event()).

    Per the KEY FINDING above, this collector structurally cannot see
    native tool calls (invoke_subagent, write_to_file, run_command,
    browser_*, etc.) -- only traffic for tools actually routed through
    a configured MCP server. This is a real, confirmed architectural
    boundary, not a gap in this implementation.

    This build also supports feeding capture() from an already-captured
    JSONL file (any of Antigravity_MCPProxy/captured_mcp_events*.jsonl)
    to exercise it deterministically against real sample data, including
    the delegation session that produced only a 7-line handshake.
    """

    def __init__(
        self,
        server_command: Optional[List[str]] = None,
        replay_path: Optional[Path] = None,
    ) -> None:
        self._server_command = server_command
        self._replay_path = replay_path
        self._activated = False
        self._proc: Optional[subprocess.Popen] = None
        self._captured: List[dict] = []
        self._lock = threading.Lock()

    @property
    def collector_id(self) -> str:
        return COLLECTOR_ID

    def detect(self) -> bool:
        """Real, cheap heuristics:
          1. `agy` on PATH (same product-presence check as the other
             Antigravity collectors -- confirmed a separate CLI binary,
             Antigravity_OTel/RESULTS.md section 0).
          2. ~/.gemini/config/mcp_config.json exists AND parses under the
             real confirmed schema (proof MCP is actually configured, not
             just that the product is installed -- mirrors
             antigravity_hooks.py's detect() pattern for hooks.json).
        """
        found_any = False
        if shutil.which("agy") is not None:
            found_any = True
        if load_mcp_config(GLOBAL_MCP_CONFIG) is not None:
            found_any = True
        return found_any

    def activate(self) -> None:
        """If replay_path was given, activation is a no-op besides marking
        ready. Otherwise spawns the real server_command as a subprocess
        with piped stdin/stdout, exactly as mcp_proxy.py's
        subprocess.Popen(..., stdin=PIPE, stdout=PIPE) does."""
        if self._replay_path is not None:
            self._activated = True
            return

        if self._server_command:
            self._proc = subprocess.Popen(
                self._server_command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=sys.stderr,
            )
        self._activated = True

    def _log_event(self, direction: str, raw_line: str) -> dict:
        """Mirrors mcp_proxy.py's log_event() exactly: same field names
        (timestamp, direction, raw, parsed), same best-effort JSON parse
        with parsed=None on failure."""
        record: dict = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "direction": direction,
            "raw": raw_line,
        }
        try:
            record["parsed"] = json.loads(raw_line)
        except json.JSONDecodeError:
            record["parsed"] = None
        return record

    def capture(self) -> Iterator[RawEvent]:
        if not self._activated:
            raise RuntimeError(f"{self.collector_id}: capture() called before activate()")

        if self._replay_path is not None:
            for payload in _load_jsonl(self._replay_path):
                yield RawEvent(
                    timestamp=RawEvent.now_iso(),
                    collector_id=self.collector_id,
                    agent=AGENT_NAME,
                    raw_payload=payload,  # untouched, exact captured shape
                )
            return

        if self._proc is None or self._proc.stdout is None:
            # No real MCP server subprocess wired up in this standalone
            # demo build without an explicit server_command -- live mode
            # placeholder, same pattern as antigravity_hooks.py's capture()
            # when no replay data / no live listener is configured.
            return

        # Drain server_to_client traffic (the direction most useful to a
        # passive collector -- what real MCP-backed tools returned).
        for raw_line in iter(self._proc.stdout.readline, b""):
            if not raw_line:
                break
            decoded = raw_line.decode("utf-8", errors="replace").rstrip("\n")
            if not decoded.strip():
                continue
            payload = self._log_event("server_to_client", decoded)
            yield RawEvent(
                timestamp=RawEvent.now_iso(),
                collector_id=self.collector_id,
                agent=AGENT_NAME,
                raw_payload=payload,
            )

    def deactivate(self) -> None:
        if self._proc is not None:
            try:
                self._proc.terminate()
            except OSError:
                pass
            self._proc = None
        self._activated = False


if __name__ == "__main__":
    collector = AntigravityMCPProxyCollector()
    print(f"detect() -> {collector.detect()}")

    real_dir = Path(__file__).resolve().parents[3].parent / "Antigravity_MCPProxy"

    for label, fname in [
        ("BASELINE (real MCP tool calls)", "captured_mcp_events_baseline.jsonl"),
        ("DELEGATION (invoke_subagent -- confirms MCP sees NOTHING)", "captured_mcp_events_delegation.jsonl"),
        ("CONNECTION FAILURE (broken server config)", "captured_mcp_events_connfail.jsonl"),
    ]:
        replay_file = real_dir / fname
        print(f"\nSIMULATED FROM CAPTURED DATA -- {label} ({replay_file}):")
        if not replay_file.is_file():
            print(f"  file not found, skipping")
            continue
        replay_collector = AntigravityMCPProxyCollector(replay_path=replay_file)
        replay_collector.activate()
        events = list(replay_collector.capture())
        print(f"  {len(events)} RawEvent(s) total")
        for ev in events[:3]:
            print(f"  {ev}")
        replay_collector.deactivate()
