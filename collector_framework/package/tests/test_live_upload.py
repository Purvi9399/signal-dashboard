"""Live session flush and summary."""

from __future__ import annotations

import unittest
from unittest import mock

from export.validate_ingest import MAX_EVENTS
from package.live_upload import flush_session, summarize_uploads, try_flush_session


_EVENT = {
    "timestamp": "2026-10-09T12:00:00+00:00",
    "collector_id": "cursor.hooks",
    "agent": "cursor",
    "raw_payload": {"event_type": "stop", "conversationId": "c1"},
}


class FlushSessionTests(unittest.TestCase):
    def test_empty_returns_no_batches(self) -> None:
        self.assertEqual(flush_session([]), [])

    def test_chunks_over_max_events(self) -> None:
        events = [_EVENT] * (MAX_EVENTS + 1)
        with mock.patch(
            "package.live_upload.upload_event_dicts",
            side_effect=lambda chunk, origin="live-capture": {"inserted": len(chunk)},
        ) as upload:
            results = flush_session(events)
        self.assertEqual(len(results), 2)
        self.assertEqual(upload.call_count, 2)
        self.assertEqual(len(upload.call_args_list[0][0][0]), MAX_EVENTS)
        self.assertEqual(len(upload.call_args_list[1][0][0]), 1)

    def test_try_flush_records_error(self) -> None:
        batches: list = []
        with mock.patch(
            "package.live_upload.flush_session",
            side_effect=ValueError("events exceed max 500"),
        ):
            try_flush_session([_EVENT], batches)
        self.assertEqual(batches[0]["error"], "events exceed max 500")

    def test_try_flush_records_attribute_error(self) -> None:
        batches: list = []
        with mock.patch(
            "package.live_upload.flush_session",
            side_effect=AttributeError("'str' object has no attribute 'get'"),
        ):
            try_flush_session([_EVENT], batches)
        self.assertIn("has no attribute 'get'", batches[0]["error"])


class SummarizeUploadsTests(unittest.TestCase):
    def test_sums_and_collects_ids(self) -> None:
        summary = summarize_uploads(
            [
                {
                    "mode": "edge",
                    "inserted": 2,
                    "observables_curated": 1,
                    "observables_upserted": 1,
                    "events_read": 2,
                    "ingest_batch_id": "a",
                },
                {
                    "mode": "edge",
                    "inserted": 3,
                    "observables_curated": 2,
                    "observables_upserted": 2,
                    "events_read": 3,
                    "ingest_batch_id": "b",
                },
            ]
        )
        self.assertEqual(summary["sessions_uploaded"], 2)
        self.assertEqual(summary["inserted"], 5)
        self.assertEqual(summary["ingest_batch_ids"], ["a", "b"])
        self.assertEqual(summary["ingest_batch_id"], "b")
        self.assertEqual(summary["errors"], [])


if __name__ == "__main__":
    unittest.main()
