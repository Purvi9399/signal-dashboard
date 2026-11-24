"""Regression: freeze Context and Memory status names (FR3–FR8)."""

from __future__ import annotations

import unittest

from cops.context_memory import FROZEN_STATUSES

FROZEN = (
    "incomplete",
    "context_loss",
    "contradictory",
    "stale",
    "precedent_found",
    "context_ok",
)


class ContextMemoryStatusRegressionTests(unittest.TestCase):
    # Status strings must stay stable for downstream COPS and dashboards.
    def test_statuses_frozen(self) -> None:
        self.assertEqual(FROZEN_STATUSES, FROZEN)


if __name__ == "__main__":
    unittest.main()
