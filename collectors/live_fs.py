"""Shared live filesystem Observer + feed (LIVE_FS.md)."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, List, Optional, Sequence

from collectors.live_http import LiveEventFeed

try:
    from watchdog.events import FileSystemEventHandler
    from watchdog.observers.polling import PollingObserver as Observer

    WATCHDOG_AVAILABLE = True
except ImportError:
    WATCHDOG_AVAILABLE = False
    FileSystemEventHandler = object  # type: ignore[assignment,misc]
    Observer = None  # type: ignore[assignment]

ExtraFn = Callable[[str, str], Optional[dict]]
TailFn = Callable[[str], Optional[List[dict]]]


def require_watchdog() -> None:
    if WATCHDOG_AVAILABLE:
        return
    raise RuntimeError(
        "live filesystem watching requires the third-party `watchdog` package"
    )


def safe_size(path: str) -> Optional[int]:
    try:
        return os.path.getsize(path)
    except OSError:
        return None


def fs_record(
    event_type: str,
    path: str,
    is_directory: bool,
    extra: Optional[dict] = None,
) -> dict:
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "event_type": event_type,
        "path": path,
        "is_directory": is_directory,
        "size_bytes": None if is_directory else safe_size(path),
    }
    if extra:
        record.update(extra)
    return record


def existing_dirs(roots: Sequence[str], *, mkdir: bool = False) -> List[str]:
    out: List[str] = []
    for root in roots:
        path = Path(root)
        if mkdir:
            path.mkdir(parents=True, exist_ok=True)
        if path.is_dir():
            out.append(str(path))
    return out


class QueuedFSHandler(FileSystemEventHandler):
    """Push generic FS records (and optional transcript lines) onto a feed."""

    def __init__(
        self,
        feed: LiveEventFeed,
        extra_for: Optional[ExtraFn] = None,
        tail_file: Optional[TailFn] = None,
    ) -> None:
        self._feed = feed
        self._extra_for = extra_for
        self._tail_file = tail_file

    def _put_fs(self, event_type: str, src, extra: Optional[dict] = None) -> None:
        more = dict(extra or {})
        if self._extra_for is not None:
            added = self._extra_for(event_type, src.src_path)
            if added:
                more.update(added)
        self._feed.put(
            fs_record(event_type, src.src_path, src.is_directory, more or None)
        )

    def _try_tail(self, src) -> bool:
        if src.is_directory or self._tail_file is None:
            return False
        lines = self._tail_file(src.src_path)
        if lines is None:
            return False
        for line in lines:
            self._feed.put(line)
        return True

    def on_created(self, event) -> None:
        self._put_fs("created", event)
        self._try_tail(event)

    def on_modified(self, event) -> None:
        if self._try_tail(event):
            return
        self._put_fs("modified", event)

    def on_deleted(self, event) -> None:
        self._put_fs("deleted", event)

    def on_moved(self, event) -> None:
        self._put_fs("moved", event, extra={"dest_path": event.dest_path})


class LiveFSWatch:
    """Start/stop one Observer; stop() also ends the feed."""

    def __init__(self, feed: LiveEventFeed, handler: QueuedFSHandler) -> None:
        self._feed = feed
        self._handler = handler
        self._observer = None

    def start(self, roots: Sequence[str], *, mkdir: bool = False) -> None:
        require_watchdog()
        observer = Observer(timeout=0.25)
        for root in existing_dirs(roots, mkdir=mkdir):
            observer.schedule(self._handler, root, recursive=True)
        observer.start()
        self._observer = observer

    def stop(self) -> None:
        observer = self._observer
        self._observer = None
        if observer is not None:
            observer.stop()
            observer.join(timeout=2.0)
        self._feed.stop()
