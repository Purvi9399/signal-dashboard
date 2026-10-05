"""Functional: ClassificationCOP public evaluate path (FR1, FR9, FR12)."""

from __future__ import annotations

import unittest

from cops import ActionType, ClassificationCOP, CopType, MetricEvent, MetricState


class ClassificationFunctionalTests(unittest.TestCase):
    def test_package_exports_classification_cop(self) -> None:
        event = MetricEvent(
            metric_id="metric_cls_3",
            metric_name="is_config_file",
            session_id="session_42",
            value=True,
            threshold=False,
            state=MetricState.NORMAL,
            features={
                "sensitivity_tier": 1,
                "is_config_file": True,
                "risk_markers": None,
                "groups": "auth",
            },
        )
        decision = ClassificationCOP().evaluate(event)
        self.assertEqual(decision.cop, CopType.CLASSIFICATION)
        self.assertEqual(decision.action, ActionType.NONE)
        self.assertIn("config", decision.context_used["tags"])
        self.assertIn("groups:auth", decision.context_used["tags"])
        self.assertFalse(decision.failed)


if __name__ == "__main__":
    unittest.main()
