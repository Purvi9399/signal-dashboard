"""Shared live stdio MCP shuttle (LIVE_MCP.md)."""

from __future__ import annotations

import json
import subprocess
import sys
import threading
from datetime import datetime, timezone
from typing import BinaryIO, List, Optional, Sequence

from collectors.live_http import LiveEventFeed

CLIENT_TO_SERVER = "client_to_server"
SERVER_TO_CLIENT = "server_to_client"


def build_log_record(direction: str, raw_line: str) -> dict:
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "direction": direction,
        "raw": raw_line,
    }
    try:
        record["parsed"] = json.loads(raw_line)
    except json.JSONDecodeError:
        record["parsed"] = None
    return record


def pump_line(src, dst, direction: str, feed: LiveEventFeed) -> None:
    for raw_line in iter(src.readline, b""):
        if not raw_line:
            break
        decoded = raw_line.decode("utf-8", errors="replace").rstrip("\n")
        if decoded.strip():
            feed.put(build_log_record(direction, decoded))
        dst.write(raw_line)
        dst.flush()


class LiveStdioProxy:
    """Spawn an MCP stdio server and shuttle+log both directions."""

    def __init__(
        self,
        command: Sequence[str],
        feed: LiveEventFeed,
        client_in: Optional[BinaryIO] = None,
        client_out: Optional[BinaryIO] = None,
    ) -> None:
        self._command = list(command)
        self._feed = feed
        self._client_in = client_in
        self._client_out = client_out
        self._proc: Optional[subprocess.Popen] = None
        self._threads: List[threading.Thread] = []

    def start(self) -> None:
        self._proc = subprocess.Popen(
            self._command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=sys.stderr,
        )
        cin = self._client_in if self._client_in is not None else sys.stdin.buffer
        cout = self._client_out if self._client_out is not None else sys.stdout.buffer
        self._threads = [
            self._start_pump(cin, self._proc.stdin, CLIENT_TO_SERVER),
            self._start_pump(self._proc.stdout, cout, SERVER_TO_CLIENT),
        ]

    def _start_pump(self, src, dst, direction: str) -> threading.Thread:
        thread = threading.Thread(
            target=pump_line, args=(src, dst, direction, self._feed), daemon=True
        )
        thread.start()
        return thread

    def stop(self) -> None:
        proc = self._proc
        self._proc = None
        if proc is None:
            self._feed.stop()
            return
        try:
            proc.terminate()
        except OSError:
            pass
        self._close_pipes(proc)
        self._feed.stop()

    def _close_pipes(self, proc) -> None:
        for stream in (proc.stdin, proc.stdout):
            if stream is None:
                continue
            try:
                stream.close()
            except OSError:
                pass
