"""Integration: Incident Response consumes COPDecision objects (FR11)."""

from __future__ import annotations

import unittest

from cops.contracts import (
    ActionType,
    COPDecision,
    CopType,
    MetricEvent,
    MetricState,
    is_incident_input,
)


def _decision(cop: CopType, action: ActionType) -> COPDecision:
    return COPDecision(
        cop=cop,
        session_id="session_42",
        interpretation="unit path",
        confidence=0.9,
        action=action,
        action_reason="integration fixture",
        metric_id="metric_001",
    )


class IncidentInputIntegrationTests(unittest.TestCase):
    def test_list_of_decisions_is_incident_input(self) -> None:
        decisions = [
            _decision(CopType.ANOMALY_DETECTION, ActionType.PASS_TO_COP),
            _decision(CopType.POLICY_ENFORCEMENT, ActionType.ALERT),
        ]
        self.assertTrue(is_incident_input(decisions))

    def test_metric_event_is_not_incident_input(self) -> None:
        event = MetricEvent(
            metric_id="metric_001",
            metric_name="interaction_path_novelty",
            session_id="session_42",
            value=0.92,
            threshold=0.75,
            state=MetricState.EXCEPTION,
        )
        self.assertFalse(is_incident_input(event))
        self.assertFalse(is_incident_input([event]))


if __name__ == "__main__":
    unittest.main()
