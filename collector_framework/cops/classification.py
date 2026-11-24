"""
Classification COP: name the situation. Do not enforce.

Reads curated features. Does not recalculate metrics.
Does not query targeted context in V1.
"""

from __future__ import annotations

import time
from typing import Any, Dict, List

from cops.contracts import ActionType, COPDecision, CopType, MetricEvent, MetricState

FROZEN_TAGS = ("sensitive", "config", "high-risk", "watch", "routine")
OPTIONAL_TAG_KEYS = ("groups", "trigger_class", "solution_class")
REQUIRED_FEATURE_KEYS = ("sensitivity_tier", "is_config_file", "risk_markers")
SENSITIVE_MIN = 3


def _is_sensitive(features: Dict[str, Any]) -> bool:
    tier = features.get("sensitivity_tier")
    if isinstance(tier, bool) or not isinstance(tier, (int, float)):
        return False
    return float(tier) >= SENSITIVE_MIN


def _is_config(features: Dict[str, Any]) -> bool:
    return bool(features.get("is_config_file"))


def _is_high_risk(features: Dict[str, Any]) -> bool:
    markers = features.get("risk_markers")
    if not markers:
        return False
    return True


def _optional_tags(features: Dict[str, Any]) -> List[str]:
    tags: List[str] = []
    for key in OPTIONAL_TAG_KEYS:
        value = features.get(key)
        if isinstance(value, str) and value:
            tags.append(f"{key}:{value}")
    return tags


def _core_tags(features: Dict[str, Any]) -> List[str]:
    tags: List[str] = []
    if _is_sensitive(features):
        tags.append("sensitive")
    if _is_config(features):
        tags.append("config")
    if _is_high_risk(features):
        tags.append("high-risk")
    return tags


def _needs_watch(tags: List[str]) -> bool:
    return bool({"sensitive", "config", "high-risk"} & set(tags))


def collect_tags(features: Dict[str, Any]) -> List[str]:
    tags = _core_tags(features)
    tags.extend(_optional_tags(features))
    if _needs_watch(tags):
        tags.append("watch")
        return tags
    tags.append("routine")
    return tags


def _confidence(metric: MetricEvent, features: Dict[str, Any]) -> float:
    if metric.state == MetricState.UNKNOWN:
        return 0.30
    if all(key in features for key in REQUIRED_FEATURE_KEYS):
        return 0.95
    return 0.60


def _elapsed_ms(start: float) -> float:
    return (time.perf_counter() - start) * 1000.0


def _interpretation(tags: List[str]) -> str:
    return "Classification tags: " + ", ".join(tags)


def _downstream(tags: List[str]) -> List[CopType]:
    if "watch" not in tags:
        return []
    return [CopType.GOVERNANCE_INTELLIGENCE]


class ClassificationCOP:
    name = CopType.CLASSIFICATION

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
            action_reason="Classification failed.",
            metric_id=metric.metric_id,
            evaluation_ms=_elapsed_ms(start),
            failed=True,
            error=str(exc),
        )

    def _decide(self, metric: MetricEvent, start: float) -> COPDecision:
        features = metric.features or {}
        tags = collect_tags(features)
        context: Dict[str, Any] = {"tags": tags}
        if "tool_category" in features:
            context["tool_category"] = features.get("tool_category")
        return COPDecision(
            cop=self.name,
            session_id=metric.session_id,
            interpretation=_interpretation(tags),
            confidence=_confidence(metric, features),
            action=ActionType.NONE,
            action_reason="Tags only.",
            metric_id=metric.metric_id,
            context_used=context,
            downstream_cops=_downstream(tags),
            evaluation_ms=_elapsed_ms(start),
        )
