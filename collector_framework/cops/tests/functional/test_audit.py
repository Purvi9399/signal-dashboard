"""Functional: AuditCOP public evaluate path (FR1, FR10)."""

from __future__ import annotations

import unittest

from cops import ActionType, AuditCOP, CopType, MetricEvent, MetricState


class AuditFunctionalTests(unittest.TestCase):
    # Public cops.AuditCOP must evaluate a complete row without failure.
    def test_package_exports_audit_cop(self) -> None:
        event = MetricEvent(
            metric_id="metric_audit_3",
            metric_name="attribution",
            session_id="session_42",
            value="agent",
            threshold=None,
            state=MetricState.NORMAL,
            features={
                "raw_ref": "source_events:evt_3",
                "attribution": "agent",
                "unattributed": False,
                "collectors_agree": True,
            },
        )
        decision = AuditCOP().evaluate(event)
        self.assertEqual(decision.cop, CopType.AUDIT_AND_TRACEABILITY)
        self.assertEqual(decision.action, ActionType.NONE)
        self.assertFalse(decision.failed)


if __name__ == "__main__":
    unittest.main()
