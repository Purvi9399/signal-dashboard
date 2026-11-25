"""Thin FS Watcher extractors for Project Signal RawEvent payloads (Phase 3).

Generic watchdog records (created/modified/deleted/moved*) reshape into
Purvi Signal rows via extract_row. Claude Code transcript JSONL lines
(no event_type) map to transcript_appended with usage tokens.
"""

from __future__ import annotations

import os
from datetime import datetime
from typing import Any, Dict, Mapping, Optional

from curate.signal_curate import extract_row

_COLLECTOR = "fs_watcher"

_FS_TYPE = {
    "created": "file_create",
    "modified": "file_modify",
    "deleted": "file_delete",
    "moved": "file_modify",
    "moved_from": "file_modify",
    "moved_to": "file_modify",
}

_TRANSCRIPT_NAMES = frozenset(
    {"transcript.jsonl", "transcript_full.jsonl"}
)


def _as_mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _iso(when: Optional[datetime], raw: Mapping[str, Any]) -> Any:
    if when is not None:
        return when.isoformat()
    return raw.get("timestamp")


def _path_of(raw: Mapping[str, Any]) -> Optional[str]:
    path = raw.get("path") or raw.get("abs_path")
    return str(path) if path else None


def _size_of(raw: Mapping[str, Any]) -> Any:
    if "size_bytes" in raw:
        return raw.get("size_bytes")
    return raw.get("size")


def _is_transcript_path(path: str) -> bool:
    base = os.path.basename(path).lower()
    if base in _TRANSCRIPT_NAMES:
        return True
    return base.endswith(".jsonl") and "transcript" in base


def _interaction_for_fs(event_type: str, path: str) -> Optional[str]:
    if event_type == "modified" and _is_transcript_path(path):
        return "transcript_appended"
    return _FS_TYPE.get(event_type)


def _fs_detail(path: str, raw: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        "abs_path": path,
        "path": path,
        "extension": os.path.splitext(path)[1] or None,
        "change": raw.get("event_type"),
        "size": _size_of(raw),
    }


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
    }
    if extra:
        row.update(extra)
    return extract_row(row)


def is_fs_event_wire(raw: Mapping[str, Any]) -> bool:
    return "event_type" in raw and bool(_path_of(raw))


def is_claude_transcript_line(raw: Mapping[str, Any]) -> bool:
    if "event_type" in raw:
        return False
    if raw.get("type") in ("user", "assistant", "system"):
        return True
    return isinstance(raw.get("message"), Mapping)


def extract_fs_event_wire(
    raw: Mapping[str, Any],
    when: Optional[datetime],
    *,
    tool: str,
) -> Optional[Dict[str, Any]]:
    """Map one watchdog FS record to curator fields, or None."""
    path = _path_of(raw)
    if not path:
        return None
    interaction = _interaction_for_fs(str(raw.get("event_type") or ""), path)
    if interaction is None:
        return None
    return _via_extract_row(tool, interaction, when, raw, _fs_detail(path, raw))


def _usage_from_line(raw: Mapping[str, Any]) -> Mapping[str, Any]:
    msg = _as_mapping(raw.get("message"))
    usage = msg.get("usage") or raw.get("usage") or {}
    return _as_mapping(usage)


def _transcript_detail(raw: Mapping[str, Any]) -> Dict[str, Any]:
    usage = _usage_from_line(raw)
    msg = _as_mapping(raw.get("message"))
    detail: Dict[str, Any] = {
        "input_tokens": usage.get("input_tokens"),
        "output_tokens": usage.get("output_tokens"),
        "cache_read_tokens": usage.get("cache_read_input_tokens")
        or usage.get("cache_read_tokens"),
    }
    model = msg.get("model") or raw.get("model")
    if model:
        detail["models"] = [str(model)]
    return detail


def _tokens_total(detail: Mapping[str, Any]) -> Optional[int]:
    tin = detail.get("input_tokens") or 0
    tout = detail.get("output_tokens") or 0
    total = int(tin) + int(tout)
    return total or None


_DENY_MARKERS = (
    "permission", "don't want to proceed", "do not want to proceed",
    "user denied", "denied", "not allowed", "rejected", "blocked by",
)


def _tool_result_from_line(raw: Mapping[str, Any]):
    """If a transcript 'user' line carries a tool_result block, return
    (tool_use_id, is_error, text); else None. The transcript records the real
    tool outcome (including is_error on denials) that the hook stream omits."""
    msg = _as_mapping(raw.get("message"))
    content = msg.get("content")
    if not isinstance(content, list):
        return None
    for b in content:
        if isinstance(b, dict) and b.get("type") == "tool_result":
            c = b.get("content")
            if isinstance(c, list):
                txt = " ".join(
                    str(x.get("text", "")) if isinstance(x, dict) else str(x)
                    for x in c
                )
            else:
                txt = str(c) if c is not None else ""
            return (b.get("tool_use_id"), bool(b.get("is_error")), txt)
    return None


def extract_claude_transcript_line(
    raw: Mapping[str, Any],
    when: Optional[datetime],
    *,
    tool: str = "claude-code",
) -> Optional[Dict[str, Any]]:
    """Map one Claude transcript JSONL object to transcript_appended."""
    if not is_claude_transcript_line(raw):
        return None
    detail = _transcript_detail(raw)
    if raw.get("cwd"):
        detail["cwd"] = raw.get("cwd")
    # The transcript carries agent identity/context the hooks omit: the client
    # version (tool_version) and the active git branch. Surface them so they
    # stop being Bucket-B "needs collection" fields for claude-code.
    extra: Dict[str, Any] = {"tokens_total": _tokens_total(detail)}
    if raw.get("version"):
        extra["tool_version"] = raw.get("version")
    if raw.get("gitBranch"):
        extra["git_branch"] = raw.get("gitBranch")
    # A 'user' line carrying a tool_result enriches the matching tool observable
    # (merged by tool_use_id) with the real outcome the hook stream lacks:
    # success/error, the result text, and permission-denial signals.
    tr = _tool_result_from_line(raw)
    if tr:
        tuid, is_err, txt = tr
        if tuid:
            extra["tool_use_id"] = tuid
        if txt:
            detail["tool_result"] = {"output": txt[:4000]}
        if is_err:
            extra["success"] = False
            extra["error_type"] = (txt[:200] or "tool_error")
            low = txt.lower()
            if any(m in low for m in _DENY_MARKERS):
                extra["permission_decision"] = "deny"
                extra["denial_reason"] = txt[:200]
        else:
            extra["success"] = True
    return _via_extract_row(
        tool,
        "transcript_appended",
        when,
        raw,
        detail,
        extra,
    )


def extract_fs_watcher(
    raw: Mapping[str, Any],
    when: Optional[datetime],
    *,
    tool: str,
) -> Optional[Dict[str, Any]]:
    """Dispatch FS watcher raw_payload by shape."""
    if is_fs_event_wire(raw):
        return extract_fs_event_wire(raw, when, tool=tool)
    if tool == "claude-code" and is_claude_transcript_line(raw):
        return extract_claude_transcript_line(raw, when, tool=tool)
    return None
