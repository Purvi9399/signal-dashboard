"""Unit tests for Context and Memory status rules (FR3–FR12, FR18)."""

from __future__ import annotations

import unittest

from cops.context_memory import ContextMemoryCOP
from cops.contracts import ActionType, CopType, MetricEvent, MetricState


# Build a MetricEvent for unit cases from a feature dict.
# Keeps each test focused on status rules, not fixture noise.
def _event(features: dict, state: MetricState = MetricState.NORMAL) -> MetricEvent:
    return MetricEvent(
        metric_id="metric_ctx_1",
        metric_name="context_consistency",
        session_id="session_42",
        value=features.get("context_consistency"),
        threshold=True,
        state=state,
        features=features,
    )


COMPLETE = {
    "compaction_count": 0,
    "context_loss_after_compaction": False,
    "session_reset": False,
    "precedent_match": False,
    "context_consistency": True,
}


class ContextMemoryUnitTests(unittest.TestCase):
    # Fresh COP per test so injected context cannot leak.
    def setUp(self) -> None:
        self.cop = ContextMemoryCOP()

    # Complete healthy features must be context_ok with action NONE.
    def test_context_ok_is_none(self) -> None:
        decision = self.cop.evaluate(_event(dict(COMPLETE)))
        self.assertEqual(decision.cop, CopType.CONTEXT_AND_MEMORY)
        self.assertEqual(decision.action, ActionType.NONE)
        self.assertEqual(decision.context_used["status"], "context_ok")
        self.assertEqual(decision.downstream_cops, [])
        self.assertIsNone(decision.confidence)

    # Compaction loss must WATCH Behavior Analytics.
    def test_context_loss_watches(self) -> None:
        features = dict(COMPLETE)
        features["context_loss_after_compaction"] = True
        decision = self.cop.evaluate(_event(features))
        self.assertEqual(decision.context_used["status"], "context_loss")
        self.assertEqual(decision.action, ActionType.WATCH)
        self.assertEqual(
            decision.downstream_cops,
            [CopType.BEHAVIOR_ANALYTICS],
        )

    # False consistency wins over session_reset (FR9 order).
    def test_contradictory_beats_stale_order(self) -> None:
        features = dict(COMPLETE)
        features["context_consistency"] = False
        features["session_reset"] = True
        decision = self.cop.evaluate(_event(features))
        self.assertEqual(decision.context_used["status"], "contradictory")
        self.assertEqual(decision.action, ActionType.WATCH)

    # Session reset without loss or contradiction is stale.
    def test_stale_watches(self) -> None:
        features = dict(COMPLETE)
        features["session_reset"] = True
        decision = self.cop.evaluate(_event(features))
        self.assertEqual(decision.context_used["status"], "stale")
        self.assertEqual(decision.action, ActionType.WATCH)

    # Precedent match is NONE, not a problem WATCH.
    def test_precedent_found_is_none(self) -> None:
        features = dict(COMPLETE)
        features["precedent_match"] = True
        decision = self.cop.evaluate(_event(features))
        self.assertEqual(decision.context_used["status"], "precedent_found")
        self.assertEqual(decision.action, ActionType.NONE)
        self.assertEqual(decision.downstream_cops, [])

    # Missing keys must be incomplete NONE, not a false WATCH.
    def test_incomplete_is_none(self) -> None:
        decision = self.cop.evaluate(_event({}))
        self.assertEqual(decision.context_used["status"], "incomplete")
        self.assertEqual(decision.action, ActionType.NONE)
        self.assertIsNone(decision.confidence)

    # UNKNOWN telemetry must stay on NONE even if features say loss.
    def test_unknown_state_does_not_claim_certainty(self) -> None:
        features = dict(COMPLETE)
        features["context_loss_after_compaction"] = True
        decision = self.cop.evaluate(_event(features, MetricState.UNKNOWN))
        self.assertEqual(decision.action, ActionType.NONE)
        self.assertEqual(decision.downstream_cops, [])
        self.assertIsNone(decision.confidence)

    # Copied metric confidence is allowed. Invented scores are not.
    def test_supplied_confidence_is_copied(self) -> None:
        features = dict(COMPLETE)
        features["confidence"] = 0.8
        decision = self.cop.evaluate(_event(features))
        self.assertEqual(decision.confidence, 0.8)


if __name__ == "__main__":
    unittest.main()
