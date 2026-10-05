"""
Audit COP: can we attribute and reconstruct this action?

Does not judge collector heartbeat or latency.
Status comes from curated features only.
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from cops.context import ContextProvider
from cops.contracts import ActionType, COPDecision, CopType, MetricEvent, MetricState

FROZEN_STATUSES = ("incomplete", "disputed", "untraceable", "attributable")
REQUIRED_FEATURE_KEYS = (
    "raw_ref",
    "attribution",
    "unattributed",
    "collectors_agree",
)


def _has_required_keys(features: Dict[str, Any]) -> bool:
    return all(key in features for key in REQUIRED_FEATURE_KEYS)


def _is_untraceable(features: Dict[str, Any]) -> bool:
    if features.get("unattributed") is True:
        return True
    return not features.get("raw_ref")


def audit_status(features: Dict[str, Any]) -> str:
    if not _has_required_keys(features):
        return "incomplete"
    if features.get("collectors_agree") is False:
        return "disputed"
    if _is_untraceable(features):
        return "untraceable"
    return "attributable"


def _alerts(status: str) -> bool:
    return status in ("untraceable", "disputed")


def _confidence(metric: MetricEvent, features: Dict[str, Any]) -> float:
    if metric.state == MetricState.UNKNOWN:
        return 0.30
    if _has_required_keys(features):
        return 0.95
    return 0.60


def _elapsed_ms(start: float) -> float:
    return (time.perf_counter() - start) * 1000.0


def _action(metric: MetricEvent, status: str) -> ActionType:
    if metric.state == MetricState.UNKNOWN:
        return ActionType.NONE
    if _alerts(status):
        return ActionType.ALERT
    return ActionType.NONE


def _downstream(status: str) -> List[CopType]:
    if not _alerts(status):
        return []
    return [CopType.GOVERNANCE_ARCHITECTURE]


def _first_ref(metric: MetricEvent) -> Optional[str]:
    if not metric.evidence_refs:
        return None
    return metric.evidence_refs[0]


class AuditCOP:
    name = CopType.AUDIT_AND_TRACEABILITY

    def __init__(self, context: Optional[ContextProvider] = None) -> None:
        self.context = context or ContextProvider()

    def evaluate(self, metric: MetricEvent) -> COPDecision:
        start = time.perf_counter()
        try:
            return self._decide(metric, start)
        except Exception as exc:
            return self._failed(metric, exc, start)

    def _failed(
        self, metric: MetricEvent, exc: Exception, start: float
    ) -> COPDecision:
        return COPDecision(
            cop=self.name,
            session_id=metric.session_id,
            interpretation="Evaluation failed.",
            confidence=0.0,
            action=ActionType.ESCALATE,
            action_reason="Audit failed.",
            metric_id=metric.metric_id,
            evaluation_ms=_elapsed_ms(start),
            failed=True,
            error=str(exc),
        )

    def _raw_event(self, metric: MetricEvent) -> Dict[str, Any]:
        event_id = _first_ref(metric)
        if not event_id:
            return {}
        return self.context.get_raw_event(event_id)

    def _decide(self, metric: MetricEvent, start: float) -> COPDecision:
        features = metric.features or {}
        status = audit_status(features)
        used: Dict[str, Any] = {"status": status}
        raw_event = self._raw_event(metric)
        if raw_event:
            used["raw_event"] = raw_event
        return COPDecision(
            cop=self.name,
            session_id=metric.session_id,
            interpretation="Audit status: " + status,
            confidence=_confidence(metric, features),
            action=_action(metric, status),
            action_reason="Do not claim certainty." if status == "incomplete" else "Audit judgement.",
            metric_id=metric.metric_id,
            context_used=used,
            downstream_cops=_downstream(status),
            evaluation_ms=_elapsed_ms(start),
        )
