"""Merge Claude Code HTTP hooks into settings.json without dropping user hooks.

See package/LIVE_HOOKS.md FR-H5 / FR-H6.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Sequence

_HTTP = "http"


def _http_entry(url: str) -> Dict[str, str]:
    return {"type": _HTTP, "url": url}


def _hook_url(hook: Any) -> Optional[str]:
    if not isinstance(hook, Mapping):
        return None
    url = hook.get("url")
    return str(url) if url else None


def _block_has_url(block: Any, url: str) -> bool:
    if not isinstance(block, Mapping):
        return False
    return any(_hook_url(hook) == url for hook in (block.get("hooks") or []))


def _blocks_have_url(blocks: Sequence[Any], url: str) -> bool:
    return any(_block_has_url(block, url) for block in blocks)


def _with_http_block(blocks: list, url: str) -> list:
    if _blocks_have_url(blocks, url):
        return blocks
    blocks.append({"matcher": "*", "hooks": [_http_entry(url)]})
    return blocks


def merge_claude_http_hooks(
    settings: Mapping[str, Any],
    url: str,
    events: Sequence[str],
) -> Dict[str, Any]:
    """Return a copy of settings with HTTP hooks for each event name."""
    out = copy.deepcopy(dict(settings))
    hooks = dict(out.get("hooks") or {})
    for name in events:
        existing = hooks.get(name) or []
        blocks = list(existing) if isinstance(existing, list) else []
        hooks[name] = _with_http_block(blocks, url)
    out["hooks"] = hooks
    return out


def install_claude_http_hooks(path: Path, url: str, events: Sequence[str]) -> Optional[bytes]:
    """Write merged settings. Return previous file bytes, or None if new."""
    backup = path.read_bytes() if path.is_file() else None
    current: Dict[str, Any] = {}
    if backup:
        current = json.loads(backup.decode("utf-8"))
        if not isinstance(current, dict):
            current = {}
    merged = merge_claude_http_hooks(current, url, events)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(merged, indent=2) + "\n", encoding="utf-8")
    return backup


def restore_settings_file(path: Path, backup: Optional[bytes]) -> None:
    if backup is None:
        if path.is_file():
            path.unlink()
        return
    path.write_bytes(backup)
