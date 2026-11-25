"""Detect agent session-complete hooks for live upload flush."""

from __future__ import annotations

from typing import Any, Mapping

from export.mapping import _best_effort_str, _EVENT_NAME_KEYS

# Exact names after lowercasing. subagentStop is not in this set.
_END_NAMES = frozenset({"stop", "sessionend", "session_end"})


def _payload(event: Any) -> Mapping[str, Any]:
    if isinstance(event, Mapping):
        raw = event.get("raw_payload")
    else:
        raw = getattr(event, "raw_payload", None)
    if isinstance(raw, Mapping):
        return raw
    return {}


def is_session_end(event: Any) -> bool:
    raw = _payload(event)
    name = (_best_effort_str(raw, _EVENT_NAME_KEYS) or "").lower()
    if name in _END_NAMES:
        return True
    return bool(raw.get("fullyIdle") or raw.get("terminationReason"))
