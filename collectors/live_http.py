"""Shared live HTTP feed for hook collectors (LIVE_HOOKS.md)."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from queue import Queue
from typing import Any, Dict, Iterator, Optional

_STOP = object()


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


class _HookHandler(BaseHTTPRequestHandler):
    feed: LiveEventFeed
    webhook_path: str

    def log_message(self, fmt: str, *args: Any) -> None:
        return

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        length = int(self.headers.get("Content-Length", "0") or 0)
        raw_body = self.rfile.read(length) if length else b""
        if self.path == self.webhook_path:
            payload = _payload_from_body(raw_body)
            if payload is not None:
                self.feed.put(payload)
        body = b"{}"
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class HookHTTPServer:
    def __init__(self, host: str, port: int, webhook_path: str, feed: LiveEventFeed) -> None:
        self._host = host
        self._port = port
        self._path = webhook_path
        self._feed = feed
        self._httpd: Optional[ThreadingHTTPServer] = None
        self._thread: Optional[threading.Thread] = None

    @property
    def port(self) -> int:
        if self._httpd is None:
            return self._port
        return int(self._httpd.server_address[1])

    def start(self) -> int:
        handler = type(
            "BoundHookHandler",
            (_HookHandler,),
            {"feed": self._feed, "webhook_path": self._path},
        )
        self._httpd = ThreadingHTTPServer((self._host, self._port), handler)
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()
        return self.port

    def stop(self) -> None:
        if self._httpd is None:
            return
        self._httpd.shutdown()
        self._httpd.server_close()
        self._httpd = None
        self._feed.stop()
