"""Merge Codex plain-string command hooks without dropping user hooks."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Sequence

from collectors.cursor.hook_settings import forwarder_command, restore_hooks_file

__all__ = [
    "forwarder_command",
    "install_codex_command_hooks",
    "merge_codex_command_hooks",
    "restore_hooks_file",
]


def _has_command(blocks: Sequence[Any], command: str) -> bool:
    if command in blocks:
        return True
    return any(isinstance(b, Mapping) and b.get("command") == command for b in blocks)


def _extend_list(existing: list, command: str) -> list:
    if _has_command(existing, command):
        return list(existing)
    out = list(existing)
    out.append(command)
    return out


def _codex_value(existing: Any, command: str) -> Any:
    if existing in (None, ""):
        return command
    if existing == command:
        return existing
    if isinstance(existing, list):
        return _extend_list(existing, command)
    return existing


def merge_codex_command_hooks(
    settings: Mapping[str, Any],
    command: str,
    events: Sequence[str],
) -> Dict[str, Any]:
    out = copy.deepcopy(dict(settings))
    hooks = dict(out.get("hooks") or {})
    for name in events:
        hooks[name] = _codex_value(hooks.get(name), command)
    out["hooks"] = hooks
    return out


def install_codex_command_hooks(
    path: Path, command: str, events: Sequence[str]
) -> Optional[bytes]:
    backup = path.read_bytes() if path.is_file() else None
    current: Dict[str, Any] = {}
    if backup:
        loaded = json.loads(backup.decode("utf-8"))
        current = loaded if isinstance(loaded, dict) else {}
    merged = merge_codex_command_hooks(current, command, events)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(merged, indent=2) + "\n", encoding="utf-8")
    return backup
