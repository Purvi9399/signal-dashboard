"""Thin hooks extractors for Codex / Copilot RawEvent payloads (Phase 1).

Claude/Cursor use Purvi extractors in signal_curate.py. Antigravity stays
in adapter_source_events. This module only covers Codex + Copilot.
"""

from __future__ import annotations

import os
from datetime import datetime
from typing import Any, Dict, Mapping, Optional

_NATIVE_HOOK_TYPE = {
    "SessionStart": "session_start",
    "SessionEnd": "session_end",
    "UserPromptSubmit": "user_prompt",
    "UserPromptSubmitted": "user_prompt",
    "PreToolUse": "tool_request",
    "PostToolUse": "tool_result",
    "PostToolUseFailure": "tool_failure",
    "PermissionRequest": "permission_request",
    "permissionRequest": "permission_request",
    "SubagentStart": "subagent_start",
    "SubagentStop": "subagent_stop",
    "Stop": "agent_response",
    "AgentStop": "agent_response",
    "agentStop": "agent_response",
    "PreCompact": "compaction",
    "Notification": "notification",
    "sessionStart": "session_start",
    "sessionEnd": "session_end",
    "userPromptSubmitted": "user_prompt",
    "preToolUse": "tool_request",
    "postToolUse": "tool_result",
    "postToolUseFailure": "tool_failure",
    "subagentStart": "subagent_start",
    "subagentStop": "subagent_stop",
}

_CODEX_IDE_TYPE = {
    "activation": "session_start",
    "environment_snapshot": "session_start",
    "onDidSaveTextDocument": "file_modify",
    "onDidChangeTextDocument": "file_modify",
    "onDidOpenTextDocument": "file_read",
    "onDidCloseTextDocument": "file_modify",
    "fsWatcher_create": "file_create",
    "fsWatcher_change": "file_modify",
    "fsWatcher_delete": "file_delete",
    "probable_accept": "agent_response",
    "probable_undo": "file_revert",
    "accept_correlation": "agent_response",
}


def _session(raw: Mapping[str, Any]) -> Optional[str]:
    for key in ("session_id", "sessionId", "conversation_id", "conversationId"):
        value = raw.get(key)
        if value:
            return str(value)
    return None


def _tool_input(raw: Mapping[str, Any]) -> Mapping[str, Any]:
    tin = raw.get("tool_input") or raw.get("toolInput") or {}
    if isinstance(tin, Mapping):
        return tin
    return {"raw": tin} if tin else {}


def _infer_shape_a_name(raw: Mapping[str, Any]) -> str:
    if raw.get("initialPrompt") is not None:
        return "sessionStart"
    if raw.get("transformedPrompt") is not None:
        return "userPromptTransformed"
    if raw.get("stopReason") is not None:
        return "agentStop"
    if raw.get("prompt") is not None:
        return "userPromptSubmitted"
    return ""


def _hook_name(raw: Mapping[str, Any]) -> str:
    if raw.get("hook_event_name"):
        return str(raw["hook_event_name"])
    if raw.get("hookName"):
        return str(raw["hookName"])
    return _infer_shape_a_name(raw)


def _path_from(raw: Mapping[str, Any], tin: Mapping[str, Any]) -> Optional[str]:
    path = tin.get("file_path") or tin.get("path") or raw.get("file_path")
    return str(path) if path else None


def _native_fields(
    *,
    tool: str,
    raw: Mapping[str, Any],
    when: Optional[datetime],
    event_name: str,
) -> Optional[Dict[str, Any]]:
    otype = _NATIVE_HOOK_TYPE.get(event_name)
    if not otype:
        return None
    tin = _tool_input(raw)
    path = _path_from(raw, tin)
    cmd = tin.get("command") or raw.get("command")
    session = _session(raw)
    return {
        "tool": tool,
        "occurred_at": when,
        "observable_type": otype,
        "session_id": session,
        "conversation_id": session,
        "tool_name": raw.get("tool_name") or raw.get("toolName"),
        "tool_arguments": dict(tin) if tin else None,
        "command": str(cmd) if cmd else None,
        "file_path": path,
        "file_extension": os.path.splitext(path)[1] if path else None,
        "cwd": raw.get("cwd"),
        "prompt_text": raw.get("prompt") or raw.get("initialPrompt"),
        "model": raw.get("model"),
    }


def _ide_fields(
    raw: Mapping[str, Any], when: Optional[datetime], ide: str
) -> Dict[str, Any]:
    path = raw.get("path") or raw.get("fsPath") or raw.get("fileName")
    path_s = str(path) if path else None
    session = _session(raw)
    return {
        "tool": "codex",
        "occurred_at": when,
        "observable_type": _CODEX_IDE_TYPE[ide],
        "session_id": session,
        "conversation_id": session,
        "file_path": path_s,
        "file_extension": os.path.splitext(path_s)[1] if path_s else None,
    }


def extract_codex_hook(
    raw: Mapping[str, Any], when: Optional[datetime]
) -> Optional[Dict[str, Any]]:
    ide = raw.get("type")
    if isinstance(ide, str) and ide in _CODEX_IDE_TYPE:
        return _ide_fields(raw, when, ide)
    return _native_fields(tool="codex", raw=raw, when=when, event_name=_hook_name(raw))


def extract_copilot_hook(
    raw: Mapping[str, Any], when: Optional[datetime]
) -> Optional[Dict[str, Any]]:
    name = _hook_name(raw)
    if name == "userPromptTransformed":
        session = _session(raw)
        return {
            "tool": "copilot",
            "occurred_at": when,
            "observable_type": "user_prompt",
            "session_id": session,
            "conversation_id": session,
            "prompt_text": raw.get("transformedPrompt") or raw.get("prompt"),
        }
    return _native_fields(tool="copilot", raw=raw, when=when, event_name=name)
