"""Unit tests for live capture drain helper."""

from __future__ import annotations

import threading
import unittest

from core import RawEvent
from package.live_capture import drain_until_stop


def _raw(name: str) -> RawEvent:
    return RawEvent(
        timestamp="2026-10-09T12:00:00+00:00",
        collector_id="cursor.hooks",
        agent="cursor",
        raw_payload={"event_type": name, "conversationId": "c1"},
    )


class _ListCollector:
    def __init__(self, collector_id: str, events) -> None:
        self.collector_id = collector_id
        self._events = events
        self._idle = threading.Event()

    def capture(self):
        yield from self._events
        self._idle.wait(timeout=2)

    def deactivate(self) -> None:
        self._idle.set()


class _FakeRegistry:
    def __init__(self, events=None) -> None:
        self.deactivated = 0
        self._collectors = [_ListCollector("fake", events if events is not None else ["a", "b"])]

    def iter_active(self):
        return list(self._collectors)

    def deactivate_all(self) -> None:
        self.deactivated += 1
        for collector in self._collectors:
            collector.deactivate()


class DrainUntilStopTests(unittest.TestCase):
    def test_collects_finite_stream(self) -> None:
        registry = _FakeRegistry()
        events = drain_until_stop(registry, listen_seconds=0.2)
        self.assertEqual(events, ["a", "b"])
        self.assertGreaterEqual(registry.deactivated, 1)

    def test_flushes_on_session_end_and_leftover(self) -> None:
        first = _raw("beforeSubmitPrompt")
        end = _raw("stop")
        leftover = _raw("beforeSubmitPrompt")
        registry = _FakeRegistry([first, end, leftover])
        flushed = []
        events = drain_until_stop(
            registry, listen_seconds=0.2, on_session=flushed.append
        )
        self.assertEqual(events, [first, end, leftover])
        self.assertEqual(flushed, [[first, end], [leftover]])

    def test_does_not_starve_later_collector(self) -> None:
        claude = _ListCollector("claude_code.hooks", [])
        cursor = _ListCollector("cursor.hooks", ["cursor-ev"])
        registry = _FakeRegistry()
        registry._collectors = [claude, cursor]
        events = drain_until_stop(registry, listen_seconds=0.3)
        self.assertIn("cursor-ev", events)


if __name__ == "__main__":
    unittest.main()
