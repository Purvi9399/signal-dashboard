"""Unit tests for ingest body validation (offline)."""

from __future__ import annotations

import unittest

from export.validate_ingest import (
    MAX_EVENTS,
    validate_event_rows,
    validate_ingest_body,
    validate_observables,
)


class ValidateIngestTests(unittest.TestCase):
    def test_empty_events_ok(self) -> None:
        validate_event_rows([])

    def test_rejects_over_max(self) -> None:
        rows = [{"payload": {}} for _ in range(MAX_EVENTS + 1)]
        with self.assertRaises(ValueError):
            validate_event_rows(rows)

    def test_requires_payload_on_row(self) -> None:
        with self.assertRaises(ValueError):
            validate_event_rows([{"tenant_id": "t"}])

    def test_body_requires_events_list(self) -> None:
        with self.assertRaises(ValueError):
            validate_ingest_body({"ingest_batch_id": "b"})

    def test_observables_require_keys(self) -> None:
        with self.assertRaises(ValueError):
            validate_observables([{"tool": "antigravity"}])

    def test_body_accepts_observables(self) -> None:
        validate_ingest_body(
            {
                "events": [{"payload": {}}],
                "observables": [
                    {
                        "dedup_key": "d1",
                        "tool": "antigravity",
                        "observable_type": "session_start",
                    }
                ],
            }
        )


if __name__ == "__main__":
    unittest.main()
