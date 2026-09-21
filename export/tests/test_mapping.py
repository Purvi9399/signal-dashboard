"""Unit tests for RawEvent -> source_events mapping (no network)."""

from __future__ import annotations

import unittest

from export.mapping import map_collector, map_raw_event_dict, map_tool


class MapToolTests(unittest.TestCase):
    def test_known_agents(self) -> None:
        self.assertEqual(map_tool("antigravity"), "antigravity")
        self.assertEqual(map_tool("cursor"), "cursor")
        self.assertEqual(map_tool("claude_code"), "claude-code")
        self.assertEqual(map_tool("Claude-Code"), "claude-code")

    def test_unknown(self) -> None:
        self.assertEqual(map_tool("something-else"), "unknown")
        self.assertEqual(map_tool(None), "unknown")


class MapCollectorTests(unittest.TestCase):
    def test_technique_suffix(self) -> None:
        self.assertEqual(map_collector("antigravity.hooks"), "local_hooks")
        self.assertEqual(map_collector("cursor.local_hooks"), "local_hooks")
        self.assertEqual(map_collector("codex.otel"), "otel")
        self.assertEqual(map_collector("claude_code.fs_watcher"), "fs_watcher")
        self.assertEqual(map_collector("copilot.mcp_proxy"), "mcp_proxy")
        self.assertEqual(map_collector("x.cli_wrapper"), "cli_wrapper")

    def test_unknown_suffix(self) -> None:
        self.assertEqual(map_collector("agent.weird"), "derived")
        self.assertEqual(map_collector(None), "derived")


class MapRowTests(unittest.TestCase):
    def test_antigravity_hooks_envelope(self) -> None:
        event = {
            "timestamp": "2026-09-10T09:47:27.564149+00:00",
            "collector_id": "antigravity.hooks",
            "agent": "antigravity",
            "raw_payload": {
                "conversationId": "6189e919-cbb8-4ac8-9bbd-efd9d5ba40bb",
                "toolCall": {"name": "write_to_file", "args": {}},
            },
        }
        row = map_raw_event_dict(
            event,
            tenant_id="00000000-0000-0000-0000-000000000001",
            device_id="pilot-mac-1",
            ingest_batch_id="11111111-1111-1111-1111-111111111111",
            file_origin="/tmp/live.jsonl",
            source_ref="line:2",
        )
        self.assertEqual(row["tool"], "antigravity")
        self.assertEqual(row["collector"], "local_hooks")
        self.assertEqual(row["session_id"], "6189e919-cbb8-4ac8-9bbd-efd9d5ba40bb")
        self.assertEqual(row["event_name"], "write_to_file")
        self.assertEqual(row["device_id"], "pilot-mac-1")
        self.assertEqual(row["payload"]["collector_id"], "antigravity.hooks")
        self.assertEqual(row["payload"]["raw_payload"]["conversationId"], event["raw_payload"]["conversationId"])
        self.assertEqual(row["file_origin"], "/tmp/live.jsonl")
        self.assertEqual(row["source_ref"], "line:2")
        self.assertNotIn("identity", row["payload"])


if __name__ == "__main__":
    unittest.main()
