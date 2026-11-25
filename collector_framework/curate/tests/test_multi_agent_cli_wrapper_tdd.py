"""Red-green TDD: multi-agent CLI Wrapper RawEvent → curator tuples (Phase 5)."""

from __future__ import annotations

import unittest
from datetime import datetime

from curate.adapter_source_events import adapt_raw_event


def _env(agent: str, collector_id: str, raw: dict, ts: str = "2026-09-21T14:00:00+00:00"):
    return {
        "timestamp": ts,
        "collector_id": collector_id,
        "agent": agent,
        "raw_payload": raw,
    }


class CliStdinUserPromptTdd(unittest.TestCase):
    def test_copilot_stdin_chunk(self) -> None:
        triple = adapt_raw_event(
            _env(
                "copilot",
                "copilot.cli_wrapper",
                {
                    "timestamp": "2026-09-21T14:00:00+00:00",
                    "stream": "stdin",
                    "content": "list files\n",
                    "wrapped_command": "copilot -p hello",
                },
            )
        )
        self.assertIsNotNone(triple)
        coll, fields, _ = triple
        self.assertEqual(coll, "cli_wrapper")
        self.assertEqual(fields["tool"], "copilot")
        self.assertEqual(fields["observable_type"], "user_prompt")
        self.assertEqual(fields["command"], "list files\n")
        self.assertIsInstance(fields["occurred_at"], datetime)

    def test_claude_stdin_with_operator(self) -> None:
        triple = adapt_raw_event(
            _env(
                "claude_code",
                "claude_code.cli_wrapper",
                {
                    "stream": "stdin",
                    "content": "/help",
                    "wrapped_command": "claude",
                    "operator_username": "malhar",
                    "hostname": "mbp",
                },
            )
        )
        self.assertIsNotNone(triple)
        _, fields, _ = triple
        self.assertEqual(fields["tool"], "claude-code")
        self.assertEqual(fields["observable_type"], "user_prompt")
        self.assertEqual(fields["operator_username"], "malhar")


class CliStallAndSessionTdd(unittest.TestCase):
    def test_hung_seconds_is_shell_stall(self) -> None:
        triple = adapt_raw_event(
            _env(
                "codex",
                "codex.cli_wrapper",
                {
                    "wrapped_command": "codex exec",
                    "hung_seconds": 45,
                },
            )
        )
        self.assertIsNotNone(triple)
        _, fields, _ = triple
        self.assertEqual(fields["tool"], "codex")
        self.assertEqual(fields["observable_type"], "shell_stall")
        self.assertEqual(fields["hung_seconds"], 45)

    def test_explicit_session_start(self) -> None:
        triple = adapt_raw_event(
            _env(
                "antigravity",
                "antigravity.cli_wrapper",
                {
                    "interaction_type": "session_start",
                    "wrapped_command": "agy --output-format stream-json",
                },
            )
        )
        self.assertIsNotNone(triple)
        _, fields, _ = triple
        self.assertEqual(fields["tool"], "antigravity")
        self.assertEqual(fields["observable_type"], "session_start")
        self.assertEqual(fields["command"], "agy --output-format stream-json")

    def test_cursor_session_marker_without_stream(self) -> None:
        triple = adapt_raw_event(
            _env(
                "cursor",
                "cursor.cli_wrapper",
                {
                    "wrapped_command": "cursor",
                    "operator_username": "dev",
                    "hostname": "host1",
                },
            )
        )
        self.assertIsNotNone(triple)
        _, fields, _ = triple
        self.assertEqual(fields["tool"], "cursor")
        self.assertEqual(fields["observable_type"], "session_start")


class CliSkipNoiseTdd(unittest.TestCase):
    def test_stdout_chunk_skipped(self) -> None:
        triple = adapt_raw_event(
            _env(
                "copilot",
                "copilot.cli_wrapper",
                {
                    "stream": "stdout",
                    "content": "Listing",
                    "wrapped_command": "copilot -p x",
                },
            )
        )
        self.assertIsNone(triple)

    def test_empty_stdin_skipped(self) -> None:
        triple = adapt_raw_event(
            _env(
                "codex",
                "codex.cli_wrapper",
                {"stream": "stdin", "content": "", "wrapped_command": "codex"},
            )
        )
        self.assertIsNone(triple)


if __name__ == "__main__":
    unittest.main()
