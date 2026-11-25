"""Merge Antigravity named-hook-block config without dropping user blocks."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

from collectors.cursor.hook_settings import forwarder_command, restore_hooks_file

SIGNAL_BLOCK = "signal-live"
_GROUPED = frozenset({"PreToolUse", "PostToolUse"})

__all__ = [
    "SIGNAL_BLOCK",
    "forwarder_command",
    "install_antigravity_command_hooks",
    "merge_antigravity_command_hooks",
    "restore_hooks_file",
]


def _command_handler(command: str) -> Dict[str, str]:
    return {"type": "command", "command": command}


def _grouped_block(command: str) -> List[dict]:
    return [{"matcher": "*", "hooks": [_command_handler(command)]}]


def _flat_block(command: str) -> List[dict]:
    return [_command_handler(command)]


def _has_command(value: Any, command: str) -> bool:
    text = json.dumps(value)
    return command in text


def _event_value(name: str, existing: Any, command: str) -> Any:
    if existing and _has_command(existing, command):
        return existing
    if name in _GROUPED:
        return _grouped_block(command)
    return _flat_block(command)


def merge_antigravity_command_hooks(
    settings: Mapping[str, Any],
    command: str,
    events: Sequence[str],
) -> Dict[str, Any]:
    out = copy.deepcopy(dict(settings))
    block = dict(out.get(SIGNAL_BLOCK) or {})
    block["enabled"] = True
    for name in events:
        block[name] = _event_value(name, block.get(name), command)
    out[SIGNAL_BLOCK] = block
    return out


def install_antigravity_command_hooks(
    path: Path, command: str, events: Sequence[str]
) -> Optional[bytes]:
    backup = path.read_bytes() if path.is_file() else None
    current: Dict[str, Any] = {}
    if backup:
        loaded = json.loads(backup.decode("utf-8"))
        current = loaded if isinstance(loaded, dict) else {}
    merged = merge_antigravity_command_hooks(current, command, events)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(merged, indent=2) + "\n", encoding="utf-8")
    return backup
