"""
Context and Memory COP: is session context consistent and usable?

Does not judge collector heartbeat. Does not tag situations.
Status comes from curated features only.
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from cops.context import ContextProvider
from cops.contracts import (
    ActionType,
    COPDecision,
    CopType,
    MetricEvent,
    MetricState,
    confidence_is_valid,
)

FROZEN_STATUSES = (
    "incomplete",
    "context_loss",
    "contradictory",
    "stale",
    "precedent_found",
    "context_ok",
)
REQUIRED_FEATURE_KEYS = (
    "compaction_count",
    "context_loss_after_compaction",
    "session_reset",
    "precedent_match",
    "context_consistency",
)
WATCH_STATUSES = ("context_loss", "contradictory", "stale")
UNCERTAIN_STATES = (MetricState.UNKNOWN, MetricState.NOT_APPLICABLE)


# True when all context-memory keys exist on the metric row.
# Missing keys must not look like a confirmed context_ok result.
def _has_required_keys(features: Dict[str, Any]) -> bool:
    return all(key in features for key in REQUIRED_FEATURE_KEYS)


# First problem flag in FR9 order, or empty when none apply.
# Split out of memory_status so McCabe stays at or below 5.
def _problem_status(features: Dict[str, Any]) -> str:
    if features.get("context_loss_after_compaction") is True:
        return "context_loss"
    if features.get("context_consistency") is False:
        return "contradictory"
    if features.get("session_reset") is True:
        return "stale"
    return ""


# Map curated features to one status in a fixed order.
# Incomplete first so missing metric-layer fields never become WATCH.
def memory_status(features: Dict[str, Any]) -> str:
    if not _has_required_keys(features):
        return "incomplete"
    problem = _problem_status(features)
    if problem:
        return problem
    if features.get("precedent_match") is True:
        return "precedent_found"
    return "context_ok"


# Copy confidence only when the metric layer supplied a valid number.
# Context and Memory must not invent 0.30, 0.60, or 0.95.
def _confidence(features: Dict[str, Any]) -> Optional[float]:
    value = features.get("confidence")
    if not confidence_is_valid(value):
        return None
    return float(value)


# Convert a perf_counter start time to milliseconds.
# Needed for COP self-observability on every decision.
def _elapsed_ms(start: float) -> float:
    return (time.perf_counter() - start) * 1000.0


# Choose NONE or WATCH from status and metric state.
# UNKNOWN and NOT_APPLICABLE force NONE so this COP does not claim certainty.
def _action(metric: MetricEvent, status: str) -> ActionType:
    if metric.state in UNCERTAIN_STATES:
        return ActionType.NONE
    if status in WATCH_STATUSES:
        return ActionType.WATCH
    return ActionType.NONE


# Route WATCH findings to Behavior Analytics only.
# Context and Memory does not recruit Incident Response by itself.
def _downstream(action: ActionType) -> List[CopType]:
    if action != ActionType.WATCH:
        return []
    return [CopType.BEHAVIOR_ANALYTICS]


# List which required features were present on the row.
# Lets later COPS see what this interpreter actually read.
def _metrics_read(features: Dict[str, Any]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for key in REQUIRED_FEATURE_KEYS:
        if key in features:
            rows.append({"metric": key, "value": features.get(key)})
    return rows


# Context and Memory COP: context quality, not live collector health.
class ContextMemoryCOP:
    name = CopType.CONTEXT_AND_MEMORY

    # Inject a context provider, or use the empty default.
    # Tests replace this to prove lookup does not change status.
    def __init__(self, context: Optional[ContextProvider] = None) -> None:
        self.context = context or ContextProvider()

    # Produce one COPDecision from a metric row.
    # Failure becomes ESCALATE so a crash is visible.
    def evaluate(self, metric: MetricEvent) -> COPDecision:
        start = time.perf_counter()
        try:
            return self._decide(metric, start)
        except Exception as exc:
            return self._failed(metric, exc, start)

    # Build the failed COPDecision with zero confidence.
    # Required so callers still receive a typed finding.
    def _failed(
        self, metric: MetricEvent, exc: Exception, start: float
    ) -> COPDecision:
        return COPDecision(
            cop=self.name,
            session_id=metric.session_id,
            interpretation="Evaluation failed.",
            confidence=0.0,
            action=ActionType.ESCALATE,
            action_reason="Context and Memory failed.",
            metric_id=metric.metric_id,
            evaluation_ms=_elapsed_ms(start),
            failed=True,
            error=str(exc),
        )

    # Fetch at most one session snapshot by session ID.
    # The body is context only; it must not change status.
    def _session(self, metric: MetricEvent) -> Dict[str, Any]:
        if not metric.session_id:
            return {}
        return self.context.get_session_context(metric.session_id)

    # Assemble interpretation, action, and optional session context.
    # Status is computed from features before any lookup.
    def _decide(self, metric: MetricEvent, start: float) -> COPDecision:
        features = metric.features or {}
        status = memory_status(features)
        action = _action(metric, status)
        used: Dict[str, Any] = {"status": status}
        session = self._session(metric)
        if session:
            used["session"] = session
        return COPDecision(
            cop=self.name,
            session_id=metric.session_id,
            interpretation="Context and Memory status: " + status,
            confidence=_confidence(features),
            action=action,
            action_reason="Do not claim certainty." if status == "incomplete" else "Context judgement.",
            metric_id=metric.metric_id,
            context_used=used,
            downstream_cops=_downstream(action),
            evaluation_ms=_elapsed_ms(start),
            metrics_read=_metrics_read(features),
        )
