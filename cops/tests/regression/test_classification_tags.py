"""Regression: freeze Classification tag names (FR3–FR7)."""

from __future__ import annotations

import unittest

from cops.classification import FROZEN_TAGS, OPTIONAL_TAG_KEYS

FROZEN = ("sensitive", "config", "high-risk", "watch", "routine")
OPTIONAL = ("groups", "trigger_class", "solution_class")


class ClassificationTagRegressionTests(unittest.TestCase):
    def test_core_tags_frozen(self) -> None:
        self.assertEqual(FROZEN_TAGS, FROZEN)

    def test_optional_feature_keys_frozen(self) -> None:
        self.assertEqual(OPTIONAL_TAG_KEYS, OPTIONAL)


if __name__ == "__main__":
    unittest.main()
