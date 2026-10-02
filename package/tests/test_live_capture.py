"""Unit tests for live capture drain helper."""

from __future__ import annotations

import unittest

from package.live_capture import drain_until_stop


class _FakeRegistry:
    def __init__(self) -> None:
        self.deactivated = 0

    def merged_capture(self):
        yield "a"
        yield "b"

    def deactivate_all(self) -> None:
        self.deactivated += 1


class DrainUntilStopTests(unittest.TestCase):
    def test_collects_finite_stream(self) -> None:
        registry = _FakeRegistry()
        events = drain_until_stop(registry, listen_seconds=0)
        self.assertEqual(events, ["a", "b"])
        self.assertGreaterEqual(registry.deactivated, 1)


if __name__ == "__main__":
    unittest.main()
