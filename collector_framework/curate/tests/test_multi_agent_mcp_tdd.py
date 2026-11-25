"""Red-green TDD: multi-agent MCP Proxy RawEvent → curator tuples (Phase 2)."""

from __future__ import annotations

import unittest
from datetime import datetime

from curate.adapter_source_events import adapt_raw_event


def _env(agent: str, collector_id: str, raw: dict, ts: str = "2026-09-21T11:00:00+00:00"):
    return {
        "timestamp": ts,
        "collector_id": collector_id,
        "agent": agent,
        "raw_payload": raw,
    }


def _stdio(direction: str, parsed: dict | None) -> dict:
    return {
        "timestamp": "2026-09-21T11:00:00+00:00",
        "direction": direction,
        "raw": "",
        "parsed": parsed,
    }


class StdioToolsCallRequestTdd(unittest.TestCase):
    def test_copilot_tools_call_request(self) -> None:
        triple = adapt_raw_event(
            _env(
                "copilot",
                "copilot.mcp_proxy",
                _stdio(
                    "client_to_server",
                    {
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "tools/call",
                        "params": {"name": "read_file", "arguments": {"path": "/a.py"}},
                    },
                ),
            )
        )
        self.assertIsNotNone(triple)
        coll, fields, _ = triple
        self.assertEqual(coll, "mcp_proxy")
        self.assertEqual(fields["tool"], "copilot")
        self.assertEqual(fields["observable_type"], "mcp_request")
        self.assertEqual(fields["mcp_tool"], "read_file")
        self.assertEqual(fields["tool_arguments"], {"path": "/a.py"})
        self.assertIsInstance(fields["occurred_at"], datetime)

    def test_codex_tools_call_request(self) -> None:
        triple = adapt_raw_event(
            _env(
                "codex",
                "codex.mcp_proxy",
                _stdio(
                    "client_to_server",
                    {
                        "jsonrpc": "2.0",
                        "id": 2,
                        "method": "tools/call",
                        "params": {"name": "shell", "arguments": {"command": "ls"}},
                    },
                ),
            )
        )
        self.assertIsNotNone(triple)
        _, fields, _ = triple
        self.assertEqual(fields["tool"], "codex")
        self.assertEqual(fields["observable_type"], "mcp_request")
        self.assertEqual(fields["mcp_tool"], "shell")


class StdioToolsCallResultTdd(unittest.TestCase):
    def test_antigravity_tools_call_result(self) -> None:
        triple = adapt_raw_event(
            _env(
                "antigravity",
                "antigravity.mcp_proxy",
                _stdio(
                    "server_to_client",
                    {
                        "jsonrpc": "2.0",
                        "id": 1,
                        "result": {"content": [{"type": "text", "text": "ok"}], "isError": False},
                    },
                ),
            )
        )
        self.assertIsNotNone(triple)
        _, fields, _ = triple
        self.assertEqual(fields["tool"], "antigravity")
        self.assertEqual(fields["observable_type"], "mcp_result")
        self.assertIsNotNone(fields.get("tool_result"))

    def test_claude_tools_call_error_result(self) -> None:
        triple = adapt_raw_event(
            _env(
                "claude_code",
                "claude_code.mcp_proxy",
                _stdio(
                    "server_to_client",
                    {
                        "jsonrpc": "2.0",
                        "id": 3,
                        "result": {"content": [{"type": "text", "text": "fail"}], "isError": True},
                    },
                ),
            )
        )
        self.assertIsNotNone(triple)
        _, fields, _ = triple
        self.assertEqual(fields["tool"], "claude-code")
        self.assertEqual(fields["observable_type"], "mcp_result")
        self.assertIs(fields.get("success"), False)


class StdioToolsListDiscoveryTdd(unittest.TestCase):
    def test_tools_list_result(self) -> None:
        triple = adapt_raw_event(
            _env(
                "copilot",
                "copilot.mcp_proxy",
                _stdio(
                    "server_to_client",
                    {
                        "jsonrpc": "2.0",
                        "id": 0,
                        "result": {
                            "tools": [
                                {"name": "read_file"},
                                {"name": "write_file"},
                            ]
                        },
                    },
                ),
            )
        )
        self.assertIsNotNone(triple)
        _, fields, _ = triple
        self.assertEqual(fields["observable_type"], "mcp_discovery")
        self.assertEqual(fields["mcp_tools_available"], ["read_file", "write_file"])


class StdioSkipNoiseTdd(unittest.TestCase):
    def test_initialize_skipped(self) -> None:
        triple = adapt_raw_event(
            _env(
                "copilot",
                "copilot.mcp_proxy",
                _stdio(
                    "client_to_server",
                    {"jsonrpc": "2.0", "id": 0, "method": "initialize", "params": {}},
                ),
            )
        )
        self.assertIsNone(triple)

    def test_null_parsed_skipped(self) -> None:
        triple = adapt_raw_event(
            _env("codex", "codex.mcp_proxy", _stdio("client_to_server", None))
        )
        self.assertIsNone(triple)


class CursorNetworkProxyTdd(unittest.TestCase):
    def test_token_usage_maps_model_request(self) -> None:
        triple = adapt_raw_event(
            _env(
                "cursor",
                "cursor.mcp_proxy",
                {
                    "event_type": "network_context_capture",
                    "session_id": "sess-cur",
                    "payload": {
                        "usage": {"input_tokens": 1200, "output_tokens": 340},
                    },
                    "runtime_enrichment": {
                        "source": "mitmproxy_interceptor",
                        "token_source": "cursor_reported",
                    },
                },
            )
        )
        self.assertIsNotNone(triple)
        coll, fields, _ = triple
        self.assertEqual(coll, "mcp_proxy")
        self.assertEqual(fields["tool"], "cursor")
        self.assertEqual(fields["observable_type"], "model_request")
        self.assertEqual(fields["session_id"], "sess-cur")
        self.assertEqual(fields["input_tokens"], 1200)
        self.assertEqual(fields["output_tokens"], 340)
        self.assertEqual(fields["total_tokens"], 1540)


if __name__ == "__main__":
    unittest.main()
