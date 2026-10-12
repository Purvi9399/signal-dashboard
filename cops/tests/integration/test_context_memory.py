"""Integration: session lookup is one ID; status stays feature-driven (FR15, FR16)."""

from __future__ import annotations

import unittest

from cops.context import ContextProvider
from cops.context_memory import ContextMemoryCOP
from cops.contracts import (
    ActionType,
    CopType,
    MetricEvent,
    MetricState,
    is_incident_input,
)


# Stub that returns one session snapshot so tests never hit Redis or SQL.
class FakeContext(ContextProvider):
    # Echo the requested session ID as a minimal snapshot.
    def get_session_context(self, session_id: str) -> dict:
        return {"session_id": session_id, "turns": 3}


class ContextMemoryIntegrationTests(unittest.TestCase):
    # Lookup may attach session but contradictory status stays from features.
    def test_lookup_does_not_override_contradictory_status(self) -> None:
        cop = ContextMemoryCOP(context=FakeContext())
        event = MetricEvent(
            metric_id="metric_ctx_2",
            metric_name="context_consistency",
            session_id="session_42",
            value=False,
            threshold=True,
            state=MetricState.EXCEPTION,
            features={
                "compaction_count": 2,
                "context_loss_after_compaction": False,
                "session_reset": False,
                "precedent_match": False,
                "context_consistency": False,
            },
        )
        decision = cop.evaluate(event)
        self.assertTrue(is_incident_input([decision]))
        self.assertEqual(decision.context_used["status"], "contradictory")
        self.assertEqual(decision.action, ActionType.WATCH)
        self.assertEqual(decision.context_used["session"]["session_id"], "session_42")
        self.assertEqual(
            decision.downstream_cops,
            [CopType.BEHAVIOR_ANALYTICS],
        )


if __name__ == "__main__":
    unittest.main()
