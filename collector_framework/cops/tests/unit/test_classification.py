"""Unit tests for Classification tag rules (FR3–FR8, FR13, FR14)."""

from __future__ import annotations

import unittest

from cops.classification import ClassificationCOP
from cops.contracts import ActionType, CopType, MetricEvent, MetricState


def _event(features: dict, state: MetricState = MetricState.NORMAL) -> MetricEvent:
    return MetricEvent(
        metric_id="metric_cls_1",
        metric_name="sensitivity_tier",
        session_id="session_42",
        value=features.get("sensitivity_tier"),
        threshold=3,
        state=state,
        features=features,
        evidence_refs=["evt_1"],
    )


class ClassificationUnitTests(unittest.TestCase):
    def setUp(self) -> None:
        self.cop = ClassificationCOP()

    def test_routine_when_no_risk_features(self) -> None:
        decision = self.cop.evaluate(
            _event(
                {
                    "sensitivity_tier": 1,
                    "is_config_file": False,
                    "risk_markers": None,
                }
            )
        )
        self.assertEqual(decision.action, ActionType.NONE)
        self.assertEqual(decision.cop, CopType.CLASSIFICATION)
        self.assertEqual(decision.context_used["tags"], ["routine"])
        self.assertEqual(decision.downstream_cops, [])
        self.assertEqual(decision.confidence, 0.95)

    def test_sensitive_watch_when_tier_at_least_three(self) -> None:
        decision = self.cop.evaluate(
            _event(
                {
                    "sensitivity_tier": 3,
                    "is_config_file": False,
                    "risk_markers": None,
                }
            )
        )
        self.assertIn("sensitive", decision.context_used["tags"])
        self.assertIn("watch", decision.context_used["tags"])
        self.assertEqual(
            decision.downstream_cops,
            [CopType.GOVERNANCE_INTELLIGENCE],
        )

    def test_config_and_high_risk_tags(self) -> None:
        decision = self.cop.evaluate(
            _event(
                {
                    "sensitivity_tier": 1,
                    "is_config_file": True,
                    "risk_markers": ["rm -rf"],
                    "tool_category": "shell",
                }
            )
        )
        tags = decision.context_used["tags"]
        self.assertIn("config", tags)
        self.assertIn("high-risk", tags)
        self.assertIn("watch", tags)
        self.assertEqual(decision.context_used["tool_category"], "shell")

    def test_unknown_state_lowers_confidence(self) -> None:
        decision = self.cop.evaluate(
            _event(
                {
                    "sensitivity_tier": 1,
                    "is_config_file": False,
                    "risk_markers": None,
                },
                state=MetricState.UNKNOWN,
            )
        )
        self.assertEqual(decision.confidence, 0.30)
        self.assertEqual(decision.action, ActionType.NONE)

    def test_missing_feature_keys_medium_confidence(self) -> None:
        decision = self.cop.evaluate(_event({}))
        self.assertEqual(decision.confidence, 0.60)


if __name__ == "__main__":
    unittest.main()
