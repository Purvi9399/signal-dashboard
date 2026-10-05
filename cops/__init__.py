"""
COPS: metric-fed governance modules.

Metrics are calculated outside this package. COPS receive MetricEvent
rows, optionally request a small targeted context query, and emit
COPDecision. Incident Response consumes COPDecision objects, not raw
metrics.
"""

from __future__ import annotations

from .contracts import ActionType, COPDecision, CopType, MetricEvent, MetricState

__all__ = [
    "ActionType",
    "COPDecision",
    "CopType",
    "MetricEvent",
    "MetricState",
]
