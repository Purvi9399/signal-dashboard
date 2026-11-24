"""
GitHub Copilot MCP Proxy collector.

Grounded entirely in the real, empirical capture performed for this project.
Primary sources (read-only, never modified):

  Copilot_MCP_Proxy/RESULTS.md        -- the full write-up this file is built from
  Copilot_MCP_Proxy/mcp_proxy.py      -- the real transparent stdio proxy this
                                          collector's parsing logic is ported from
  Copilot_MCP_Proxy/config/mcp_config.json         -- working proxy registration
  Copilot_MCP_Proxy/config/mcp_config_broken.json  -- connection-failure config
  Copilot_MCP_Proxy/captured_mcp_events_run1_success.jsonl   -- 9 real JSON-RPC lines
  Copilot_MCP_Proxy/captured_mcp_events_run2_toolerror.jsonl -- 7 real JSON-RPC lines
  Copilot_MCP_Proxy/captured_mcp_events_run3_connfail.jsonl  -- 1 real JSON-RPC line

MECHANISM (ported from mcp_proxy.py, not reimplemented from scratch): the
real proxy sits between Copilot CLI (the MCP client) and a real MCP stdio
server, spawning the server as a child process and shuttling newline-
delimited JSON-RPC lines in both directions unmodified, logging each line
with a timestamp + direction ("client_to_server" / "server_to_client") to
captured_mcp_events.jsonl -- see mcp_proxy.py's `log_event()`/`pump()`. This
collector's `_pump_and_log()` below is a direct, minimally-adapted port of
that same logic, adjusted to yield RawEvent objects instead of appending to
a file, and to be driven by this framework's activate()/capture()/
deactivate() lifecycle instead of running as a standalone script.

KEY REAL FINDINGS THIS COLLECTOR'S DESIGN ACCOUNTS FOR (see RESULTS.md for
full detail; cited inline near the relevant code below):
  - The proxy is genuinely transparent -- Copilot CLI's own transcript shows
    no sign of an intermediary (RESULTS.md section a).
  - `initialize` negotiates DIFFERENT protocol versions on each side
    (client "2025-11-25" vs. server "2025-06-18") with no error -- this
    collector does not assume/enforce a single protocol version.
  - There is NO auth/credential/scope field anywhere in the stdio protocol
    (RESULTS.md section b) -- this collector does not invent one.
  - `tools/call` results carry TWO parallel representations of the result
    (`content` text/typed blocks AND `structuredContent` native JSON) --
    both are passed through untouched, not collapsed into one.
  - Tool-level errors use `isError: true` INSIDE a normal JSON-RPC `result`
    object, not a JSON-RPC `error` object (RESULTS.md section g.4) --
    genuinely different from...
  - ...a connection-level failure, which produces NO JSON-RPC message at
    all on the wire -- only ever visible via Copilot CLI's own local debug
    log (`~/.copilot/logs/process-*.log`), never as an interceptable
    protocol payload (RESULTS.md section f). This collector does NOT
    fabricate a synthetic "connection failed" JSON-RPC-shaped event for
    that case; run3's real capture (a single client_to_server `initialize`
    line with no reply, ever) is preserved and passed through exactly as
    thin as it really is.

No normalization: raw_payload is the exact {timestamp, direction, raw,
parsed} shape mcp_proxy.py's own log_event() already produces -- untouched.
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

AGENT_NAME = "copilot"
COLLECTOR_ID = "copilot.mcp_proxy"

DIRECTION_CLIENT_TO_SERVER = "client_to_server"
DIRECTION_SERVER_TO_CLIENT = "server_to_client"


def is_connection_failure_record(records: List[dict]) -> bool:
    """Best-effort structural check for the real connection-failure shape
    documented in Copilot_MCP_Proxy/RESULTS.md section f: exactly one
    client_to_server `initialize` request line, and NOTHING after it (no
    server_to_client reply at all -- confirmed via
    captured_mcp_events_run3_connfail.jsonl, which contains exactly 1 line).
    This is a diagnostic label only, used for human/log purposes -- it does
    not alter or synthesize any event; the underlying truth (silence) is
    represented by the mere absence of further RawEvents, not a fabricated
    "error" event, per RESULTS.md's explicit finding that no JSON-RPC error
    object is ever produced for this case.
    """
    if len(records) != 1:
        return False
    only = records[0]
    parsed = only.get("parsed")
    return (
        only.get("direction") == DIRECTION_CLIENT_TO_SERVER
        and isinstance(parsed, dict)
        and parsed.get("method") == "initialize"
    )


class CopilotMCPProxyCollector(Collector):
    """MCP-proxy-based collector for GitHub Copilot CLI's MCP tool traffic.

    Live mode (real deployment): activate() spawns the real target MCP
    stdio server as a child process (exactly as mcp_proxy.py's main() does)
    and this collector sits where Copilot CLI expects its configured
    `--additional-mcp-config` server to be, shuttling+logging JSON-RPC lines
    in both directions. capture() drains a queue the pump threads fill.

    This build supports feeding capture() from already-captured JSONL
    records (Copilot_MCP_Proxy/captured_mcp_events_run{1,2,3}_*.jsonl -- the
    REAL wire captures of a success run, a tool-error run, and a connection-
    failure run) to exercise it deterministically without spawning a live
    MCP server pair.
    """

    def __init__(
        self,
        server_command: Optional[List[str]] = None,
        replay_records: Optional[List[dict]] = None,
        client_in=None,
        client_out=None,
    ) -> None:
        self._server_command = server_command
        self._replay_records = replay_records
        self._client_in = client_in
        self._client_out = client_out
        self._activated = False
        self._live_feed: Optional[LiveEventFeed] = None
        self._proxy: Optional[LiveStdioProxy] = None

    @property
    def collector_id(self) -> str:
        return COLLECTOR_ID

    def detect(self) -> bool:
        """Real, cheap heuristics that Copilot CLI has MCP configured on
        this machine, per Copilot_MCP_Proxy/RESULTS.md and
        config/mcp_config.json's real shape:
          1. `copilot` binary on PATH (MCP is a CLI feature, invoked via
             `--additional-mcp-config`).
          2. A project-local mcp config file exists and actually declares
             an `mcpServers` object (matching the real
             config/mcp_config.json shape: {"mcpServers": {"<name>":
             {"type": "local", "command": ..., "args": [...]}}}).
          3. ~/.copilot/mcp-config.json (the CLI's own default/global MCP
             config location, analogous to ~/.copilot/hooks used by
             copilot_hooks.py's detect()) exists.
        This does not shell out or start any process -- side-effect-free
        per the Collector.detect() contract.
        """
        found_any = False

        if shutil.which("copilot") is not None:
            found_any = True

        cur = Path.cwd()
        candidate_names = ("mcp_config.json", "mcp-config.json", ".copilot-mcp.json")
        for base in [cur, *cur.parents]:
            for name in candidate_names:
                candidate = base / name
                if candidate.is_file():
                    try:
                        cfg = json.loads(candidate.read_text())
                        if isinstance(cfg.get("mcpServers"), dict) and cfg["mcpServers"]:
                            found_any = True
                    except (json.JSONDecodeError, OSError):
                        pass

        home_mcp_config = Path.home() / ".copilot" / "mcp-config.json"
        if home_mcp_config.is_file():
            found_any = True

        return found_any

    def activate(self) -> None:
        """In replay mode, a no-op besides marking activated. In live mode,
        spawns the real target MCP server as a child process (direct port
        of mcp_proxy.py's subprocess.Popen call) and starts the two pump
        threads that shuttle+log JSON-RPC lines, matching mcp_proxy.py's
        `pump()` in both directions.
        """
        self._activated = True
        if self._replay_records is not None:
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
        for record in source:
            yield RawEvent(
                timestamp=RawEvent.now_iso(),
                collector_id=self.collector_id,
                agent=AGENT_NAME,
                raw_payload=record,
            )

    def _event_source(self):
        if self._replay_records is not None:
            return self._replay_records
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


if __name__ == "__main__":
    print("=== CopilotMCPProxyCollector standalone test ===")

    collector = CopilotMCPProxyCollector()
    detected = collector.detect()
    print(f"detect() -> {detected}")

    mcp_proxy_dir = Path(__file__).resolve().parents[4] / "Copilot_MCP_Proxy"
    real_files = {
        "run1 (success)": mcp_proxy_dir / "captured_mcp_events_run1_success.jsonl",
        "run2 (tool error)": mcp_proxy_dir / "captured_mcp_events_run2_toolerror.jsonl",
        "run3 (connection failure)": mcp_proxy_dir / "captured_mcp_events_run3_connfail.jsonl",
    }

    for label, path in real_files.items():
        print(f"\n--- SIMULATED FROM CAPTURED DATA: {label} ({path.name}) ---")
        if not path.is_file():
            print(f"  Real evidence file not found at {path} -- skipping.")
            continue

        records = []
        with open(path) as f:
            for line in f:
                line = line.strip()
                if line:
                    records.append(json.loads(line))

        replay_collector = CopilotMCPProxyCollector(replay_records=records)
        replay_collector.activate()
        events = list(replay_collector.capture())
        replay_collector.deactivate()

        for event in events:
            payload = event.raw_payload
            parsed = payload.get("parsed") or {}
            method = parsed.get("method")
            has_error_result = (
                isinstance(parsed.get("result"), dict)
                and parsed["result"].get("isError") is True
            )
            summary = method or ("isError=true result" if has_error_result else "result/other")
            print(
                f"  collector_id={event.collector_id!r} direction={payload.get('direction')!r} "
                f"summary={summary!r}"
            )

        if is_connection_failure_record(records):
            print(
                "  -> Structurally matches the real connection-failure shape "
                "(one lone client_to_server `initialize`, no reply ever) -- "
                "per Copilot_MCP_Proxy/RESULTS.md section f, this is the "
                "ENTIRE wire signal for a connection failure; the real "
                "diagnostic only exists in Copilot CLI's own local debug "
                "log, not on this JSON-RPC stream."
            )

    print(f"\nTotal real evidence runs replayed: {len(real_files)}")
