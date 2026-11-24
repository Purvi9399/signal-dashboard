"""Unit tests for on-device live curate (offline)."""

from __future__ import annotations

import unittest

from export.live_curate import curate_raw_event_dicts


class LiveCurateTests(unittest.TestCase):
    def test_antigravity_write_produces_observable(self) -> None:
        events = [
            {
                "timestamp": "2026-08-26T15:22:35.650686+00:00",
                "collector_id": "antigravity.hooks",
                "agent": "antigravity",
                "raw_payload": {
                    "conversationId": "sess-a",
                    "modelName": "gemini-x",
                    "toolCall": {
                        "name": "write_to_file",
                        "args": {
                            "TargetFile": "/tmp/a.py",
                            "Overwrite": True,
                            "toolAction": "Creating file",
                        },
                    },
                },
            }
        ]
        rows = curate_raw_event_dicts(
            events,
            tenant_id="00000000-0000-0000-0000-000000000001",
            device_id="pilot-mac-1",
            ingest_batch_id="batch-curate-1",
        )
        self.assertGreaterEqual(len(rows), 1)
        self.assertEqual(rows[0]["tool"], "antigravity")
        self.assertNotEqual(rows[0]["observable_type"], "other")
        self.assertEqual(rows[0]["device_id"], "pilot-mac-1")
        self.assertEqual(rows[0]["ingest_batch_id"], "batch-curate-1")
        self.assertTrue(rows[0].get("dedup_key"))


if __name__ == "__main__":
    unittest.main()
