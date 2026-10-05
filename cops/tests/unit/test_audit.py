"""Unit tests for Audit status rules (FR3–FR9, FR15, FR16)."""

from __future__ import annotations

import unittest

from cops.audit import AuditCOP
from cops.contracts import ActionType, CopType, MetricEvent, MetricState


def _event(features: dict, state: MetricState = MetricState.NORMAL) -> MetricEvent:
    return MetricEvent(
        metric_id="metric_audit_1",
        metric_name="raw_ref",
        session_id="session_42",
        value=features.get("raw_ref"),
        threshold=None,
        state=state,
        features=features,
        evidence_refs=["evt_1"],
    )


COMPLETE = {
    "raw_ref": "source_events:evt_1",
    "attribution": "agent",
    "unattributed": False,
    "collectors_agree": True,
}


class AuditUnitTests(unittest.TestCase):
    def setUp(self) -> None:
        self.cop = AuditCOP()

    def test_attributable_is_none(self) -> None:
        decision = self.cop.evaluate(_event(dict(COMPLETE)))
        self.assertEqual(decision.cop, CopType.AUDIT_AND_TRACEABILITY)
        self.assertEqual(decision.action, ActionType.NONE)
        self.assertEqual(decision.context_used["status"], "attributable")
        self.assertEqual(decision.downstream_cops, [])
        self.assertEqual(decision.confidence, 0.95)

    def test_untraceable_alerts(self) -> None:
        features = dict(COMPLETE)
        features["raw_ref"] = ""
        features["unattributed"] = True
        decision = self.cop.evaluate(_event(features))
        self.assertEqual(decision.context_used["status"], "untraceable")
        self.assertEqual(decision.action, ActionType.ALERT)
        self.assertEqual(
            decision.downstream_cops,
            [CopType.GOVERNANCE_ARCHITECTURE],
        )

    def test_disputed_beats_untraceable_order(self) -> None:
        features = dict(COMPLETE)
        features["collectors_agree"] = False
        features["raw_ref"] = ""
        decision = self.cop.evaluate(_event(features))
        self.assertEqual(decision.context_used["status"], "disputed")
        self.assertEqual(decision.action, ActionType.ALERT)

    def test_incomplete_is_none(self) -> None:
        decision = self.cop.evaluate(_event({}))
        self.assertEqual(decision.context_used["status"], "incomplete")
        self.assertEqual(decision.action, ActionType.NONE)
        self.assertEqual(decision.confidence, 0.60)

    def test_unknown_state_does_not_claim_certainty(self) -> None:
        decision = self.cop.evaluate(_event(dict(COMPLETE), MetricState.UNKNOWN))
        self.assertEqual(decision.confidence, 0.30)
        self.assertEqual(decision.action, ActionType.NONE)


if __name__ == "__main__":
    unittest.main()
