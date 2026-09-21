"""Red-green TDD: multi-agent FS Watcher RawEvent → curator tuples (Phase 3)."""

from __future__ import annotations

import unittest
from datetime import datetime

from curate.adapter_source_events import adapt_raw_event


def _env(agent: str, collector_id: str, raw: dict, ts: str = "2026-09-21T12:00:00+00:00"):
    return {
        "timestamp": ts,
        "collector_id": collector_id,
        "agent": agent,
        "raw_payload": raw,
    }


def _fs(event_type: str, path: str, **extra) -> dict:
    record = {
        "timestamp": "2026-09-21T12:00:00+00:00",
        "event_type": event_type,
        "path": path,
        "is_directory": False,
        "size_bytes": 42,
    }
    record.update(extra)
    return record


class FsCreateModifyDeleteTdd(unittest.TestCase):
    def test_antigravity_created(self) -> None:
        triple = adapt_raw_event(
            _env(
                "antigravity",
                "antigravity.fs_watcher",
                _fs("created", "/tmp/agy/brain/a.py"),
            )
        )
        self.assertIsNotNone(triple)
        coll, fields, _ = triple
        self.assertEqual(coll, "fs_watcher")
        self.assertEqual(fields["tool"], "antigravity")
        self.assertEqual(fields["observable_type"], "file_create")
        self.assertEqual(fields["file_path"], "/tmp/agy/brain/a.py")
        self.assertEqual(fields["file_extension"], ".py")
        self.assertIsInstance(fields["occurred_at"], datetime)

    def test_cursor_modified(self) -> None:
        triple = adapt_raw_event(
            _env(
                "cursor",
                "cursor.fs_watcher",
                _fs("modified", "/proj/src/main.ts", size_bytes=100),
            )
        )
        self.assertIsNotNone(triple)
        _, fields, _ = triple
        self.assertEqual(fields["tool"], "cursor")
        self.assertEqual(fields["observable_type"], "file_modify")
        self.assertEqual(fields["file_size"], 100)

    def test_copilot_deleted(self) -> None:
        triple = adapt_raw_event(
            _env(
                "copilot",
                "copilot.fs_watcher",
                _fs("deleted", "/tmp/out.txt", size_bytes=None),
            )
        )
        self.assertIsNotNone(triple)
        _, fields, _ = triple
        self.assertEqual(fields["tool"], "copilot")
        self.assertEqual(fields["observable_type"], "file_delete")


class FsCodexSizeAndMovedTdd(unittest.TestCase):
    def test_codex_uses_size_field(self) -> None:
        triple = adapt_raw_event(
            _env(
                "codex",
                "codex.fs_watcher",
                {
                    "timestamp": "2026-09-21T12:00:00+00:00",
                    "event_type": "created",
                    "path": "/ws/foo.py",
                    "is_directory": False,
                    "size": 17,
                    "watch_root": "/ws",
                },
            )
        )
        self.assertIsNotNone(triple)
        _, fields, _ = triple
        self.assertEqual(fields["tool"], "codex")
        self.assertEqual(fields["observable_type"], "file_create")
        self.assertEqual(fields["file_size"], 17)

    def test_moved_maps_file_modify(self) -> None:
        triple = adapt_raw_event(
            _env(
                "codex",
                "codex.fs_watcher",
                {
                    "event_type": "moved_to",
                    "path": "/ws/b.py",
                    "is_directory": False,
                    "size": 3,
                },
            )
        )
        self.assertIsNotNone(triple)
        _, fields, _ = triple
        self.assertEqual(fields["observable_type"], "file_modify")


class FsTranscriptTdd(unittest.TestCase):
    def test_transcript_jsonl_modified(self) -> None:
        triple = adapt_raw_event(
            _env(
                "antigravity",
                "antigravity.fs_watcher",
                _fs(
                    "modified",
                    "/brain/conv/.system_generated/logs/transcript.jsonl",
                    size_bytes=999,
                ),
            )
        )
        self.assertIsNotNone(triple)
        _, fields, _ = triple
        self.assertEqual(fields["observable_type"], "transcript_appended")

    def test_claude_transcript_line(self) -> None:
        triple = adapt_raw_event(
            _env(
                "claude_code",
                "claude_code.fs_watcher",
                {
                    "type": "assistant",
                    "message": {
                        "model": "claude-sonnet-4",
                        "usage": {
                            "input_tokens": 10,
                            "output_tokens": 20,
                            "cache_read_input_tokens": 5,
                        },
                        "content": [{"type": "thinking", "thinking": "hmm"}],
                    },
                },
            )
        )
        self.assertIsNotNone(triple)
        coll, fields, _ = triple
        self.assertEqual(coll, "fs_watcher")
        self.assertEqual(fields["tool"], "claude-code")
        self.assertEqual(fields["observable_type"], "transcript_appended")
        self.assertEqual(fields["input_tokens"], 10)
        self.assertEqual(fields["output_tokens"], 20)
        self.assertEqual(fields["cache_read_tokens"], 5)


class FsSkipNoiseTdd(unittest.TestCase):
    def test_unknown_event_skipped(self) -> None:
        triple = adapt_raw_event(
            _env(
                "copilot",
                "copilot.fs_watcher",
                _fs("chmod", "/tmp/x"),
            )
        )
        self.assertIsNone(triple)

    def test_missing_path_skipped(self) -> None:
        triple = adapt_raw_event(
            _env(
                "cursor",
                "cursor.fs_watcher",
                {"event_type": "created", "is_directory": False},
            )
        )
        self.assertIsNone(triple)


if __name__ == "__main__":
    unittest.main()
