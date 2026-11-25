"""TDD: batch rows for PostgREST must share identical keys (PGRST102)."""

from __future__ import annotations

import unittest

from export.observable_rows import align_observable_keys


class AlignObservableKeysTdd(unittest.TestCase):
    def test_aligns_missing_keys_to_none(self) -> None:
        rows = [
            {"dedup_key": "a", "tool": "antigravity", "file_path": "/x"},
            {"dedup_key": "b", "tool": "antigravity", "command": "ls"},
        ]
        aligned = align_observable_keys(rows)
        self.assertEqual(set(aligned[0].keys()), set(aligned[1].keys()))
        self.assertIsNone(aligned[0]["command"])
        self.assertIsNone(aligned[1]["file_path"])
        self.assertEqual(aligned[0]["file_path"], "/x")
        self.assertEqual(aligned[1]["command"], "ls")

    def test_empty_ok(self) -> None:
        self.assertEqual(align_observable_keys([]), [])


if __name__ == "__main__":
    unittest.main()
