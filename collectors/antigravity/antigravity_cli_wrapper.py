"""
Antigravity CLI Wrapper collector.

Grounded in the real, empirical test performed for this project. Primary
sources (read-only, never modified):

  Antigravity_CLIWrapper/RESULTS.md -- full write-up: `agy` (the separate
    Homebrew-cask-installed Antigravity CLI, confirmed distinct from the
    IDE in Antigravity_OTel/RESULTS.md) wrapped with a pty passthrough,
    real captured stdout across --output-format text/json/stream-json.
  Antigravity_CLIWrapper/wrapper.py -- the actual wrapper used: forked from
    cli_wrapper_demo/wrapper.py (generic pty-wrapper mechanism), adapted to
    append captured {timestamp, stream, content, wrapped_command} JSONL
    records to a local file instead of POSTing to an HTTP receiver.
  Antigravity_CLIWrapper/captured_output_json.jsonl -- one real captured
    `--output-format json` run.
  Antigravity_CLIWrapper/captured_output_streamjson.jsonl,
  captured_output_full_streamjson.jsonl -- real captured
    `--output-format stream-json` runs (per-step + final result events).
  /Users/malharshinde/Desktop/SOAISEC/Project_Signal/cli_wrapper_demo/
    wrapper.py, receiver.py, README.md -- the generic, product-agnostic
    pty-wrapper + local-JSONL-sink pattern this collector's activate()/
    capture() lifecycle is adapted from (receiver.py's ingest-and-append
    shape is mirrored here as capture()'s file-tailing behavior instead of
    an HTTP server, since this collector owns its own subprocess directly
    rather than being fed externally).

HEADLINE FINDING this collector exists to capture (RESULTS.md section 4):
`agy --output-format json` / `stream-json` print a full structured usage
object on every run -- exact input/output/thinking/cache_read token counts
and per-step duration_seconds -- with zero extra flags or auth changes.
This closes most of the token/cost/latency gap the OTel collector left
open (antigravity_otel.py: confirmed architecturally dead).

IMPORTANT SHAPE NOTE: the wrapper's real captured JSONL is NOT
one-JSON-object-per-agy-event. Each captured line is
{timestamp, stream, content, wrapped_command} where `content` is a raw
chunk of pty output text -- `agy`'s own JSON events can and do arrive
split across multiple pty chunks (confirmed directly in
captured_output_streamjson.jsonl: the `init` event's tool list is split
across two consecutive captured lines, see RESULTS.md-adjacent raw file).
raw_payload passes through the wrapper's own captured record UNCHANGED
(no reassembly, no re-parsing) per the framework's no-normalization
constraint -- reassembling `content` chunks into complete `agy` JSON
events is a later pipeline stage's job, not this collector's.
"""

from __future__ import annotations

import datetime
import json
import os
import select
import shutil
import signal
import struct
import sys
import threading
from pathlib import Path
from queue import Queue
from typing import Any, Iterator, List, Optional

# fcntl/pty/termios are POSIX-only; importing them at module level (as this
# file originally did) raises ModuleNotFoundError on Windows before detect()
# ever runs (see GitHub Issue #1). Guard them so the module always imports;
# the POSIX-only helpers/branches below are simply never called on Windows.
# (The original unused `import tty` is dropped, not ported to the guard --
# it was never referenced anywhere in this file.)
if sys.platform != "win32":
    import fcntl
    import pty
    import termios

# Windows PTY backend: pywinpty (wraps the native ConPTY API). This is the
# project's first non-stdlib dependency (see requirements-windows.txt) and is
# only imported on Windows. If it's missing, detect() honestly reports the
# technique as not viable instead of silently falling back to a
# non-PTY subprocess (which would change how the wrapped CLI behaves under
# isatty() checks and produce a non-equivalent capture).
_PYWINPTY_AVAILABLE = False
if sys.platform == "win32":
    try:
        from winpty import PtyProcess as _WinPtyProcess

        _PYWINPTY_AVAILABLE = True
    except ImportError:
        _WinPtyProcess = None  # type: ignore[assignment]

import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parents[2]))
from core import Collector, RawEvent

AGENT_NAME = "antigravity"
COLLECTOR_ID = "antigravity.cli_wrapper"
CHUNK_SIZE = 4096

# Confirmed real (Antigravity_CLIWrapper/RESULTS.md section 4) flag that
# unlocks structured usage/timing data. Not required for this collector to
# function (plain `agy` / text mode still gets captured as raw chunks,
# same as the Copilot wrapper test's fallback behavior), but it's the
# single most valuable real finding of the whole CLI Wrapper test, so a
# live activate()/spawn helper defaults to it when the caller doesn't
# override the command.
RECOMMENDED_OUTPUT_FORMAT = "stream-json"


def _load_jsonl(path: Path) -> List[dict]:
    """Load a real captured wrapper JSONL file exactly as written by
    Antigravity_CLIWrapper/wrapper.py -- one {timestamp, stream, content,
    wrapped_command} dict per line. Skips lines that fail to parse rather
    than raising, since these are hand-collected evidence files, not a
    guaranteed-clean format."""
    events: List[dict] = []
    if not path.is_file():
        return events
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return events


class AntigravityCLIWrapperCollector(Collector):
    """pty-wrapper collector for Antigravity's `agy` CLI.

    Live mode (real deployment): activate() forks a child under a pty
    exactly as Antigravity_CLIWrapper/wrapper.py (itself adapted from
    cli_wrapper_demo/wrapper.py) does -- transparent passthrough of the
    user's real stdin/stdout so the interactive session is unaffected,
    while every chunk written to the pty master is also captured. capture()
    yields one RawEvent per captured chunk, in the exact
    {timestamp, stream, content, wrapped_command} shape the real wrapper
    produces -- NOT reassembled/parsed `agy` JSON events, per the
    IMPORTANT SHAPE NOTE above.

    This build also supports feeding capture() from an already-captured
    JSONL file (any of Antigravity_CLIWrapper/captured_output_*.jsonl) to
    exercise it deterministically against real sample data.
    """

    def __init__(
        self,
        command: Optional[List[str]] = None,
        replay_path: Optional[Path] = None,
    ) -> None:
        # Default command matches the real, most-informative invocation
        # confirmed in RESULTS.md section 4 (stream-json > json > text for
        # data richness). Callers may override to wrap plain `agy`.
        self._command = command
        self._replay_path = replay_path
        self._activated = False
        self._child_pid: Optional[int] = None
        self._master_fd: Optional[int] = None
        self._winpty_proc: Optional[Any] = None
        self._queue: "Queue[Optional[dict]]" = Queue()

    @property
    def collector_id(self) -> str:
        return COLLECTOR_ID

    def detect(self) -> bool:
        """Real, cheap PATH check for the `agy` binary -- confirmed in
        Antigravity_OTel/RESULTS.md section 0 to be a separate
        Homebrew-cask (`antigravity-cli`) install, symlinked onto PATH as
        `agy`, NOT bundled inside Antigravity.app. This is the same
        heuristic antigravity_hooks.py uses for its `agy`-on-PATH check.

        On Windows, this technique additionally depends on pywinpty being
        installed (see module-level import guard above). If it isn't,
        report not-viable honestly here rather than crashing later in
        activate() or silently degrading to a non-PTY capture.
        """
        if sys.platform == "win32" and not _PYWINPTY_AVAILABLE:
            return False
        return shutil.which("agy") is not None

    def activate(self) -> None:
        """If a replay_path was given, activation is a no-op (replay mode
        just marks itself ready). Otherwise, forks a real child under a
        pty running self._command (default: `agy --output-format
        stream-json`), mirroring Antigravity_CLIWrapper/wrapper.py's
        fork+pty+dup2 setup exactly (same pattern as
        cli_wrapper_demo/wrapper.py, generalized there and specialized for
        `agy` in the real test) on POSIX, or a pywinpty/ConPTY spawn (see
        _activate_windows()) on Windows."""
        if self._replay_path is not None:
            self._activated = True
            return

        cmd = self._command or ["agy", "--output-format", RECOMMENDED_OUTPUT_FORMAT]

        if sys.platform == "win32":
            self._activate_windows(cmd)
            return

        master_fd, slave_fd = pty.openpty()
        if sys.stdout.isatty():
            try:
                packed = fcntl.ioctl(sys.stdout.fileno(), termios.TIOCGWINSZ, b"\x00" * 8)
                rows, cols, xpix, ypix = struct.unpack("HHHH", packed)
                fcntl.ioctl(slave_fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, xpix, ypix))
            except OSError:
                pass

        pid = os.fork()
        if pid == 0:
            os.close(master_fd)
            os.setsid()
            fcntl.ioctl(slave_fd, termios.TIOCSCTTY, 0)
            os.dup2(slave_fd, 0)
            os.dup2(slave_fd, 1)
            os.dup2(slave_fd, 2)
            if slave_fd > 2:
                os.close(slave_fd)
            try:
                os.execvp(cmd[0], cmd)
            except FileNotFoundError:
                os._exit(127)

        os.close(slave_fd)
        self._child_pid = pid
        self._master_fd = master_fd
        self._activated = True

    def _activate_windows(self, cmd: List[str]) -> None:
        """Windows PTY backend: spawns `cmd` under a real ConPTY via
        pywinpty (https://github.com/andfoy/pywinpty), the closest Windows
        equivalent of the POSIX pty.openpty()/os.fork() machinery above.
        A real PTY (not plain pipes) matters here because `agy` (see
        module docstring) is exercised the same way it's really deployed --
        a subprocess.Popen pipe fallback risks changing its output shape
        under isatty() checks.
        """
        if not _PYWINPTY_AVAILABLE:
            raise RuntimeError(
                f"{self.collector_id}: pywinpty is not installed; cannot spawn on Windows "
                "(detect() should have already reported this technique as not viable)"
            )
        self._winpty_proc = _WinPtyProcess.spawn(cmd, encoding="utf-8", errors="replace")
        self._activated = True

    def _iter_live_chunks(self) -> Iterator[dict]:
        """Drain the pty master until the child exits, emitting captured
        records in the exact real shape (RESULTS.md's wrapper.py `emit()`).
        """
        if sys.platform == "win32":
            yield from self._iter_live_chunks_windows()
            return

        assert self._master_fd is not None
        wrapped_command = " ".join(self._command or ["agy", "--output-format", RECOMMENDED_OUTPUT_FORMAT])
        while True:
            try:
                rlist, _, _ = select.select([self._master_fd], [], [], 0.2)
            except InterruptedError:
                continue
            if self._master_fd in rlist:
                try:
                    data = os.read(self._master_fd, CHUNK_SIZE)
                except OSError:
                    data = b""
                if data:
                    yield {
                        "timestamp": datetime.datetime.now().isoformat(),
                        "stream": "stdout",
                        "content": data.decode("utf-8", errors="replace"),
                        "wrapped_command": wrapped_command,
                    }
                else:
                    break
            if self._child_pid is not None:
                done_pid, _status = os.waitpid(self._child_pid, os.WNOHANG)
                if done_pid == self._child_pid:
                    break

    def _iter_live_chunks_windows(self) -> Iterator[dict]:
        """Windows counterpart of _iter_live_chunks(). ConPTY already
        merges stdout/stderr into one stream (same as the POSIX branch
        above, which dup2's stderr into the same slave fd), so the payload
        shape here is unchanged; a "platform": "windows" marker is added
        purely for traceability of which backend produced the event."""
        assert self._winpty_proc is not None
        proc = self._winpty_proc
        wrapped_command = " ".join(self._command or ["agy", "--output-format", RECOMMENDED_OUTPUT_FORMAT])
        while True:
            try:
                data = proc.read(CHUNK_SIZE)
            except EOFError:
                break
            except Exception:
                break
            if data:
                yield {
                    "timestamp": datetime.datetime.now().isoformat(),
                    "stream": "stdout",
                    "platform": "windows",
                    "content": data,
                    "wrapped_command": wrapped_command,
                }
            if not proc.isalive():
                break

    def capture(self) -> Iterator[RawEvent]:
        if not self._activated:
            raise RuntimeError(f"{self.collector_id}: capture() called before activate()")

        if self._replay_path is not None:
            for payload in _load_jsonl(self._replay_path):
                yield RawEvent(
                    timestamp=RawEvent.now_iso(),
                    collector_id=self.collector_id,
                    agent=AGENT_NAME,
                    raw_payload=payload,  # untouched, exact captured shape
                )
            return

        for payload in self._iter_live_chunks():
            yield RawEvent(
                timestamp=RawEvent.now_iso(),
                collector_id=self.collector_id,
                agent=AGENT_NAME,
                raw_payload=payload,
            )

    def deactivate(self) -> None:
        if sys.platform == "win32":
            if self._winpty_proc is not None:
                try:
                    self._winpty_proc.terminate(force=True)
                except Exception:
                    pass
                self._winpty_proc = None
            self._activated = False
            return

        if self._master_fd is not None:
            try:
                os.close(self._master_fd)
            except OSError:
                pass
            self._master_fd = None
        if self._child_pid is not None:
            try:
                os.kill(self._child_pid, signal.SIGTERM)
            except OSError:
                pass
            self._child_pid = None
        self._activated = False


if __name__ == "__main__":
    collector = AntigravityCLIWrapperCollector()
    print(f"detect() -> {collector.detect()}")

    real_capture_dir = Path(__file__).resolve().parents[3].parent / "Antigravity_CLIWrapper"
    replay_file = real_capture_dir / "captured_output_streamjson.jsonl"
    if not replay_file.is_file():
        replay_file = real_capture_dir / "captured_output_json.jsonl"

    print(f"\nSIMULATED FROM CAPTURED DATA ({replay_file}):")
    if replay_file.is_file():
        replay_collector = AntigravityCLIWrapperCollector(replay_path=replay_file)
        replay_collector.activate()
        count = 0
        for ev in replay_collector.capture():
            print(f"  {ev}")
            count += 1
            if count >= 5:
                break
        replay_collector.deactivate()
        print(f"  ... ({count} of possibly more RawEvents shown)")
    else:
        print(f"  no real captured file found at {replay_file}, skipping replay demo")
