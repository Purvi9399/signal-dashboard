"""Drain live collectors until deactivate (LIVE_HOOKS.md FR-H7 / FR-H8).

Each active collector is pumped on its own thread. Round-robin
merged_capture() blocks on the first empty LiveEventFeed and never
starts cursor.hooks capture().
"""

from __future__ import annotations

import signal
import threading
from queue import Queue
from typing import Any, Callable, List, Optional

from package.session_end import is_session_end

OnSession = Callable[[List[Any]], None]
_DONE = object()


def drain_until_stop(
    registry: Any,
    listen_seconds: Optional[float] = None,
    on_session: Optional[OnSession] = None,
) -> List[Any]:
    events: List[Any] = []
    pending: List[Any] = []
    stopped = threading.Event()
    outbound: Queue = Queue()

    def halt(_signum=None, _frame=None) -> None:
        if stopped.is_set():
            return
        stopped.set()
        registry.deactivate_all()

    workers = _start_pumps(registry, outbound)
    signal.signal(signal.SIGINT, halt)
    signal.signal(signal.SIGTERM, halt)
    timer = _start_timer(halt, listen_seconds)
    try:
        _collect_from_queue(outbound, workers, events, pending, on_session)
    finally:
        _cancel_timer(timer)
        halt()
        _flush_pending(pending, on_session)
    return events


def _collectors(registry: Any) -> List[Any]:
    iterate = getattr(registry, "iter_active", None)
    if iterate is not None:
        return list(iterate())
    return []


def _start_pumps(registry: Any, outbound: Queue) -> int:
    count = 0
    for collector in _collectors(registry):
        worker = threading.Thread(
            target=_pump,
            args=(registry, collector, outbound),
            daemon=True,
        )
        worker.start()
        count += 1
    return count


def _pump(registry: Any, collector: Any, outbound: Queue) -> None:
    try:
        for event in _capture_iter(registry, collector):
            outbound.put(event)
    finally:
        outbound.put(_DONE)


def _capture_iter(registry: Any, collector: Any):
    safe = getattr(registry, "_safe_capture_stream", None)
    if safe is not None:
        return safe(collector)
    return collector.capture()


def _collect_from_queue(
    outbound: Queue,
    workers: int,
    events: List[Any],
    pending: List[Any],
    on_session: Optional[OnSession],
) -> None:
    remaining = workers
    while remaining > 0:
        item = outbound.get()
        if item is _DONE:
            remaining -= 1
            continue
        _accept_event(item, events, pending, on_session)


def _accept_event(
    event: Any,
    events: List[Any],
    pending: List[Any],
    on_session: Optional[OnSession],
) -> None:
    events.append(event)
    pending.append(event)
    if on_session is None or not is_session_end(event):
        return
    on_session(list(pending))
    pending.clear()


def _flush_pending(pending: List[Any], on_session: Optional[OnSession]) -> None:
    if on_session is None or not pending:
        return
    on_session(list(pending))
    pending.clear()


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
