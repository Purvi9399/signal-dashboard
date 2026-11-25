"""Unit tests for observables-only sink (mocked HTTP)."""

from __future__ import annotations

import unittest
from unittest import mock

from curate import observables_sink as sink


class UpsertObservablesTests(unittest.TestCase):
    @mock.patch.object(sink, "_request")
    def test_upsert_posts_observables_only(self, req: mock.Mock) -> None:
        req.return_value = None
        records = [
            {
                "observable_id": "a",
                "dedup_key": "d1",
                "tool": "antigravity",
                "observable_type": "tool_request",
            }
        ]
        n = sink.upsert_observables(records, key="secret")
        self.assertEqual(n, 1)
        self.assertEqual(req.call_count, 1)
        args, kwargs = req.call_args
        self.assertEqual(args[0], "POST")
        self.assertEqual(args[1], "observables")
        self.assertEqual(kwargs["query"]["on_conflict"], "dedup_key")
        self.assertIn("merge-duplicates", kwargs["prefer"])

    @mock.patch.object(sink, "_request")
    def test_fetch_pages_until_short(self, req: mock.Mock) -> None:
        req.side_effect = [
            [{"payload": {"a": 1}}, {"payload": {"b": 2}}],
            [{"payload": {"c": 3}}],
        ]
        rows = sink.fetch_source_events_by_batch("batch-1", key="secret", page_size=2)
        self.assertEqual(len(rows), 3)
        self.assertEqual(req.call_count, 2)


if __name__ == "__main__":
    unittest.main()
