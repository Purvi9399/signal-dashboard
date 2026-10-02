"""Red → green: live CLI wrapper (LIVE_CLI.md)."""

from __future__ import annotations

import json
import sys
import tempfile
import threading
import time
from pathlib import Path
from unittest import TestCase

from collectors.antigravity.antigravity_cli_wrapper import AntigravityCLIWrapperCollector
from collectors.claude_code.claude_code_cli_wrapper import ClaudeCodeCLIWrapperCollector
from collectors.codex.codex_cli_wrapper import CodexCLIWrapperCollector
from collectors.copilot.copilot_cli_wrapper import CopilotCLIWrapperCollector
from collectors.cursor.cursor_cli_wrapper import CursorCLIWrapperCollector
from collectors.live_cli import build_chunk

_MARKER = "WRAP-OK"
_PRINT = [
    sys.executable,
    "-c",
    "import sys; sys.stdout.write('WRAP-OK\\n'); sys.stdout.flush()",
]


def _drain(collector, bucket: list) -> None:
    for ev in collector.capture():
        bucket.append(ev)


def _joined(events) -> str:
    return "".join((ev.raw_payload.get("content") or "") for ev in events)


class BuildChunkTests(TestCase):
    def test_shape(self) -> None:
        rec = build_chunk("stdout", "hi", "python -c x")
        self.assertEqual(rec["stream"], "stdout")
        self.assertEqual(rec["content"], "hi")
        self.assertEqual(rec["wrapped_command"], "python -c x")


class ReplayCliTests(TestCase):
    def test_claude_replay(self) -> None:
        chunk = {"stream": "stdout", "content": "hello", "wrapped_command": "claude"}
        c = ClaudeCodeCLIWrapperCollector(replay_events=[chunk])
        c.activate()
        events = list(c.capture())
        c.deactivate()
        self.assertEqual(events[0].agent, "claude_code")
        self.assertEqual(events[0].raw_payload, chunk)

    def test_antigravity_replay_path(self) -> None:
        path = Path(tempfile.mkdtemp()) / "wrap.jsonl"
        rec = {"stream": "stdout", "content": "init", "wrapped_command": "agy"}
        path.write_text(json.dumps(rec) + "\n", encoding="utf-8")
        c = AntigravityCLIWrapperCollector(replay_path=path)
        c.activate()
        events = list(c.capture())
        c.deactivate()
        self.assertEqual(events[0].agent, "antigravity")
        self.assertEqual(events[0].raw_payload["content"], "init")


class LivePtyCliTdd(TestCase):
    def test_copilot_python_c(self) -> None:
        c = CopilotCLIWrapperCollector(command=_PRINT, replay_events=None)
        c.activate()
        events = list(c.capture())
        c.deactivate()
        self.assertEqual(events[0].agent, "copilot")
        self.assertIn(_MARKER, _joined(events))

    def test_codex_python_c(self) -> None:
        c = CodexCLIWrapperCollector(replay_events=None, command=_PRINT)
        c.activate()
        events = list(c.capture())
        c.deactivate()
        self.assertEqual(events[0].agent, "codex")
        self.assertIn(_MARKER, _joined(events))


class LiveCursorCliTdd(TestCase):
    def test_explicit_command_captures(self) -> None:
        c = CursorCLIWrapperCollector(replay_events=None, command=_PRINT)
        c.activate()
        events = list(c.capture())
        c.deactivate()
        self.assertEqual(events[0].agent, "cursor")
        self.assertIn(_MARKER, _joined(events))

    def test_no_command_does_not_spawn(self) -> None:
        c = CursorCLIWrapperCollector(replay_events=None)
        c.activate()
        got: list = []
        thread = threading.Thread(target=_drain, args=(c, got), daemon=True)
        thread.start()
        time.sleep(0.15)
        c.deactivate()
        thread.join(timeout=5)
        self.assertEqual(got, [])
