"""Targeted context for COPS. One ID lookup only. Not SELECT *."""

from __future__ import annotations

from typing import Any, Dict


# Default context source for COPS that may fetch one row.
# Override in tests or with Redis/SQL adapters later.
class ContextProvider:
    # Return one raw event by ID, or an empty dict.
    # Empty default avoids a table scan in V1.
    def get_raw_event(self, event_id: str) -> Dict[str, Any]:
        return {}

    # Return one session snapshot by session ID, or an empty dict.
    # Context and Memory uses this. It must not scan full history.
    def get_session_context(self, session_id: str) -> Dict[str, Any]:
        return {}
