"""Unit tests: observables validation + ingest body (TDD contract)."""

from __future__ import annotations

import unittest

from export.validate_ingest import (
    MAX_OBSERVABLES,
    validate_ingest_body,
    validate_observables,
)


class ValidateObservablesTests(unittest.TestCase):
    def test_none_ok(self) -> None:
        validate_observables(None)

    def test_empty_ok(self) -> None:
        validate_observables([])

    def test_rejects_over_max(self) -> None:
        rows = [
            {
                "dedup_key": f"d{i}",
                "tool": "antigravity",
                "observable_type": "tool_request",
            }
            for i in range(MAX_OBSERVABLES + 1)
        ]
        with self.assertRaises(ValueError) as ctx:
            validate_observables(rows)
        self.assertIn("observables exceed max", str(ctx.exception))

    def test_requires_dedup_key(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            validate_observables(
                [{"tool": "antigravity", "observable_type": "session_start"}]
            )
        self.assertIn("dedup_key", str(ctx.exception))

    def test_requires_observable_type(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            validate_observables([{"dedup_key": "d1", "tool": "antigravity"}])
        self.assertIn("observable_type", str(ctx.exception))

    def test_requires_tool(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            validate_observables(
                [{"dedup_key": "d1", "observable_type": "session_start"}]
            )
        self.assertIn("tool", str(ctx.exception))

    def test_body_rejects_non_list_observables(self) -> None:
        with self.assertRaises(ValueError):
            validate_ingest_body({"events": [], "observables": {"x": 1}})

    def test_body_accepts_events_and_observables(self) -> None:
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
