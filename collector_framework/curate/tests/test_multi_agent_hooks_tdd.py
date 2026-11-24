"""Red-green TDD: multi-agent hooks RawEvent → curator tuples (Phase 1)."""

from __future__ import annotations

import unittest
from datetime import datetime

from curate.adapter_source_events import adapt_raw_event


def _env(agent: str, collector_id: str, raw: dict, ts: str = "2026-09-21T10:00:00+00:00"):
    return {
        "timestamp": ts,
        "collector_id": collector_id,
        "agent": agent,
        "raw_payload": raw,
    }


class ClaudeHooksAdapterTdd(unittest.TestCase):
    def test_pre_tool_use(self) -> None:
        triple = adapt_raw_event(
            _env(
                "claude-code",
                "claude_code.hooks",
                {
                    "hook_event_name": "PreToolUse",
                    "session_id": "sess-cc",
                    "tool_name": "Bash",
                    "tool_input": {"command": "ls"},
                    "cwd": "/tmp",
                },
            )
        )
        self.assertIsNotNone(triple)
        coll, fields, _ = triple
        self.assertEqual(coll, "local_hooks")
        self.assertEqual(fields["tool"], "claude-code")
        self.assertEqual(fields["observable_type"], "tool_request")
        self.assertEqual(fields["session_id"], "sess-cc")
        self.assertIsInstance(fields["occurred_at"], datetime)


class CursorHooksAdapterTdd(unittest.TestCase):
    def test_pre_tool_use_via_event_type(self) -> None:
        triple = adapt_raw_event(
            _env(
                "cursor",
                "cursor.hooks",
                {
                    "event_type": "preToolUse",
                    "conversation_id": "conv-1",
                    "tool_name": "Shell",
                    "tool_input": {"command": "pwd"},
                },
            )
        )
        self.assertIsNotNone(triple)
        _, fields, _ = triple
        self.assertEqual(fields["tool"], "cursor")
        self.assertEqual(fields["observable_type"], "tool_request")
        self.assertEqual(fields["session_id"], "conv-1")


class CodexHooksAdapterTdd(unittest.TestCase):
    def test_native_pre_tool_use(self) -> None:
        triple = adapt_raw_event(
            _env(
                "codex",
                "codex.hooks",
                {
                    "hook_event_name": "PreToolUse",
                    "session_id": "sess-cx",
                    "tool_name": "shell",
                    "tool_input": {"command": "echo hi"},
                },
            )
        )
        self.assertIsNotNone(triple)
        _, fields, _ = triple
        self.assertEqual(fields["tool"], "codex")
        self.assertEqual(fields["observable_type"], "tool_request")

    def test_ide_save_maps_file_modify(self) -> None:
        triple = adapt_raw_event(
            _env(
                "codex",
                "codex.local_hooks",
                {
                    "type": "onDidSaveTextDocument",
                    "timestamp": "2026-09-21T10:00:00+00:00",
                    "path": "/tmp/a.py",
                },
            )
        )
        self.assertIsNotNone(triple)
        _, fields, _ = triple
        self.assertEqual(fields["tool"], "codex")
        self.assertEqual(fields["observable_type"], "file_modify")
        self.assertEqual(fields["file_path"], "/tmp/a.py")


class CopilotHooksAdapterTdd(unittest.TestCase):
    def test_pascalcase_pre_tool_use(self) -> None:
        triple = adapt_raw_event(
            _env(
                "copilot",
                "copilot.hooks",
                {
                    "hook_event_name": "PreToolUse",
                    "session_id": "sess-cp",
                    "tool_name": "bash",
                    "tool_input": {"command": "true"},
                },
            )
        )
        self.assertIsNotNone(triple)
        _, fields, _ = triple
        self.assertEqual(fields["tool"], "copilot")
        self.assertEqual(fields["observable_type"], "tool_request")

    def test_camelcase_session_start(self) -> None:
        triple = adapt_raw_event(
            _env(
                "copilot",
                "copilot.hooks",
                {
                    "sessionId": "sess-cp2",
                    "initialPrompt": "hello",
                },
            )
        )
        self.assertIsNotNone(triple)
        _, fields, _ = triple
        self.assertEqual(fields["tool"], "copilot")
        self.assertEqual(fields["observable_type"], "session_start")


class AntigravityRegressionTdd(unittest.TestCase):
    def test_still_maps_write(self) -> None:
        triple = adapt_raw_event(
            _env(
                "antigravity",
                "antigravity.hooks",
                {
                    "conversationId": "agy-1",
                    "toolCall": {
                        "name": "write_to_file",
                        "args": {"TargetFile": "/t.py", "Overwrite": True},
                    },
                },
            )
        )
        self.assertIsNotNone(triple)
        _, fields, _ = triple
        self.assertEqual(fields["tool"], "antigravity")
        self.assertIn(fields["observable_type"], ("file_create", "file_modify", "tool_request"))


if __name__ == "__main__":
    unittest.main()
