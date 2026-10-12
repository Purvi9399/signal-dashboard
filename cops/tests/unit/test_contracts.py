"""Unit tests for COPS contracts (FR1–FR5, FR10, FR13)."""

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
    confidence_is_valid,
    evidence_refs_are_ids,
)


class MetricEventUnitTests(unittest.TestCase):
    def test_required_fields_and_id_only_evidence(self) -> None:
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
        self.assertTrue(evidence_refs_are_ids(event.evidence_refs))

    def test_empty_ref_is_not_an_id(self) -> None:
        self.assertFalse(evidence_refs_are_ids([""]))
        self.assertFalse(evidence_refs_are_ids(["evt_1", ""]))


class COPDecisionUnitTests(unittest.TestCase):
    def test_incident_decision_may_omit_metric_id(self) -> None:
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
        self.assertTrue(confidence_is_valid(decision.confidence))

    def test_confidence_bounds(self) -> None:
        self.assertTrue(confidence_is_valid(0.0))
        self.assertTrue(confidence_is_valid(1.0))
        self.assertFalse(confidence_is_valid(-0.01))
        self.assertFalse(confidence_is_valid(1.01))


class OwnershipUnitTests(unittest.TestCase):
    def test_ownership_split(self) -> None:
        self.assertEqual(len(COLLEAGUE_COPS), 5)
        self.assertEqual(
            OWNED_COPS,
            (CopType.CLASSIFICATION, CopType.CONTEXT_AND_MEMORY),
        )
        self.assertEqual(set(COLLEAGUE_COPS) & set(OWNED_COPS), set())


if __name__ == "__main__":
    unittest.main()
