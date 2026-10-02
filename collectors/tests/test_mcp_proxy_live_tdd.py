"""Red → green: live MCP proxy (LIVE_MCP.md)."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from unittest import TestCase

from collectors.antigravity.antigravity_mcp_proxy import AntigravityMCPProxyCollector
from collectors.claude_code.claude_code_mcp_proxy import ClaudeCodeMCPProxyCollector
from collectors.codex.codex_mcp_proxy import CodexMCPProxyCollector
from collectors.copilot.copilot_mcp_proxy import CopilotMCPProxyCollector
from collectors.cursor.cursor_mcp_proxy import CursorMCPProxyCollector
from collectors.live_mcp import build_log_record

_ECHO = [
    sys.executable,
    "-c",
    "import sys; line=sys.stdin.buffer.readline(); sys.stdout.buffer.write(line); sys.stdout.buffer.flush()",
]


def _pipes():
    r_in, w_in = os.pipe()
    r_out, w_out = os.pipe()
    return (
        os.fdopen(r_in, "rb"),
        os.fdopen(w_out, "wb"),
        os.fdopen(w_in, "wb"),
        os.fdopen(r_out, "rb"),
    )


def _post(url: str, payload: dict) -> None:
    data = json.dumps(payload).encode("utf-8")
    last_exc: Exception = RuntimeError("post failed")
    for _ in range(20):
        req = urllib.request.Request(
            url, data=data, headers={"Content-Type": "application/json"}, method="POST"
        )
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                resp.read()
            return
        except (urllib.error.URLError, ConnectionError, OSError) as exc:
            last_exc = exc
            time.sleep(0.05)
    raise last_exc


def _drain(collector, bucket: list) -> None:
    for ev in collector.capture():
        bucket.append(ev)


class BuildLogRecordTests(TestCase):
    def test_parses_json(self) -> None:
        rec = build_log_record("client_to_server", '{"method": "initialize"}')
        self.assertEqual(rec["parsed"]["method"], "initialize")
        self.assertEqual(rec["direction"], "client_to_server")


class ReplayMcpTests(TestCase):
    def test_claude_replay(self) -> None:
        rec = {"direction": "client_to_server", "parsed": {"method": "initialize"}}
        c = ClaudeCodeMCPProxyCollector(replay_records=[rec])
        c.activate()
        events = list(c.capture())
        c.deactivate()
        self.assertEqual(events[0].agent, "claude_code")
        self.assertEqual(events[0].raw_payload, rec)

    def test_antigravity_replay_path(self) -> None:
        path = Path(tempfile.mkdtemp()) / "mcp.jsonl"
        rec = {"direction": "server_to_client", "parsed": {"method": "server/discover"}}
        path.write_text(json.dumps(rec) + "\n", encoding="utf-8")
        c = AntigravityMCPProxyCollector(replay_path=path)
        c.activate()
        events = list(c.capture())
        c.deactivate()
        self.assertEqual(events[0].agent, "antigravity")
        self.assertEqual(events[0].raw_payload["parsed"]["method"], "server/discover")


class LiveStdioMcpTdd(TestCase):
    def test_copilot_echo_roundtrip(self) -> None:
        cin, cout, writer, reader = _pipes()
        c = CopilotMCPProxyCollector(
            server_command=_ECHO,
            replay_records=None,
            client_in=cin,
            client_out=cout,
        )
        c.activate()
        got: list = []
        thread = threading.Thread(target=_drain, args=(c, got), daemon=True)
        thread.start()
        time.sleep(0.1)
        line = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize"}) + "\n"
        writer.write(line.encode("utf-8"))
        writer.flush()
        deadline = time.time() + 2.0
        while time.time() < deadline:
            if any((ev.raw_payload.get("parsed") or {}).get("method") == "initialize" for ev in got):
                break
            time.sleep(0.05)
        c.deactivate()
        writer.close()
        reader.close()
        thread.join(timeout=5)
        self.assertTrue(got)
        self.assertEqual(got[0].agent, "copilot")
        self.assertEqual(got[0].raw_payload["parsed"]["method"], "initialize")

    def test_codex_echo_roundtrip(self) -> None:
        cin, cout, writer, reader = _pipes()
        c = CodexMCPProxyCollector(
            replay_events=None,
            server_command=_ECHO,
            client_in=cin,
            client_out=cout,
        )
        c.activate()
        got: list = []
        thread = threading.Thread(target=_drain, args=(c, got), daemon=True)
        thread.start()
        time.sleep(0.1)
        line = json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/call"}) + "\n"
        writer.write(line.encode("utf-8"))
        writer.flush()
        deadline = time.time() + 2.0
        while time.time() < deadline:
            if any((ev.raw_payload.get("parsed") or {}).get("method") == "tools/call" for ev in got):
                break
            time.sleep(0.05)
        c.deactivate()
        writer.close()
        reader.close()
        thread.join(timeout=5)
        self.assertEqual(got[0].agent, "codex")


class LiveCursorMcpTdd(TestCase):
    def test_usage_flow_becomes_network_capture(self) -> None:
        c = CursorMCPProxyCollector(replay_flows=None, listen_port=0)
        c.activate()
        got: list = []
        thread = threading.Thread(target=_drain, args=(c, got), daemon=True)
        thread.start()
        time.sleep(0.05)
        _post(
            f"http://127.0.0.1:{c.listen_port}/webhook/cursor/mcp",
            {
                "route": "/aiserver.v1.ChatService/StreamChat",
                "session_id": "s1",
                "request_body": "",
                "response_body": '{"promptTokenCount": 812, "candidatesTokenCount": 640}',
            },
        )
        time.sleep(0.05)
        c.deactivate()
        thread.join(timeout=5)
        self.assertEqual(got[0].agent, "cursor")
        self.assertEqual(got[0].raw_payload["event_type"], "network_context_capture")
        self.assertEqual(got[0].raw_payload["payload"]["usage"]["input_tokens"], 812)

    def test_ignore_routes_drop(self) -> None:
        c = CursorMCPProxyCollector(replay_flows=None, listen_port=0)
        c.activate()
        got: list = []
        thread = threading.Thread(target=_drain, args=(c, got), daemon=True)
        thread.start()
        time.sleep(0.05)
        _post(
            f"http://127.0.0.1:{c.listen_port}/webhook/cursor/mcp",
            {"route": "/aiserver.v1.AnalyticsService/Track", "session_id": "s", "response_body": '{"promptTokenCount": 900, "candidatesTokenCount": 900}'},
        )
        time.sleep(0.08)
        c.deactivate()
        thread.join(timeout=5)
        self.assertEqual(got, [])
