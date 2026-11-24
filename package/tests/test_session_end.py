"""Session-complete detection for live upload flush."""

from __future__ import annotations

import unittest

from core import RawEvent
from package.session_end import is_session_end


def _event(payload: dict) -> RawEvent:
    return RawEvent(
        timestamp="2026-10-09T12:00:00+00:00",
        collector_id="cursor.hooks",
        agent="cursor",
        raw_payload=payload,
    )


class SessionEndTests(unittest.TestCase):
    def test_cursor_stop(self) -> None:
        self.assertTrue(is_session_end(_event({"event_type": "stop"})))

    def test_cursor_session_end(self) -> None:
        self.assertTrue(is_session_end(_event({"event_type": "sessionEnd"})))

    def test_claude_session_end(self) -> None:
        self.assertTrue(is_session_end(_event({"hook_event_name": "SessionEnd"})))

    def test_antigravity_idle(self) -> None:
        self.assertTrue(is_session_end(_event({"fullyIdle": True})))

    def test_subagent_stop_is_not_session_end(self) -> None:
        self.assertFalse(is_session_end(_event({"event_type": "subagentStop"})))

    def test_prompt_is_not_session_end(self) -> None:
        self.assertFalse(is_session_end(_event({"event_type": "beforeSubmitPrompt"})))

    def test_dict_envelope(self) -> None:
        self.assertTrue(
            is_session_end({"raw_payload": {"hook_event_name": "Stop"}})
        )


if __name__ == "__main__":
    unittest.main()
