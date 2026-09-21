"""Thin CLI Wrapper extractors for Project Signal RawEvent payloads (Phase 5).

Pty chunk records {stream, content, wrapped_command, ...} and optional
Signal-shaped rows reshape into Purvi extract_row cli_wrapper fields.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Mapping, Optional

from curate.signal_curate import extract_row

_COLLECTOR = "cli_wrapper"

_INTERACTION = {
    "session_start": "session_start",
    "session_end": "session_end",
    "user_input": "user_input",
    "user_prompt": "user_input",
    "stall": "stall",
    "shell_stall": "stall",
    "interrupt": "interrupt",
    "shell_interrupt": "interrupt",
}


def _as_mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _iso(when: Optional[datetime], raw: Mapping[str, Any]) -> Any:
    if when is not None:
        return when.isoformat()
    return raw.get("timestamp")


def _via_extract_row(
    tool: str,
    interaction: str,
    when: Optional[datetime],
    raw: Mapping[str, Any],
    detail: Dict[str, Any],
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    row: Dict[str, Any] = {
        "collector": _COLLECTOR,
        "agent_tool": tool,
        "interaction_type": interaction,
        "detail": detail,
        "timestamp": _iso(when, raw),
        "operator": raw.get("operator_username") or raw.get("operator"),
        "cwd": raw.get("cwd"),
        "workspace": raw.get("workspace"),
    }
    if extra:
        row.update(extra)
    return extract_row(row)


def _explicit_interaction(raw: Mapping[str, Any]) -> Optional[str]:
    for key in ("interaction_type", "event_type"):
        value = raw.get(key)
        if value in _INTERACTION:
            return _INTERACTION[str(value)]
    return None


def _detail_from_raw(raw: Mapping[str, Any], interaction: str) -> Dict[str, Any]:
    detail: Dict[str, Any] = {}
    if interaction == "user_input":
        detail["text"] = raw.get("content") or raw.get("text")
    else:
        detail["command"] = raw.get("wrapped_command") or raw.get("command")
    _apply_stall_fields(detail, raw)
    _apply_exit_code(detail, raw)
    return detail


def _apply_stall_fields(detail: Dict[str, Any], raw: Mapping[str, Any]) -> None:
    if raw.get("silent_seconds") is not None:
        detail["silent_seconds"] = raw.get("silent_seconds")
    elif raw.get("hung_seconds") is not None:
        detail["silent_seconds"] = raw.get("hung_seconds")


def _apply_exit_code(detail: Dict[str, Any], raw: Mapping[str, Any]) -> None:
    if raw.get("exit_code") is not None:
        detail["exit_code"] = raw.get("exit_code")


def _from_explicit(
    raw: Mapping[str, Any], when: Optional[datetime], tool: str
) -> Optional[Dict[str, Any]]:
    interaction = _explicit_interaction(raw)
    if interaction is None:
        return None
    return _via_extract_row(
        tool, interaction, when, raw, _detail_from_raw(raw, interaction)
    )


def _from_stall(
    raw: Mapping[str, Any], when: Optional[datetime], tool: str
) -> Optional[Dict[str, Any]]:
    if raw.get("silent_seconds") is None and raw.get("hung_seconds") is None:
        return None
    return _via_extract_row(
        tool, "stall", when, raw, _detail_from_raw(raw, "stall")
    )


def _from_stdin(
    raw: Mapping[str, Any], when: Optional[datetime], tool: str
) -> Optional[Dict[str, Any]]:
    if raw.get("stream") != "stdin":
        return None
    content = raw.get("content")
    if content in (None, ""):
        return None
    return _via_extract_row(
        tool, "user_input", when, raw, _detail_from_raw(raw, "user_input")
    )


def _from_session_marker(
    raw: Mapping[str, Any], when: Optional[datetime], tool: str
) -> Optional[Dict[str, Any]]:
    """session_start when wrapped_command is set and stream/content absent."""
    if raw.get("stream") is not None:
        return None
    cmd = raw.get("wrapped_command") or raw.get("command")
    if not cmd:
        return None
    return _via_extract_row(
        tool,
        "session_start",
        when,
        raw,
        {"command": cmd},
    )


def extract_cli_wrapper(
    raw: Mapping[str, Any],
    when: Optional[datetime],
    *,
    tool: str,
) -> Optional[Dict[str, Any]]:
    """Map one CLI wrapper raw_payload to curator fields, or None."""
    for mapper in (_from_explicit, _from_stall, _from_stdin, _from_session_marker):
        fields = mapper(raw, when, tool)
        if fields is not None:
            return fields
    return None
