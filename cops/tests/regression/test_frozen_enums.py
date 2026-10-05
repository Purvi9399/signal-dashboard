"""Regression: freeze CopType and ActionType string values (FR7, FR8, FR9)."""

from __future__ import annotations

import unittest

from cops.contracts import ActionType, CopType, MetricState

FROZEN_COP_VALUES = (
    "WorkflowOrchestration",
    "RuntimeMonitoring",
    "AnomalyDetection",
    "ContextAndMemory",
    "InteractionDiscovery",
    "IncidentResponse",
    "Classification",
    "PolicyEnforcement",
    "RiskMitigation",
    "ResourceManagement",
    "GovernanceIntelligence",
    "BehaviorAnalytics",
    "RecoveryAndResilience",
    "WorkloadOptimization",
    "AuditAndTraceability",
    "KnowledgeAndPolicy",
    "AgentLifecycle",
    "GovernanceArchitecture",
)

FROZEN_ACTIONS = (
    "NONE",
    "WATCH",
    "ALERT",
    "INSTRUCT",
    "REQUEST_CONTEXT",
    "REQUEST_APPROVAL",
    "ESCALATE",
    "BLOCK",
    "PAUSE",
    "PASS_TO_COP",
)

FROZEN_STATES = ("NORMAL", "WARNING", "EXCEPTION", "UNKNOWN")


class FrozenEnumRegressionTests(unittest.TestCase):
    def test_cop_type_values_frozen(self) -> None:
        self.assertEqual(tuple(c.value for c in CopType), FROZEN_COP_VALUES)
        self.assertEqual(len(CopType), 18)

    def test_action_type_values_frozen(self) -> None:
        self.assertEqual(tuple(a.value for a in ActionType), FROZEN_ACTIONS)

    def test_metric_state_values_frozen(self) -> None:
        self.assertEqual(tuple(s.value for s in MetricState), FROZEN_STATES)


if __name__ == "__main__":
    unittest.main()
