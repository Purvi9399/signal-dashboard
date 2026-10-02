"""Merge Cursor command hooks into hooks.json without dropping user hooks.

See package/LIVE_HOOKS.md FR-H5 / FR-H6 / FR-H13.
"""

from __future__ import annotations

import copy
import json
import shlex
import sys
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Sequence

_FORWARDER = Path(__file__).resolve().with_name("forward_hook.py")


def forwarder_command(url: str, forwarder: Optional[Path] = None) -> str:
    script = str(forwarder or _FORWARDER)
    return " ".join(shlex.quote(part) for part in (sys.executable, script, url))


def _has_command(blocks: Sequence[Any], command: str) -> bool:
    return any(isinstance(b, Mapping) and b.get("command") == command for b in blocks)


def _with_command(blocks: list, command: str) -> list:
    if _has_command(blocks, command):
        return blocks
    blocks.append({"command": command})
    return blocks


def _blocks_for_event(hooks: Dict[str, Any], name: str) -> list:
    existing = hooks.get(name) or []
    if isinstance(existing, list):
        return list(existing)
    return []


def merge_cursor_command_hooks(
    settings: Mapping[str, Any],
    command: str,
    events: Sequence[str],
) -> Dict[str, Any]:
    out = copy.deepcopy(dict(settings))
    out["version"] = out.get("version") or 1
    hooks = dict(out.get("hooks") or {})
    for name in events:
        hooks[name] = _with_command(_blocks_for_event(hooks, name), command)
    out["hooks"] = hooks
    return out


def install_cursor_command_hooks(
    path: Path, command: str, events: Sequence[str]
) -> Optional[bytes]:
    backup = path.read_bytes() if path.is_file() else None
    current: Dict[str, Any] = {}
    if backup:
        loaded = json.loads(backup.decode("utf-8"))
        current = loaded if isinstance(loaded, dict) else {}
    merged = merge_cursor_command_hooks(current, command, events)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(merged, indent=2) + "\n", encoding="utf-8")
    return backup


def restore_hooks_file(path: Path, backup: Optional[bytes]) -> None:
    if backup is None:
        if path.is_file():
            path.unlink()
        return
    path.write_bytes(backup)
