"""Adapt Project Signal RawEvent / source_events rows into curator loaded tuples.

Output shape matches colleague signal_curate.load_all entries:
  (collector_code, flat_fields, raw_payload)

Phase 1: hooks / local_hooks for antigravity, claude-code, cursor, codex, copilot.
Phase 2: mcp_proxy (stdio JSON-RPC + Cursor network token capture).
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from curate.extract_codex_copilot import extract_codex_hook, extract_copilot_hook
from curate.extract_mcp_stdio import (
    extract_cursor_network_proxy,
    extract_mcp_stdio_wire,
    is_stdio_mcp_wire,
)
from curate.signal_curate import extract_claude_hook, extract_cursor_hook
from export.mapping import map_collector, map_tool

LoadedTuple = Tuple[str, Dict[str, Any], Any]

_FILE_WRITE_TOOLS = frozenset({"write_to_file", "replace_file_content"})
_HOOKS = "local_hooks"
_MCP = "mcp_proxy"


def _as_mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _parse_when(timestamp: Any) -> Optional[datetime]:
    if isinstance(timestamp, datetime):
        return timestamp
    if not timestamp:
        return None
    try:
        return datetime.fromisoformat(str(timestamp).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def _first_workspace(paths: Any) -> Optional[str]:
    if not isinstance(paths, Sequence) or isinstance(paths, (str, bytes)):
        return None
    if not paths:
        return None
    first = paths[0]
    return str(first) if first else None


def _workspace(raw: Mapping[str, Any]) -> Optional[str]:
    from_list = _first_workspace(raw.get("workspacePaths"))
    if from_list:
        return from_list
    value = raw.get("workspace") or raw.get("cwd")
    return str(value) if value else None


def _file_type(args: Mapping[str, Any]) -> str:
    action = str(args.get("toolAction") or "").lower()
    if "creat" in action:
        return "file_create"
    if args.get("Overwrite") is False:
        return "file_create"
    return "file_modify"


def _tool_call_type(name: str, args: Mapping[str, Any], raw: Mapping[str, Any]) -> str:
    if name in _FILE_WRITE_TOOLS:
        return _file_type(args)
    if raw.get("error") not in (None, ""):
        return "tool_result"
    return "tool_request"


def _apply_path_fields(fields: Dict[str, Any], args: Mapping[str, Any]) -> None:
    path = args.get("TargetFile") or args.get("AbsolutePath") or args.get("path")
    if not path:
        return
    fields["file_path"] = str(path)
    fields["file_extension"] = os.path.splitext(str(path))[1] or None


def _apply_command_fields(fields: Dict[str, Any], args: Mapping[str, Any]) -> None:
    cmd = args.get("CommandLine") or args.get("command")
    if cmd:
        fields["command"] = str(cmd)
    cwd = args.get("Cwd")
    if cwd:
        fields["cwd"] = str(cwd)


def _apply_error_fields(fields: Dict[str, Any], raw: Mapping[str, Any]) -> None:
    err = raw.get("error")
    if err in (None, ""):
        return
    fields["success"] = False
    fields["error_type"] = str(err)[:200]
    fields["observable_type"] = "tool_result"


def _fill_tool_call(fields: Dict[str, Any], raw: Mapping[str, Any]) -> bool:
    tool_call = raw.get("toolCall")
    if not isinstance(tool_call, Mapping):
        return False
    name = str(tool_call.get("name") or "").strip()
    if not name:
        return False
    args = _as_mapping(tool_call.get("args"))
    fields["observable_type"] = _tool_call_type(name, args, raw)
    fields["tool_name"] = name
    fields["tool_arguments"] = dict(args) if args else None
    _apply_path_fields(fields, args)
    _apply_command_fields(fields, args)
    _apply_error_fields(fields, raw)
    return True


def _is_session_start(raw: Mapping[str, Any]) -> bool:
    return "initialNumSteps" in raw or "invocationNum" in raw


def _is_session_end(raw: Mapping[str, Any]) -> bool:
    return bool(raw.get("fullyIdle") or raw.get("terminationReason"))


def _fill_session(fields: Dict[str, Any], raw: Mapping[str, Any]) -> bool:
    if _is_session_start(raw):
        fields["observable_type"] = "session_start"
        return True
    if not _is_session_end(raw):
        return False
    fields["observable_type"] = "session_end"
    reason = raw.get("terminationReason")
    if reason:
        fields["error_type"] = str(reason)
    return True


def _base_fields(
    *,
    tool: str,
    when: Optional[datetime],
    raw: Mapping[str, Any],
    session_hint: Optional[str] = None,
) -> Dict[str, Any]:
    session = session_hint or raw.get("conversationId") or raw.get("session_id")
    session_s = str(session) if session else None
    workspace = _workspace(raw)
    return {
        "tool": tool,
        "occurred_at": when,
        "session_id": session_s,
        "conversation_id": session_s,
        "model": raw.get("modelName") or raw.get("model"),
        "workspace": workspace,
        "cwd": workspace,
        "raw_ref": raw.get("transcriptPath") or raw.get("artifactDirectoryPath"),
    }


def extract_antigravity_hook(
    raw: Mapping[str, Any],
    when: Optional[datetime],
    *,
    session_hint: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Flat fields for antigravity local_hooks payloads, or None if untyped."""
    fields = _base_fields(
        tool="antigravity", when=when, raw=raw, session_hint=session_hint
    )
    if _fill_tool_call(fields, raw):
        return fields
    if _fill_session(fields, raw):
        return fields
    return None


def _ensure_hook_event_name(raw: Mapping[str, Any]) -> Dict[str, Any]:
    payload = dict(raw)
    if not payload.get("hook_event_name") and payload.get("event_type"):
        payload["hook_event_name"] = payload["event_type"]
    return payload


def _extract_claude(raw: Mapping[str, Any], when: Optional[datetime]):
    return extract_claude_hook(_ensure_hook_event_name(raw), when)


def _extract_cursor(raw: Mapping[str, Any], when: Optional[datetime]):
    return extract_cursor_hook(_ensure_hook_event_name(raw), when)


_HOOK_EXTRACTORS = {
    "antigravity": extract_antigravity_hook,
    "claude-code": _extract_claude,
    "cursor": _extract_cursor,
    "codex": extract_codex_hook,
    "copilot": extract_copilot_hook,
}


def _fields_for_hooks(
    tool: str, raw: Mapping[str, Any], when: Optional[datetime]
) -> Optional[Dict[str, Any]]:
    extractor = _HOOK_EXTRACTORS.get(tool)
    if extractor is None:
        return None
    return extractor(raw, when)


def _fields_for_mcp(
    tool: str, raw: Mapping[str, Any], when: Optional[datetime]
) -> Optional[Dict[str, Any]]:
    if is_stdio_mcp_wire(raw):
        return extract_mcp_stdio_wire(raw, when, tool=tool)
    if tool == "cursor":
        return extract_cursor_network_proxy(raw, when)
    return None


def _envelope_parts(
    event: Mapping[str, Any],
) -> Tuple[str, str, Mapping[str, Any], Optional[datetime]]:
    collector_id = str(event.get("collector_id") or "")
    agent = event.get("agent")
    raw = event.get("raw_payload")
    if not isinstance(raw, Mapping):
        raw = {}
    when = _parse_when(event.get("timestamp"))
    tool = map_tool(agent if isinstance(agent, str) else None)
    collector = map_collector(collector_id)
    return tool, collector, raw, when


def _typed_fields(fields: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not fields:
        return None
    otype = fields.get("observable_type")
    if not otype or otype == "other":
        return None
    return fields


def adapt_raw_event(event: Mapping[str, Any]) -> Optional[LoadedTuple]:
    """Map one RawEvent envelope to a curator tuple, or None to skip."""
    tool, collector, raw, when = _envelope_parts(event)
    fields = None
    if collector == _HOOKS:
        fields = _fields_for_hooks(tool, raw, when)
    elif collector == _MCP:
        fields = _fields_for_mcp(tool, raw, when)
    fields = _typed_fields(fields)
    if fields is None:
        return None
    fields["tool"] = tool
    return (collector, fields, raw)


def adapt_source_event_row(row: Mapping[str, Any]) -> Optional[LoadedTuple]:
    """Map a public.source_events-shaped row (payload envelope) to a tuple."""
    payload = row.get("payload")
    if isinstance(payload, Mapping) and (
        "raw_payload" in payload or "collector_id" in payload
    ):
        return adapt_raw_event(payload)
    if "raw_payload" in row:
        return adapt_raw_event(row)
    return None


def _parse_jsonl_line(text: str) -> Optional[LoadedTuple]:
    event = json.loads(text)
    if not isinstance(event, Mapping):
        return None
    return adapt_raw_event(event)


def load_jsonl(path: Path | str) -> List[LoadedTuple]:
    """Read RawEvent JSONL and return curator loaded tuples (skips untyped)."""
    loaded: List[LoadedTuple] = []
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            text = line.strip()
            if not text:
                continue
            triple = _parse_jsonl_line(text)
            if triple is not None:
                loaded.append(triple)
    return loaded


def adapt_source_event_rows(rows: Sequence[Mapping[str, Any]]) -> List[LoadedTuple]:
    out: List[LoadedTuple] = []
    for row in rows:
        triple = adapt_source_event_row(row)
        if triple is not None:
            out.append(triple)
    return out
