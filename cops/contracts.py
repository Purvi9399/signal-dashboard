"""
Shared COPS input/output types.

Every COP imports these. Do not copy this module into the dashboard repo.

Core principle:
1. Metrics are calculated deterministically outside COPS.
2. COPS receive metrics/exceptions, not entire database tables.
3. A COP may request a small targeted context query if needed.
4. A COP interprets the metric in its own behavioural context.
5. A COP produces a standard COPDecision.
6. Incident Response consumes COPDecision objects, not MetricEvent.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Sequence


class MetricState(str, Enum):
    NORMAL = "NORMAL"
    WARNING = "WARNING"
    EXCEPTION = "EXCEPTION"
    UNKNOWN = "UNKNOWN"
    CRITICAL = "CRITICAL"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class ActionType(str, Enum):
    NONE = "NONE"
    WATCH = "WATCH"
    ALERT = "ALERT"
    INSTRUCT = "INSTRUCT"
    REQUEST_CONTEXT = "REQUEST_CONTEXT"
    REQUEST_APPROVAL = "REQUEST_APPROVAL"
    ESCALATE = "ESCALATE"
    BLOCK = "BLOCK"
    PAUSE = "PAUSE"
    PASS_TO_COP = "PASS_TO_COP"


class CopType(str, Enum):
    """Canonical COP identifiers for `COPDecision.cop` and downstream lists."""

    WORKFLOW_ORCHESTRATION = "WorkflowOrchestration"
    RUNTIME_MONITORING = "RuntimeMonitoring"
    ANOMALY_DETECTION = "AnomalyDetection"
    CONTEXT_AND_MEMORY = "ContextAndMemory"
    INTERACTION_DISCOVERY = "InteractionDiscovery"
    INCIDENT_RESPONSE = "IncidentResponse"
    CLASSIFICATION = "Classification"
    POLICY_ENFORCEMENT = "PolicyEnforcement"
    RISK_MITIGATION = "RiskMitigation"
    RESOURCE_MANAGEMENT = "ResourceManagement"
    GOVERNANCE_INTELLIGENCE = "GovernanceIntelligence"
    BEHAVIOR_ANALYTICS = "BehaviorAnalytics"
    RECOVERY_AND_RESILIENCE = "RecoveryAndResilience"
    WORKLOAD_OPTIMIZATION = "WorkloadOptimization"
    AUDIT_AND_TRACEABILITY = "AuditAndTraceability"
    KNOWLEDGE_AND_POLICY = "KnowledgeAndPolicy"
    AGENT_LIFECYCLE = "AgentLifecycle"
    GOVERNANCE_ARCHITECTURE = "GovernanceArchitecture"


@dataclass
class MetricEvent:
    """Deterministic metric row produced outside COPS. Evidence is IDs only."""

    metric_id: str
    metric_name: str
    session_id: str
    value: Any
    threshold: Any
    state: MetricState
    trace_id: Optional[str] = None
    features: Dict[str, Any] = field(default_factory=dict)
    evidence_refs: List[str] = field(default_factory=list)
    metric_source: str = ""
    timestamp: Optional[str] = None


@dataclass
class COPDecision:
    """Standard finding/action object emitted by every COP."""

    cop: CopType
    session_id: str
    interpretation: str
    action: ActionType
    action_reason: str
    confidence: Optional[float] = None
    metric_id: Optional[str] = None
    context_used: Dict[str, Any] = field(default_factory=dict)
    downstream_cops: List[CopType] = field(default_factory=list)
    evaluation_ms: float = 0
    failed: bool = False
    error: Optional[str] = None
    evidence_refs: List[str] = field(default_factory=list)
    metrics_read: List[Dict[str, Any]] = field(default_factory=list)


def confidence_is_valid(value: Any) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    return 0.0 <= float(value) <= 1.0


# Empty confidence is valid. The COP must not invent a score.
# Return True only when the caller omitted the number.
def confidence_may_be_omitted(value: Any) -> bool:
    return value is None


def _is_id_string(ref: Any) -> bool:
    return isinstance(ref, str) and ref != ""


def evidence_refs_are_ids(refs: Sequence[Any]) -> bool:
    if not isinstance(refs, (list, tuple)):
        return False
    return all(_is_id_string(ref) for ref in refs)


def is_incident_input(value: Any) -> bool:
    if not isinstance(value, (list, tuple)):
        return False
    return all(isinstance(item, COPDecision) for item in value)


# COPS owned in this pass (do not implement the other side's files).
COLLEAGUE_COPS = (
    CopType.RUNTIME_MONITORING,
    CopType.INTERACTION_DISCOVERY,
    CopType.POLICY_ENFORCEMENT,
    CopType.ANOMALY_DETECTION,
    CopType.INCIDENT_RESPONSE,
)

OWNED_COPS = (CopType.CLASSIFICATION,)
