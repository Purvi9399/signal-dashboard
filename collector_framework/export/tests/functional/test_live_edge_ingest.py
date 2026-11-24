"""Functional test against live signal-ingest (skips without secrets)."""

from __future__ import annotations

import os
import unittest
from pathlib import Path

from export.upload_jsonl import upload_jsonl

SAMPLE = (
    Path(__file__).resolve().parents[4]
    / "live_tests"
    / "antigravity_hooks_live"
    / "session_10prompts_20260910_151413"
    / "live_capture_output.jsonl"
)


def _edge_ready() -> bool:
    return bool(
        os.environ.get("SIGNAL_UPLOAD_SECRET")
        and os.environ.get("SIGNAL_INGEST_URL")
        and os.environ.get("SIGNAL_DEVICE_ID")
        and os.environ.get("SIGNAL_UPLOAD_MODE", "").lower() == "edge"
    )


@unittest.skipUnless(_edge_ready(), "set SIGNAL_UPLOAD_MODE=edge and ingest secrets")
class LiveEdgeIngestFunctionalTests(unittest.TestCase):
    def test_upload_one_event(self) -> None:
        self.assertTrue(SAMPLE.is_file(), f"missing sample {SAMPLE}")
        result = upload_jsonl(SAMPLE, dry_run=False, limit=1)
        self.assertGreaterEqual(result["inserted"], 1)
        self.assertTrue(result["ingest_batch_id"])


if __name__ == "__main__":
    unittest.main()
