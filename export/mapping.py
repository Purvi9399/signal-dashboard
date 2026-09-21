"""Map collector RawEvent envelopes onto public.source_events row dicts.

Does not mutate raw_payload. Identity stamps (when present later) belong on
the export envelope / sibling columns, not inside raw_payload.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Mapping, Optional

# public.signal_tool
_TOOL_ALIASES = {
    "antigravity": "antigravity",
    "cursor": "cursor",
    "codex": "codex",
    "copilot": "copilot",
    "claude-code": "claude-code",
    "claude_code": "claude-code",
    "claudecode": "claude-code",
    "custom": "custom",
    "unknown": "unknown",
}

# public.signal_collector — technique suffix of collector_id (after last ".")
_COLLECTOR_ALIASES = {
    "hooks": "local_hooks",
    "local_hooks": "local_hooks",
    "otel": "otel",
    "cli_wrapper": "cli_wrapper",
    "mcp_proxy": "mcp_proxy",
    "fs_watcher": "fs_watcher",
    "webhook": "webhook",
    "derived": "derived",
}

_SESSION_KEYS = (
    "conversationId",
    "conversation_id",
    "sessionId",
    "session_id",
    "sessionID",
)

_EVENT_NAME_KEYS = (
    "event_type",
    "eventType",
    "hook_event_name",
    "hookEventName",
    "name",
    "type",
)


def map_tool(agent: Optional[str]) -> str:
    if not agent:
        return "unknown"
    key = str(agent).strip().lower().replace(" ", "-")
    return _TOOL_ALIASES.get(key, "unknown")


def map_collector(collector_id: Optional[str]) -> str:
    if not collector_id:
        return "derived"
    technique = str(collector_id).rsplit(".", 1)[-1].strip().lower()
    return _COLLECTOR_ALIASES.get(technique, "derived")


def _best_effort_str(payload: Mapping[str, Any], keys: tuple) -> Optional[str]:
    for key in keys:
        value = payload.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return None


def _event_name(raw_payload: Mapping[str, Any], collector_id: str) -> str:
    direct = _best_effort_str(raw_payload, _EVENT_NAME_KEYS)
    if direct:
        return direct
    tool_call = raw_payload.get("toolCall")
    if isinstance(tool_call, Mapping):
        name = tool_call.get("name")
        if name:
            return str(name)
    return collector_id or "unknown"


def _parse_received_at(timestamp: Optional[str]) -> str:
    """Return an ISO-8601 timestamptz string for PostgREST."""
    if timestamp:
        text = str(timestamp).strip()
        if text:
            # Normalize trailing Z for Postgres-friendly ISO
            if text.endswith("Z"):
                text = text[:-1] + "+00:00"
            try:
                datetime.fromisoformat(text)
                return text
            except ValueError:
                pass
    return datetime.now(timezone.utc).isoformat()


def map_raw_event_dict(
    event: Mapping[str, Any],
    *,
    tenant_id: str,
    device_id: Optional[str],
    ingest_batch_id: str,
    redaction_level: str = "redacted",
    file_origin: Optional[str] = None,
    source_ref: Optional[str] = None,
) -> Dict[str, Any]:
    """Build one source_events insert row from a RawEvent-shaped dict."""
    timestamp = event.get("timestamp")
    collector_id = str(event.get("collector_id") or "")
    agent = event.get("agent")
    raw_payload = event.get("raw_payload")
    if not isinstance(raw_payload, Mapping):
        raw_payload = {} if raw_payload is None else {"_non_object_payload": raw_payload}

    envelope = {
        "timestamp": timestamp,
        "collector_id": collector_id,
        "agent": agent,
        "raw_payload": raw_payload,
    }

    row: Dict[str, Any] = {
        "tenant_id": tenant_id,
        "device_id": device_id,
        "ingest_batch_id": ingest_batch_id,
        "redaction_level": redaction_level,
        "received_at": _parse_received_at(timestamp if isinstance(timestamp, str) else None),
        "tool": map_tool(agent if isinstance(agent, str) else None),
        "collector": map_collector(collector_id),
        "session_id": _best_effort_str(raw_payload, _SESSION_KEYS),
        "event_name": _event_name(raw_payload, collector_id),
        "payload": envelope,
    }
    if file_origin:
        row["file_origin"] = file_origin
    if source_ref:
        row["source_ref"] = source_ref
    return row
