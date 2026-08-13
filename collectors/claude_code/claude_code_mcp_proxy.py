"""
Claude Code MCP Proxy collector.

PROVENANCE NOTE: there is no `Claude_Code_MCPProxy/` (or spelling variant)
folder in this project -- checked alongside the three that DO exist
(Copilot_MCP_Proxy/, Antigravity_MCPProxy/, Codex_MCPProxy/), none of which
are for Claude Code. No captured JSON-RPC wire log for `claude` exists
anywhere in this repo. Per the task brief, this collector's mechanism is
ported from the clearest of the three existing sibling implementations --
Copilot_MCP_Proxy/mcp_proxy.py, via copilot_mcp_proxy.py's already-adapted
Collector-framework port -- since all three are structurally the same
transparent stdio-shuttling proxy (spawn the real MCP server as a child
process, copy newline-delimited JSON-RPC lines in both directions
unmodified, log each line with a timestamp+direction). copilot_mcp_proxy.py
was chosen as the porting base specifically because its docstring already
documents the real, protocol-level quirks (mismatched initialize versions
across client/server, no auth field on the wire, tool errors reported via
isError:true inside a normal `result` rather than a JSON-RPC `error`
object) that generalize to any MCP client, Claude Code included -- these are
properties of the MCP stdio transport/spec itself, not Copilot-specific
behavior, so porting them forward is not fabrication.

FIELD GROUNDING (reference_tables/observables_table_claude_cursor.csv):
  - mcp_server           <- row "mcp_server": "MCP proxy" (both Claude Code
    and Cursor columns)
  - mcp_tool             <- row "mcp_tool": "MCP proxy" (both columns)
  - mcp_transport         <- row "mcp_transport": "MCP proxy" / Note: "Known
    at deploy time really." -- i.e. this collector's own server_command
    constructor argument, not something read off the wire.
  - mcp_server_version    <- row "mcp_server_version": "MCP proxy" / Note:
    "From the initialize handshake." -- the real initialize
    request/response pair this proxy shuttles and logs verbatim.
  - mcp_tools_available   <- row "mcp_tools_available": "MCP proxy" / Note:
    "Proxy only. The full menu the agent could reach, not just what it
    used." -- i.e. a real `tools/list` response line, passed through
    untouched like every other line.
  - tool_category         <- row "tool_category" has no single-technique
    citation in the CSV (blank Claude Code/Cursor source cells) but is
    listed in this task's brief as an MCP-proxy field; treated here as
    DERIVED (a later pipeline stage's job per core.py's scope note), not
    something this collector invents inside raw_payload.
  - tool_is_builtin       <- row "tool_is_builtin": "derived" in the CSV,
    same reasoning -- not computed here, only documented as a downstream
    concern.
  - secret_detected       <- row "secret_detected": "derived" in the CSV
    (Note: "Shape match on values, not the word 'token' in a sentence.") --
    again a later-stage classifier's job, NOT computed by this collector;
    listed in the task brief as something MCP-proxy traffic could feed a
    downstream secret-scanner, but this collector does not scan/redact/flag
    payload content itself (that would violate raw_payload's
    "completely unmodified" contract in core.py).
  - tool_arguments        <- row "tool_arguments": "local hooks " as
    primary source, Note: "Hooks give the full input. Proxy is the
    fallback for MCP." -- i.e. per the CSV, THIS collector (MCP proxy) is
    explicitly documented as the fallback path for MCP tool arguments when
    hooks are unavailable/insufficient, not the primary source.

MECHANISM: `_spawn_and_pump()` below is a direct, minimally-adapted port of
Copilot_MCP_Proxy/mcp_proxy.py's real transparent stdio proxy logic (already
carried into copilot_mcp_proxy.py's Collector-framework shape) -- spawn the
real target MCP server as a child process, shuttle newline-delimited
JSON-RPC lines bidirectionally, log each non-empty line as
{timestamp, direction, raw, parsed}.

No normalization: raw_payload is that exact {timestamp, direction, raw,
parsed} shape -- untouched, including whatever real quirks (mismatched
protocol versions, isError:true inside `result`, dual content/
structuredContent representations) appear on the wire.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path
from queue import Empty, Queue
from typing import Iterator, List, Optional

import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parents[2]))
from core import Collector, RawEvent

AGENT_NAME = "claude_code"
COLLECTOR_ID = "claude_code.mcp_proxy"

DIRECTION_CLIENT_TO_SERVER = "client_to_server"
DIRECTION_SERVER_TO_CLIENT = "server_to_client"


def _build_log_record(direction: str, raw_line: str) -> dict:
    """Direct port of Copilot_MCP_Proxy/mcp_proxy.py's log_event() record
    construction (via copilot_mcp_proxy.py's _build_log_record()) -- kept
    identical so live-mode and replay-mode records share one shape.
    """
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "direction": direction,
        "raw": raw_line,
    }
    try:
        record["parsed"] = json.loads(raw_line)
    except json.JSONDecodeError:
        record["parsed"] = None
    return record


class ClaudeCodeMCPProxyCollector(Collector):
    """MCP-proxy-based collector for Claude Code's MCP tool traffic.

    Live mode (real deployment): activate() spawns the real target MCP
    stdio server as a child process (per Claude Code's own `.mcp.json` /
    `~/.claude.json` `mcpServers` config -- see detect() below, which reads
    that real, confirmed-to-exist key) and this collector sits where Claude
    Code expects that server to be, shuttling+logging JSON-RPC lines in
    both directions. capture() drains a queue the pump threads fill.

    This build supports feeding capture() from already-captured JSONL
    records to exercise it deterministically. No real Claude-Code-specific
    MCP wire capture exists anywhere in this repo (see module docstring);
    the __main__ block below replays Copilot's real capture instead,
    clearly labeled as a different agent's real data, purely to exercise
    this collector's own plumbing end-to-end -- the same honesty pattern
    copilot_fs_watcher.py used for Antigravity's fs-watcher data.
    """

    def __init__(
        self,
        server_command: Optional[List[str]] = None,
        replay_records: Optional[List[dict]] = None,
    ) -> None:
        self._server_command = server_command
        self._replay_records = replay_records
        self._activated = False
        self._proc: Optional[subprocess.Popen] = None
        self._queue: "Queue" = Queue()
        self._threads: List[threading.Thread] = []

    @property
    def collector_id(self) -> str:
        return COLLECTOR_ID

    def detect(self) -> bool:
        """Real, cheap heuristics that Claude Code has MCP configured on
        this machine:
          1. `claude` binary on PATH (same check claude_code_hooks.py's
             detect() uses).
          2. `~/.claude.json` exists AND actually declares a non-empty
             `mcpServers` object for at least one project entry -- this is
             the REAL, confirmed-to-exist Claude Code config file/key on
             this machine (verified directly: `grep -n "mcpServers"
             ~/.claude.json` returns real matches under per-project keys),
             not an assumed/guessed path.
          3. A project-local `.mcp.json` exists (Claude Code's documented
             project-scoped MCP config file) and declares `mcpServers`.
        Side-effect-free: no subprocess spawned, no network call.
        """
        found_any = False

        if shutil.which("claude") is not None:
            found_any = True

        home_config = Path.home() / ".claude.json"
        if home_config.is_file():
            try:
                cfg = json.loads(home_config.read_text())
                # Real shape: top-level "mcpServers", and/or per-project
                # entries each with their own "mcpServers" key (confirmed
                # via `grep -n mcpServers ~/.claude.json` on this machine).
                if isinstance(cfg.get("mcpServers"), dict) and cfg["mcpServers"]:
                    found_any = True
                for proj in (cfg.get("projects") or {}).values():
                    if isinstance(proj, dict) and isinstance(proj.get("mcpServers"), dict) and proj["mcpServers"]:
                        found_any = True
                        break
            except (json.JSONDecodeError, OSError):
                pass

        cur = Path.cwd()
        for base in [cur, *cur.parents]:
            candidate = base / ".mcp.json"
            if candidate.is_file():
                try:
                    cfg = json.loads(candidate.read_text())
                    if isinstance(cfg.get("mcpServers"), dict) and cfg["mcpServers"]:
                        found_any = True
                except (json.JSONDecodeError, OSError):
                    pass
                break

        return found_any

    def activate(self) -> None:
        """In replay mode, a no-op besides marking activated. In live mode,
        spawns the real target MCP server as a child process (direct port
        of Copilot_MCP_Proxy/mcp_proxy.py's approach) and starts the two
        pump threads that shuttle+log JSON-RPC lines.
        """
        if self._replay_records is None and self._server_command is not None:
            self._spawn_and_pump()
        self._activated = True

    def _spawn_and_pump(self) -> None:
        """Port of Copilot_MCP_Proxy/mcp_proxy.py's main(): spawn the real
        server, run two daemon pump threads shuttling stdin<->server_stdin
        and server_stdout->stdout, logging every non-empty line via
        _build_log_record().
        """
        self._proc = subprocess.Popen(
            self._server_command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=sys.stderr,
        )

        def pump(src_stream, dst_stream, direction: str):
            for raw_line in iter(src_stream.readline, b""):
                if not raw_line:
                    break
                decoded = raw_line.decode("utf-8", errors="replace").rstrip("\n")
                if decoded.strip():
                    self._queue.put(_build_log_record(direction, decoded))
                dst_stream.write(raw_line)
                dst_stream.flush()
            self._queue.put(None)

        t_client_to_server = threading.Thread(
            target=pump,
            args=(sys.stdin.buffer, self._proc.stdin, DIRECTION_CLIENT_TO_SERVER),
            daemon=True,
        )
        t_server_to_client = threading.Thread(
            target=pump,
            args=(self._proc.stdout, sys.stdout.buffer, DIRECTION_SERVER_TO_CLIENT),
            daemon=True,
        )
        t_client_to_server.start()
        t_server_to_client.start()
        self._threads = [t_client_to_server, t_server_to_client]

    def capture(self) -> Iterator[RawEvent]:
        if not self._activated:
            raise RuntimeError(f"{self.collector_id}: capture() called before activate()")

        if self._replay_records is not None:
            for record in self._replay_records:
                yield RawEvent(
                    timestamp=RawEvent.now_iso(),
                    collector_id=self.collector_id,
                    agent=AGENT_NAME,
                    raw_payload=record,
                )
            return

        sentinels_seen = 0
        while sentinels_seen < len(self._threads):
            try:
                item = self._queue.get(timeout=1.0)
            except Empty:
                continue
            if item is None:
                sentinels_seen += 1
                continue
            yield RawEvent(
                timestamp=RawEvent.now_iso(),
                collector_id=self.collector_id,
                agent=AGENT_NAME,
                raw_payload=item,
            )

    def deactivate(self) -> None:
        if self._proc is not None:
            try:
                self._proc.terminate()
            except OSError:
                pass
        self._activated = False


if __name__ == "__main__":
    print("=== ClaudeCodeMCPProxyCollector standalone test ===")

    collector = ClaudeCodeMCPProxyCollector()
    detected = collector.detect()
    print(f"detect() -> {detected} (checks `claude` on PATH + ~/.claude.json / .mcp.json mcpServers)")

    mcp_proxy_dir = Path(__file__).resolve().parents[4] / "Copilot_MCP_Proxy"
    real_files = {
        "run1 (success)": mcp_proxy_dir / "captured_mcp_events_run1_success.jsonl",
        "run2 (tool error)": mcp_proxy_dir / "captured_mcp_events_run2_toolerror.jsonl",
        "run3 (connection failure)": mcp_proxy_dir / "captured_mcp_events_run3_connfail.jsonl",
    }

    print(
        "\nNo Claude-Code-specific MCP wire capture exists anywhere in this "
        "repo (see module docstring). Replaying Copilot's real captures below "
        "instead, clearly labeled as a DIFFERENT agent's real data, purely to "
        "exercise this collector's own plumbing end-to-end (the JSON-RPC/MCP "
        "wire protocol itself is agent-agnostic, per the module docstring's "
        "reasoning for porting the mechanism at all)."
    )

    for label, path in real_files.items():
        print(f"\n--- SIMULATED FROM CAPTURED DATA (real, but Copilot's not Claude's): {label} ({path.name}) ---")
        if not path.is_file():
            print(f"  Real evidence file not found at {path} -- skipping.")
            continue

        records = []
        with open(path) as f:
            for line in f:
                line = line.strip()
                if line:
                    records.append(json.loads(line))

        replay_collector = ClaudeCodeMCPProxyCollector(replay_records=records)
        replay_collector.activate()
        events = list(replay_collector.capture())
        replay_collector.deactivate()

        for event in events:
            payload = event.raw_payload
            parsed = payload.get("parsed") or {}
            method = parsed.get("method")
            has_error_result = (
                isinstance(parsed.get("result"), dict) and parsed["result"].get("isError") is True
            )
            summary = method or ("isError=true result" if has_error_result else "result/other")
            print(
                f"  collector_id={event.collector_id!r} agent={event.agent!r} "
                f"direction={payload.get('direction')!r} summary={summary!r}"
            )

    print(f"\nTotal replayed evidence runs: {len(real_files)}")
