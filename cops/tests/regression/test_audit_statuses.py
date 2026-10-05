"""Regression: freeze Audit status names (FR3–FR6)."""

from __future__ import annotations

import unittest

from cops.audit import FROZEN_STATUSES

FROZEN = ("incomplete", "disputed", "untraceable", "attributable")


class AuditStatusRegressionTests(unittest.TestCase):
    def test_statuses_frozen(self) -> None:
        self.assertEqual(FROZEN_STATUSES, FROZEN)


if __name__ == "__main__":
    unittest.main()
