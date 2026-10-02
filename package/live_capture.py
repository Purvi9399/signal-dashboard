"""Drain live merged_capture until deactivate (LIVE_HOOKS.md FR-H7 / FR-H8)."""

from __future__ import annotations

import signal
import threading
from typing import Any, List, Optional


def drain_until_stop(registry: Any, listen_seconds: Optional[float] = None) -> List[Any]:
    events: List[Any] = []
    stopped = threading.Event()

    def halt(_signum=None, _frame=None) -> None:
        if stopped.is_set():
            return
        stopped.set()
        registry.deactivate_all()

    signal.signal(signal.SIGINT, halt)
    signal.signal(signal.SIGTERM, halt)
    timer = _start_timer(halt, listen_seconds)
    try:
        for ev in registry.merged_capture():
            events.append(ev)
    finally:
        _cancel_timer(timer)
    return events


def _start_timer(halt, listen_seconds: Optional[float]):
    if listen_seconds is None:
        return None
    timer = threading.Timer(listen_seconds, halt)
    timer.daemon = True
    timer.start()
    return timer


def _cancel_timer(timer) -> None:
    if timer is not None:
        timer.cancel()
