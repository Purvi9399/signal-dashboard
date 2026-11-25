"""Merge Copilot HTTP hooks (both casings) without dropping user hooks."""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Sequence

_HTTP = "http"


def apply_required_env(required: Mapping[str, str]) -> Dict[str, Optional[str]]:
    previous: Dict[str, Optional[str]] = {}
    for key, value in required.items():
        previous[key] = os.environ.get(key)
        os.environ[key] = value
    return previous


def restore_env(previous: Mapping[str, Optional[str]]) -> None:
    for key, old in previous.items():
        if old is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = old


def _http_entry(url: str) -> Dict[str, str]:
    return {"type": _HTTP, "url": url}


def _has_url(blocks: Sequence[Any], url: str) -> bool:
    return any(isinstance(b, Mapping) and b.get("url") == url for b in blocks)


def _with_http(blocks: list, url: str) -> list:
    if _has_url(blocks, url):
        return blocks
    blocks.append(_http_entry(url))
    return blocks


def _blocks_for_event(hooks: Dict[str, Any], name: str) -> list:
    existing = hooks.get(name) or []
    if isinstance(existing, list):
        return list(existing)
    return []


def event_url(base: str, name: str) -> str:
    return f"{base}?hook={name}"


def merge_copilot_http_hooks(
    settings: Mapping[str, Any],
    base_url: str,
    events: Sequence[str],
) -> Dict[str, Any]:
    out = copy.deepcopy(dict(settings))
    hooks = dict(out.get("hooks") or {})
    for name in events:
        url = event_url(base_url, name)
        hooks[name] = _with_http(_blocks_for_event(hooks, name), url)
    out["hooks"] = hooks
    return out


def install_copilot_http_hooks(
    path: Path, base_url: str, events: Sequence[str]
) -> Optional[bytes]:
    backup = path.read_bytes() if path.is_file() else None
    current: Dict[str, Any] = {}
    if backup:
        loaded = json.loads(backup.decode("utf-8"))
        current = loaded if isinstance(loaded, dict) else {}
    merged = merge_copilot_http_hooks(current, base_url, events)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(merged, indent=2) + "\n", encoding="utf-8")
    return backup


def restore_hooks_file(path: Path, backup: Optional[bytes]) -> None:
    if backup is None:
        if path.is_file():
            path.unlink()
        return
    path.write_bytes(backup)
