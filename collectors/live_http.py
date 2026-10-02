"""Shared live HTTP feed for hook collectors (LIVE_HOOKS.md).

One listener per (host, port). Paths route to per-collector feeds.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from queue import Queue
from typing import Any, Dict, Iterator, Optional, Tuple

_STOP = object()
_LOCK = threading.Lock()
_MUXES: Dict[Tuple[str, int], "HookMux"] = {}


class LiveEventFeed:
    """Blocking iterable of hook JSON dicts. stop() ends iteration."""

    def __init__(self) -> None:
        self._q: Queue = Queue()

    def put(self, payload: dict) -> None:
        self._q.put(payload)

    def stop(self) -> None:
        self._q.put(_STOP)

    def __iter__(self) -> Iterator[dict]:
        while True:
            item = self._q.get()
            if item is _STOP:
                return
            yield item


def _payload_from_body(raw_body: bytes) -> Optional[dict]:
    try:
        body = json.loads(raw_body.decode("utf-8") or "null")
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    if isinstance(body, dict):
        return body
    return None


class _MuxHandler(BaseHTTPRequestHandler):
    routes: Dict[str, LiveEventFeed]

    def log_message(self, fmt: str, *args: Any) -> None:
        return

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", "0") or 0)
        raw_body = self.rfile.read(length) if length else b""
        feed = self.routes.get(self.path.split("?", 1)[0])
        if feed is not None:
            payload = _payload_from_body(raw_body)
            if payload is not None:
                feed.put(payload)
        body = b"{}"
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class HookMux:
    def __init__(self, host: str, port: int) -> None:
        self.routes: Dict[str, LiveEventFeed] = {}
        handler = type("BoundMuxHandler", (_MuxHandler,), {"routes": self.routes})
        self._httpd = ThreadingHTTPServer((host, port), handler)
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)

    @property
    def port(self) -> int:
        return int(self._httpd.server_address[1])

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()


def attach_hook_route(host: str, port: int, path: str, feed: LiveEventFeed) -> int:
    """Register path on the shared listener. Return the bound port."""
    with _LOCK:
        mux = _MUXES.get((host, port))
        if mux is None:
            mux = HookMux(host, port)
            mux.start()
            _MUXES[(host, mux.port)] = mux
        mux.routes[path] = feed
        return mux.port


def detach_hook_route(host: str, port: int, path: str) -> None:
    with _LOCK:
        mux = _MUXES.get((host, port))
        if mux is None:
            return
        mux.routes.pop(path, None)
        if mux.routes:
            return
        mux.stop()
        _MUXES.pop((host, port), None)


class HookHTTPServer:
    """Per-collector handle: attach one path, stop that feed on shutdown."""

    def __init__(self, host: str, port: int, webhook_path: str, feed: LiveEventFeed) -> None:
        self._host = host
        self._port = port
        self._path = webhook_path
        self._feed = feed
        self._bound: Optional[int] = None

    @property
    def port(self) -> int:
        if self._bound is None:
            return self._port
        return self._bound

    def start(self) -> int:
        self._bound = attach_hook_route(self._host, self._port, self._path, self._feed)
        return self._bound

    def stop(self) -> None:
        if self._bound is None:
            return
        detach_hook_route(self._host, self._bound, self._path)
        self._bound = None
        self._feed.stop()
