"""Unit tests for live export helpers (red → green)."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from core.interfaces import RawEvent
from export.upload_events import (
    check_upload_requires_live,
    raw_event_to_dict,
    raw_events_to_dicts,
    write_jsonl,
)


class RawEventDictTests(unittest.TestCase):
    def test_raw_event_to_dict(self) -> None:
        ev = RawEvent(
            timestamp="2026-09-10T09:47:22.490610+00:00",
            collector_id="antigravity.hooks",
            agent="antigravity",
            raw_payload={"conversationId": "abc"},
        )
        d = raw_event_to_dict(ev)
        self.assertEqual(d["collector_id"], "antigravity.hooks")
        self.assertEqual(d["agent"], "antigravity")
        self.assertEqual(d["raw_payload"]["conversationId"], "abc")

    def test_raw_events_to_dicts(self) -> None:
        events = [
            RawEvent("t1", "a.hooks", "antigravity", {"n": 1}),
            RawEvent("t2", "a.hooks", "antigravity", {"n": 2}),
        ]
        dicts = raw_events_to_dicts(events)
        self.assertEqual(len(dicts), 2)
        self.assertEqual(dicts[1]["raw_payload"]["n"], 2)


class WriteJsonlTests(unittest.TestCase):
    def test_round_trip(self) -> None:
        rows = [
            {
                "timestamp": "t1",
                "collector_id": "x.hooks",
                "agent": "cursor",
                "raw_payload": {},
            }
        ]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "out.jsonl"
            write_jsonl(path, rows)
            lines = path.read_text(encoding="utf-8").strip().splitlines()
            self.assertEqual(len(lines), 1)
            self.assertEqual(json.loads(lines[0])["agent"], "cursor")


class UploadRequiresLiveTests(unittest.TestCase):
    def test_upload_without_live_raises(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            check_upload_requires_live(live=False, upload=True)
        self.assertIn("--live", str(ctx.exception))

    def test_upload_with_live_ok(self) -> None:
        check_upload_requires_live(live=True, upload=True)

    def test_no_upload_ok(self) -> None:
        check_upload_requires_live(live=False, upload=False)


if __name__ == "__main__":
    unittest.main()
