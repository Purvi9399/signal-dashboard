"""Unit tests: live_curate (TDD)."""

from __future__ import annotations

import unittest

from export.live_curate import curate_raw_event_dicts, stamp_observables


class StampObservablesTdd(unittest.TestCase):
    def test_stamps_device_and_batch(self) -> None:
        rows = stamp_observables(
            [{"dedup_key": "d1", "tool": "antigravity", "observable_type": "session_start"}],
            tenant_id="tenant-a",
            device_id="dev-1",
            ingest_batch_id="batch-9",
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["tenant_id"], "tenant-a")
        self.assertEqual(rows[0]["device_id"], "dev-1")
        self.assertEqual(rows[0]["ingest_batch_id"], "batch-9")

    def test_preserves_existing_tenant_id(self) -> None:
        rows = stamp_observables(
            [{"tenant_id": "keep-me", "dedup_key": "d1"}],
            tenant_id="other",
            device_id="dev-1",
            ingest_batch_id="batch-9",
        )
        self.assertEqual(rows[0]["tenant_id"], "keep-me")


class CurateRawEventDictsTdd(unittest.TestCase):
    def test_empty_input_yields_empty(self) -> None:
        rows = curate_raw_event_dicts(
            [],
            tenant_id="t",
            device_id="d",
            ingest_batch_id="b",
        )
        self.assertEqual(rows, [])

    def test_skips_untyped_noise(self) -> None:
        rows = curate_raw_event_dicts(
            [
                {
                    "timestamp": "2026-08-26T15:22:28+00:00",
                    "collector_id": "antigravity.hooks",
                    "agent": "antigravity",
                    "raw_payload": {"conversationId": "x", "noise": True},
                }
            ],
            tenant_id="t",
            device_id="d",
            ingest_batch_id="b",
        )
        self.assertEqual(rows, [])

    def test_session_start_curates(self) -> None:
        rows = curate_raw_event_dicts(
            [
                {
                    "timestamp": "2026-08-26T15:22:28+00:00",
                    "collector_id": "antigravity.hooks",
                    "agent": "antigravity",
                    "raw_payload": {
                        "conversationId": "sess-tdd",
                        "initialNumSteps": 1,
                        "modelName": "m",
                    },
                }
            ],
            tenant_id="t",
            device_id="d",
            ingest_batch_id="batch-tdd",
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["tool"], "antigravity")
        self.assertEqual(rows[0]["observable_type"], "session_start")
        self.assertEqual(rows[0]["device_id"], "d")
        self.assertEqual(rows[0]["ingest_batch_id"], "batch-tdd")
        self.assertTrue(rows[0].get("dedup_key"))


if __name__ == "__main__":
    unittest.main()
