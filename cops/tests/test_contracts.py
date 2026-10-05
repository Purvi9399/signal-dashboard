"""Unit tests for shared COPS contracts."""

from __future__ import annotations

import unittest

from cops.contracts import (
    COLLEAGUE_COPS,
    OWNED_COPS,
    ActionType,
    COPDecision,
    CopType,
    MetricEvent,
    MetricState,
)


class ContractTests(unittest.TestCase):
    def test_metric_event_defaults_keep_evidence_as_ids(self) -> None:
        event = MetricEvent(
            metric_id="metric_001",
            metric_name="telemetry_completeness",
            session_id="session_42",
            value=0.72,
            threshold=0.90,
            state=MetricState.EXCEPTION,
            evidence_refs=["evt_101"],
            metric_source="curate.collectors_agree",
        )
        self.assertIsNone(event.trace_id)
        self.assertEqual(event.features, {})
        self.assertEqual(event.evidence_refs, ["evt_101"])

    def test_cop_decision_allows_incident_without_own_metric(self) -> None:
        decision = COPDecision(
            cop=CopType.INCIDENT_RESPONSE,
            session_id="session_42",
            interpretation="No actionable governance exception is present.",
            confidence=0.9,
            action=ActionType.NONE,
            action_reason="No action required.",
        )
        self.assertIsNone(decision.metric_id)
        self.assertEqual(decision.downstream_cops, [])
        self.assertFalse(decision.failed)

    def test_ownership_split(self) -> None:
        self.assertEqual(len(COLLEAGUE_COPS), 5)
        self.assertEqual(OWNED_COPS, (CopType.CLASSIFICATION,))
        overlap = set(COLLEAGUE_COPS) & set(OWNED_COPS)
        self.assertEqual(overlap, set())


if __name__ == "__main__":
    unittest.main()
