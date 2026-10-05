"""Targeted context for COPS. One ID lookup only. Not SELECT *."""

from __future__ import annotations

from typing import Any, Dict


class ContextProvider:
    def get_raw_event(self, event_id: str) -> Dict[str, Any]:
        return {}
