"""Functional: ContextMemoryCOP public evaluate path (FR1, FR13, FR15)."""

from __future__ import annotations

import unittest

from cops import ActionType, ContextMemoryCOP, CopType, MetricEvent, MetricState


class ContextMemoryFunctionalTests(unittest.TestCase):
    # Public cops.ContextMemoryCOP must evaluate a complete row without failure.
    def test_package_exports_context_memory_cop(self) -> None:
        event = MetricEvent(
            metric_id="metric_ctx_3",
            metric_name="context_consistency",
            session_id="session_42",
            value=True,
            threshold=True,
            state=MetricState.NORMAL,
            features={
                "compaction_count": 0,
                "context_loss_after_compaction": False,
                "session_reset": False,
                "precedent_match": False,
                "context_consistency": True,
            },
        )
        decision = ContextMemoryCOP().evaluate(event)
        self.assertEqual(decision.cop, CopType.CONTEXT_AND_MEMORY)
        self.assertEqual(decision.action, ActionType.NONE)
        self.assertEqual(decision.context_used["status"], "context_ok")
        self.assertFalse(decision.failed)


if __name__ == "__main__":
    unittest.main()
