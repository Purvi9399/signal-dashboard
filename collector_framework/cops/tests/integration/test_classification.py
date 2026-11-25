"""Integration: Classification output is valid Incident input (FR8, FR10)."""

from __future__ import annotations

import unittest

from cops.classification import ClassificationCOP
from cops.contracts import (
    ActionType,
    CopType,
    MetricEvent,
    MetricState,
    is_incident_input,
)


class ClassificationIntegrationTests(unittest.TestCase):
    def test_decisions_are_incident_input(self) -> None:
        cop = ClassificationCOP()
        event = MetricEvent(
            metric_id="metric_cls_2",
            metric_name="risk_markers",
            session_id="session_42",
            value=["sudo "],
            threshold=None,
            state=MetricState.WARNING,
            features={
                "sensitivity_tier": 2,
                "is_config_file": False,
                "risk_markers": ["sudo "],
            },
        )
        decision = cop.evaluate(event)
        self.assertTrue(is_incident_input([decision]))
        self.assertEqual(decision.action, ActionType.NONE)
        self.assertEqual(
            decision.downstream_cops,
            [CopType.GOVERNANCE_INTELLIGENCE],
        )


if __name__ == "__main__":
    unittest.main()
