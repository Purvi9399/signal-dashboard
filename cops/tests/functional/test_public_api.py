"""Functional: public cops package import (FR12)."""

from __future__ import annotations

import unittest

import cops
from cops import ActionType, COPDecision, CopType, MetricEvent, MetricState


class PublicApiFunctionalTests(unittest.TestCase):
    def test_package_exports_contract_types(self) -> None:
        self.assertIs(cops.MetricEvent, MetricEvent)
        self.assertIs(cops.COPDecision, COPDecision)
        self.assertIs(cops.CopType, CopType)
        self.assertIs(cops.ActionType, ActionType)
        self.assertIs(cops.MetricState, MetricState)

    def test_operator_can_build_event_and_decision(self) -> None:
        event = MetricEvent(
            metric_id="metric_001",
            metric_name="sensitivity_tier",
            session_id="session_42",
            value=3,
            threshold=2,
            state=MetricState.WARNING,
        )
        decision = COPDecision(
            cop=CopType.CLASSIFICATION,
            session_id=event.session_id,
            interpretation="Sensitive tool use.",
            confidence=0.8,
            action=ActionType.NONE,
            action_reason="Tags only.",
            metric_id=event.metric_id,
            downstream_cops=[CopType.GOVERNANCE_INTELLIGENCE],
        )
        self.assertEqual(decision.action, ActionType.NONE)
        self.assertEqual(decision.cop, CopType.CLASSIFICATION)


if __name__ == "__main__":
    unittest.main()
