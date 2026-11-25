"""Thin MCP Proxy extractors for Project Signal RawEvent payloads (Phase 2).

Stdio JSON-RPC wire (copilot/codex/antigravity/claude-code) is reshaped into
Purvi Signal rows and passed through extract_row. Cursor's network proxy
emits token-usage captures (not JSON-RPC); those map to model_request.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Mapping, Optional, Tuple

from curate.signal_curate import extract_row

_COLLECTOR = "mcp_proxy"


def _as_mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _one_tool_name(item: Any) -> Optional[str]:
    if isinstance(item, Mapping) and item.get("name"):
        return str(item["name"])
    if isinstance(item, str) and item:
        return item
    return None


def _tool_names(tools: Any) -> Optional[List[str]]:
    if not isinstance(tools, list):
        return None
    names: List[str] = []
    for item in tools:
        name = _one_tool_name(item)
        if name:
            names.append(name)
    return names or None


def _iso(when: Optional[datetime], raw: Mapping[str, Any]) -> Any:
    if when is not None:
        return when.isoformat()
    return raw.get("timestamp")


def _signal_row(
    *,
    tool: str,
    interaction: str,
    when: Optional[datetime],
    raw: Mapping[str, Any],
    detail: Dict[str, Any],
    success: Any = None,
) -> Dict[str, Any]:
    row: Dict[str, Any] = {
        "collector": _COLLECTOR,
        "agent_tool": tool,
        "interaction_type": interaction,
        "connector": raw.get("connector") or raw.get("mcp_server"),
        "detail": detail,
        "timestamp": _iso(when, raw),
    }
    if success is not None:
        row["success"] = success
    return row


def _via_extract_row(
    tool: str,
    interaction: str,
    when: Optional[datetime],
    raw: Mapping[str, Any],
    detail: Dict[str, Any],
    success: Any = None,
) -> Dict[str, Any]:
    return extract_row(
        _signal_row(
            tool=tool,
            interaction=interaction,
            when=when,
            raw=raw,
            detail=detail,
            success=success,
        )
    )


def _call_request(parsed: Mapping[str, Any]) -> Optional[Dict[str, Any]]:
    if parsed.get("method") != "tools/call":
        return None
    params = _as_mapping(parsed.get("params"))
    name = params.get("name")
    if not name:
        return None
    args = params.get("arguments")
    return {
        "tool": str(name),
        "arguments": dict(args) if isinstance(args, Mapping) else args,
    }


def _content_result(result: Mapping[str, Any]) -> Optional[Dict[str, Any]]:
    if "content" not in result and "isError" not in result:
        return None
    return {"result": result, "is_error": bool(result.get("isError"))}


def _has_rpc_payload(parsed: Mapping[str, Any]) -> bool:
    return "result" in parsed or "error" in parsed


def _result_mapping(parsed: Mapping[str, Any]) -> Optional[Mapping[str, Any]]:
    result = parsed.get("result")
    return result if isinstance(result, Mapping) else None


def _call_result_body(result: Mapping[str, Any]) -> Optional[Dict[str, Any]]:
    if "tools" in result:
        return None
    return _content_result(result)


def _rpc_error_result(parsed: Mapping[str, Any]) -> Optional[Dict[str, Any]]:
    if "error" not in parsed:
        return None
    return {"result": parsed.get("error"), "is_error": True}


def _call_result(parsed: Mapping[str, Any]) -> Optional[Dict[str, Any]]:
    if parsed.get("method") or not _has_rpc_payload(parsed):
        return None
    result = _result_mapping(parsed)
    if result is not None:
        return _call_result_body(result)
    return _rpc_error_result(parsed)


def _list_result(parsed: Mapping[str, Any]) -> Optional[Dict[str, Any]]:
    result = _result_mapping(parsed)
    if result is None or "tools" not in result:
        return None
    names = _tool_names(result.get("tools"))
    if names is None:
        return None
    return {"tools": names}


def _stdio_interaction(
    parsed: Mapping[str, Any],
) -> Optional[Tuple[str, Dict[str, Any], Any]]:
    call = _call_request(parsed)
    if call is not None:
        return "mcp_call", call, None
    listed = _list_result(parsed)
    if listed is not None:
        return "mcp_tools_discovered", listed, None
    result = _call_result(parsed)
    if result is None:
        return None
    success = False if result["is_error"] else True
    return "mcp_result", {"result": result["result"]}, success


def extract_mcp_stdio_wire(
    raw: Mapping[str, Any],
    when: Optional[datetime],
    *,
    tool: str,
) -> Optional[Dict[str, Any]]:
    """Map one stdio MCP wire log record to curator fields, or None."""
    parsed = raw.get("parsed")
    if not isinstance(parsed, Mapping):
        return None
    classified = _stdio_interaction(parsed)
    if classified is None:
        return None
    interaction, detail, success = classified
    return _via_extract_row(tool, interaction, when, raw, detail, success)


def _usage_tokens(raw: Mapping[str, Any]) -> Optional[Tuple[int, int]]:
    payload = _as_mapping(raw.get("payload"))
    usage = _as_mapping(payload.get("usage"))
    tin = usage.get("input_tokens")
    tout = usage.get("output_tokens")
    if tin is None and tout is None:
        return None
    return int(tin or 0), int(tout or 0)


def _session_str(raw: Mapping[str, Any]) -> Optional[str]:
    session = raw.get("session_id")
    return str(session) if session else None


def _model_request_fields(
    when: Optional[datetime], session: Optional[str], tin: int, tout: int
) -> Dict[str, Any]:
    return {
        "tool": "cursor",
        "occurred_at": when,
        "observable_type": "model_request",
        "session_id": session,
        "conversation_id": session,
        "input_tokens": tin or None,
        "output_tokens": tout or None,
        "total_tokens": (tin + tout) or None,
    }


def extract_cursor_network_proxy(
    raw: Mapping[str, Any],
    when: Optional[datetime],
) -> Optional[Dict[str, Any]]:
    """Map Cursor network_context_capture to model_request fields."""
    if raw.get("event_type") != "network_context_capture":
        return None
    tokens = _usage_tokens(raw)
    if tokens is None:
        return None
    tin_i, tout_i = tokens
    return _model_request_fields(when, _session_str(raw), tin_i, tout_i)


def is_stdio_mcp_wire(raw: Mapping[str, Any]) -> bool:
    return "direction" in raw and ("parsed" in raw or "raw" in raw)
