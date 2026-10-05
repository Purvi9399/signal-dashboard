"""Integration: Audit lookup is one ID; status stays feature-driven (FR12, FR13)."""

from __future__ import annotations

import unittest

from cops.audit import AuditCOP
from cops.context import ContextProvider
from cops.contracts import (
    ActionType,
    CopType,
    MetricEvent,
    MetricState,
    is_incident_input,
)


class FakeContext(ContextProvider):
    def get_raw_event(self, event_id: str) -> dict:
        return {"event_id": event_id, "collector": "local_hooks"}


class AuditIntegrationTests(unittest.TestCase):
    def test_lookup_does_not_override_disputed_status(self) -> None:
        cop = AuditCOP(context=FakeContext())
        event = MetricEvent(
            metric_id="metric_audit_2",
            metric_name="collectors_agree",
            session_id="session_42",
            value=False,
            threshold=True,
            state=MetricState.EXCEPTION,
            features={
                "raw_ref": "source_events:evt_9",
                "attribution": "agent",
                "unattributed": False,
                "collectors_agree": False,
            },
            evidence_refs=["evt_9"],
        )
        decision = cop.evaluate(event)
        self.assertTrue(is_incident_input([decision]))
        self.assertEqual(decision.context_used["status"], "disputed")
        self.assertEqual(decision.action, ActionType.ALERT)
        self.assertEqual(decision.context_used["raw_event"]["event_id"], "evt_9")
        self.assertEqual(
            decision.downstream_cops,
            [CopType.GOVERNANCE_ARCHITECTURE],
        )


if __name__ == "__main__":
    unittest.main()
