"""Unit tests: RawEvent / source_events -> curator loaded tuples (antigravity)."""

from __future__ import annotations

import unittest
from datetime import datetime

from curate.adapter_source_events import (
    adapt_raw_event,
    adapt_source_event_row,
    load_jsonl,
)


class AdaptAntigravityHooksTests(unittest.TestCase):
    def test_write_to_file_maps_file_fields(self) -> None:
        event = {
            "timestamp": "2026-08-26T15:22:35.650686+00:00",
            "collector_id": "antigravity.hooks",
            "agent": "antigravity",
            "raw_payload": {
                "conversationId": "f4ec000b-f1fe-451e-a3d4-a9a84e6d1af1",
                "modelName": "gemini-3.7-flash-high",
                "toolCall": {
                    "name": "write_to_file",
                    "args": {
                        "Overwrite": True,
                        "TargetFile": "/tmp/hello_live_test.py",
                        "toolAction": "Creating file",
                    },
                },
                "workspacePaths": ["/tmp/ws"],
            },
        }
        triple = adapt_raw_event(event)
        self.assertIsNotNone(triple)
        coll, fields, raw = triple
        self.assertEqual(coll, "local_hooks")
        self.assertEqual(fields["tool"], "antigravity")
        self.assertEqual(fields["session_id"], "f4ec000b-f1fe-451e-a3d4-a9a84e6d1af1")
        self.assertEqual(fields["conversation_id"], "f4ec000b-f1fe-451e-a3d4-a9a84e6d1af1")
        self.assertEqual(fields["model"], "gemini-3.7-flash-high")
        self.assertEqual(fields["tool_name"], "write_to_file")
        self.assertEqual(fields["file_path"], "/tmp/hello_live_test.py")
        self.assertIn(fields["observable_type"], ("file_create", "file_modify", "tool_request"))
        self.assertNotEqual(fields["observable_type"], "other")
        self.assertIsInstance(fields["occurred_at"], datetime)
        self.assertIs(raw, event["raw_payload"])

    def test_run_command_is_tool_request(self) -> None:
        event = {
            "timestamp": "2026-08-26T15:22:40+00:00",
            "collector_id": "antigravity.hooks",
            "agent": "antigravity",
            "raw_payload": {
                "conversationId": "sess-1",
                "modelName": "gemini-3.7-flash-high",
                "toolCall": {
                    "name": "run_command",
                    "args": {"CommandLine": "python hello.py"},
                },
            },
        }
        triple = adapt_raw_event(event)
        self.assertIsNotNone(triple)
        _, fields, _ = triple
        self.assertEqual(fields["observable_type"], "tool_request")
        self.assertEqual(fields["tool_name"], "run_command")
        self.assertEqual(fields["command"], "python hello.py")

    def test_session_start_from_initial_steps(self) -> None:
        event = {
            "timestamp": "2026-08-26T15:22:28+00:00",
            "collector_id": "antigravity.hooks",
            "agent": "antigravity",
            "raw_payload": {
                "conversationId": "sess-2",
                "initialNumSteps": 1,
                "modelName": "gemini-3.7-flash-high",
                "workspacePaths": ["/tmp/ws"],
            },
        }
        triple = adapt_raw_event(event)
        self.assertIsNotNone(triple)
        _, fields, _ = triple
        self.assertEqual(fields["observable_type"], "session_start")
        self.assertEqual(fields["workspace"], "/tmp/ws")

    def test_skips_untyped_payload(self) -> None:
        event = {
            "timestamp": "2026-08-26T15:22:28+00:00",
            "collector_id": "antigravity.hooks",
            "agent": "antigravity",
            "raw_payload": {"conversationId": "x", "noise": True},
        }
        self.assertIsNone(adapt_raw_event(event))

    def test_source_events_row_uses_payload_envelope(self) -> None:
        row = {
            "tool": "antigravity",
            "collector": "local_hooks",
            "session_id": "sess-3",
            "payload": {
                "timestamp": "2026-08-26T15:22:35+00:00",
                "collector_id": "antigravity.hooks",
                "agent": "antigravity",
                "raw_payload": {
                    "conversationId": "sess-3",
                    "toolCall": {
                        "name": "write_to_file",
                        "args": {"TargetFile": "/a.py", "Overwrite": False},
                    },
                },
            },
        }
        triple = adapt_source_event_row(row)
        self.assertIsNotNone(triple)
        coll, fields, _ = triple
        self.assertEqual(coll, "local_hooks")
        self.assertEqual(fields["tool"], "antigravity")
        self.assertNotEqual(fields["observable_type"], "other")


class LoadJsonlTests(unittest.TestCase):
    def test_load_jsonl_collects_tuples(self) -> None:
        import tempfile
        from pathlib import Path

        lines = [
            {
                "timestamp": "2026-08-26T15:22:28+00:00",
                "collector_id": "antigravity.hooks",
                "agent": "antigravity",
                "raw_payload": {
                    "conversationId": "s",
                    "initialNumSteps": 1,
                    "modelName": "m",
                },
            },
            {
                "timestamp": "2026-08-26T15:22:29+00:00",
                "collector_id": "antigravity.hooks",
                "agent": "antigravity",
                "raw_payload": {"conversationId": "s"},
            },
        ]
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "sample.jsonl"
            path.write_text("\n".join(__import__("json").dumps(x) for x in lines) + "\n")
            loaded = load_jsonl(path)
        self.assertEqual(len(loaded), 1)
        self.assertEqual(loaded[0][1]["observable_type"], "session_start")


class AdaptCursorStringToolInputTests(unittest.TestCase):
    def test_string_tool_input_does_not_crash(self) -> None:
        event = {
            "timestamp": "2026-10-09T10:55:00+00:00",
            "collector_id": "cursor.hooks",
            "agent": "cursor",
            "raw_payload": {
                "event_type": "preToolUse",
                "conversation_id": "sess-str",
                "tool_name": "Shell",
                "tool_input": "echo hello",
            },
        }
        triple = adapt_raw_event(event)
        self.assertIsNotNone(triple)
        _, fields, _ = triple
        self.assertEqual(fields["tool"], "cursor")
        self.assertEqual(fields["tool_arguments"]["raw"], "echo hello")


if __name__ == "__main__":
    unittest.main()

