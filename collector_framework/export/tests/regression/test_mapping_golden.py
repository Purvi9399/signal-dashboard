"""Regression: freeze mapping for verified live RawEvent shapes."""

from __future__ import annotations

import unittest

from export.mapping import map_raw_event_dict


GOLDEN_ANTIGRAVITY = {
    "timestamp": "2026-09-10T09:47:27.564149+00:00",
    "collector_id": "antigravity.hooks",
    "agent": "antigravity",
    "raw_payload": {
        "conversationId": "6189e919-cbb8-4ac8-9bbd-efd9d5ba40bb",
        "toolCall": {"name": "write_to_file", "args": {"TargetFile": "p01_hello.py"}},
    },
}


class MappingRegressionTests(unittest.TestCase):
    def test_antigravity_hooks_golden(self) -> None:
        row = map_raw_event_dict(
            GOLDEN_ANTIGRAVITY,
            tenant_id="00000000-0000-0000-0000-000000000001",
            device_id="pilot-mac-1",
            ingest_batch_id="d69ca937-7e85-43fb-9fb9-cfb1f5ae28b0",
            file_origin="live_capture_output.jsonl",
            source_ref="line:2",
        )
        self.assertEqual(row["tool"], "antigravity")
        self.assertEqual(row["collector"], "local_hooks")
        self.assertEqual(row["session_id"], "6189e919-cbb8-4ac8-9bbd-efd9d5ba40bb")
        self.assertEqual(row["event_name"], "write_to_file")
        self.assertEqual(row["payload"]["agent"], "antigravity")
        self.assertEqual(
            row["payload"]["raw_payload"]["toolCall"]["name"], "write_to_file"
        )


if __name__ == "__main__":
    unittest.main()
