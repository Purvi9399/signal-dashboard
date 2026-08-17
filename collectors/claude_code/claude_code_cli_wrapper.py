"""
Claude Code CLI Wrapper collector.

Grounded in the generic pty-wrapper MECHANISM demonstrated end-to-end in
cli_wrapper_demo/ (wrapper.py, captured_events.jsonl, README.md) -- that
demo's real, already-run capture was against the `copilot` binary
specifically, NOT `claude` (checked cli_wrapper_demo/captured_events.jsonl:
every `wrapped_command` field says "copilot ..."). There is no
Claude-Code-specific captured_events.jsonl anywhere in this project (checked
for a `Claude_Code_CLIWrapper/` or similar folder -- none exists, unlike
Codex_CLIWrapper/Antigravity_CLIWrapper). Per the task brief this is the
"MECHANISM reference, same pattern as codex_cli_wrapper.py/
copilot_cli_wrapper.py did" -- i.e. port the pty-fork-and-pump machinery,
narrowed to the `claude` binary, and be honest that no Claude-specific
sample transcript exists to replay (the __main__ block below replays
cli_wrapper_demo/captured_events.jsonl explicitly labeled as a DIFFERENT
agent's real data, the same honesty pattern copilot_fs_watcher.py used for
Antigravity's fs-watcher data).

FIELD GROUNDING (reference_tables/observables_table_claude_cursor.csv):
  - operator_username <- row "operator_username": "CLI wrapper" / Note:
    "OS username from the collector." -- this collector's wrapper mechanism
    is exactly what would supply this (os.getlogin()/getpass.getuser()),
    not the wrapped `claude` process itself.
  - hostname          <- row "hostname": "CLI wrapper" / Note: "Collector
    environment." -- same reasoning, sourced from the wrapping collector's
    own machine, not from `claude`'s output.
  - exit_code         <- row "exit_code": "CLI wrapper" / Note: "Cursor puts
    exitCode inside tool_output. CC needs the wrapper or stderr." -- i.e.
    per the CSV, Claude Code's OWN hook payloads do NOT reliably carry a
    shell exit code; the wrapper is one of the two ways (with raw stderr
    parsing) to actually obtain it for Claude Code specifically.
  - hung_seconds      <- row "hung_seconds": "CLI wrapper" / Note: "WRAPPER
    ONLY. Nothing else can see silence while a process is alive." -- this
    collector's raw chunk timestamps are exactly what a later stage would
    diff to compute this; this collector does not compute it itself (no
    normalization / derived-field computation happens here).
  - is_developer_command <- row "is_developer_command": "CLI wrapper" /
    Note: "WRAPPER ONLY. Commands the human ran, which the agent never
    saw." -- the wrapper sees the human's raw stdin bytes, something no
    hook/OTel/MCP-proxy surface can see, per the CSV.

This collector does NOT reimplement the pty-fork machinery from scratch --
`_spawn_and_pump()` below is a direct, minimally-adapted port of
cli_wrapper_demo/wrapper.py's core loop (pty.openpty, fork/exec, select-loop
copy of stdin<->pty and pty->stdout, background-thread event delivery),
narrowed specifically to wrap the `claude` binary instead of `copilot`.

No normalization: raw_payload is the exact {timestamp, stream, content,
wrapped_command} shape cli_wrapper_demo/wrapper.py already produces --
untouched. As documented in cli_wrapper_demo/README.md, this is a WEAKER,
coarser signal than a real hook/OTel event: raw terminal bytes in arbitrary
chunk boundaries, not structured/typed fields. This collector does not
attempt to infer tool/action attribution from the raw text -- that would
fabricate a signal the raw data doesn't structurally provide.
"""

from __future__ import annotations

import datetime
import getpass
import json
import os
import select
import shutil
import signal
import socket
import struct
import sys
import threading
from pathlib import Path
from queue import Empty, Queue
from typing import Any, Iterator, List, Optional

# fcntl/pty/termios are POSIX-only; importing them at module level (as this
# file originally did) raises ModuleNotFoundError on Windows before detect()
# ever runs (see GitHub Issue #1). Guard them so the module always imports;
# the POSIX-only helpers/branches below are simply never called on Windows.
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

AGENT_NAME = "claude_code"
COLLECTOR_ID = "claude_code.cli_wrapper"

CHUNK_SIZE = 4096


def _get_win_size(fd: int):
    packed = fcntl.ioctl(fd, termios.TIOCGWINSZ, b"\x00" * 8)
    return struct.unpack("HHHH", packed)


def _set_win_size(fd: int, rows: int, cols: int, xpix: int = 0, ypix: int = 0) -> None:
    packed = struct.pack("HHHH", rows, cols, xpix, ypix)
    fcntl.ioctl(fd, termios.TIOCSWINSZ, packed)


def operator_username() -> str:
    """CSV row "operator_username": "CLI wrapper" -- OS username, sourced
    from the collector's own environment (the wrapper process), not from
    anything `claude` itself reports."""
    try:
        return getpass.getuser()
    except Exception:
        return os.environ.get("USER", "unknown")


def collector_hostname() -> str:
    """CSV row "hostname": "CLI wrapper" -- collector environment."""
    return socket.gethostname()


class ClaudeCodeCLIWrapperCollector(Collector):
    """CLI-wrapper-based collector for the standalone `claude` CLI binary.

    Live mode (real deployment): activate() would fork+exec the real
    `claude` binary under a pty (direct port of cli_wrapper_demo/wrapper.py
    -- see _spawn_and_pump()), and capture() would drain a queue of
    {timestamp, stream, content, wrapped_command} chunk events as the child
    process runs.

    This build supports feeding capture() from already-captured JSONL chunk
    dicts to exercise it deterministically without spawning a live process.
    No Claude-specific captured_events.jsonl exists anywhere in this repo
    (cli_wrapper_demo/captured_events.jsonl is real data, but for the
    `copilot` binary, not `claude` -- see module docstring); the __main__
    block below replays it anyway, clearly labeled as a different agent's
    real data, purely to exercise this collector's own plumbing end-to-end.
    """

    def __init__(
        self,
        command: Optional[List[str]] = None,
        replay_events: Optional[List[dict]] = None,
    ) -> None:
        self._command = command or ["claude"]
        self._replay_events = replay_events
        self._activated = False
        self._queue: "Queue" = Queue()
        self._child_pid: Optional[int] = None
        self._master_fd: Optional[int] = None
        self._pump_thread: Optional[threading.Thread] = None
        self._winpty_proc: Optional[Any] = None

    @property
    def collector_id(self) -> str:
        return COLLECTOR_ID

    def detect(self) -> bool:
        """Real, cheap heuristic: is the `claude` binary on PATH at all?
        Same check claude_code_hooks.py's detect() uses as one of its three
        signals -- here it is the ONLY precondition needed, since the
        wrapper technique (per cli_wrapper_demo/README.md's "Why it
        matters" section) does not depend on any hook config or env var
        being set correctly, only on the target being spawnable.

        On Windows, this technique additionally depends on pywinpty being
        installed (see module-level import guard above). If it isn't,
        report not-viable honestly here rather than crashing later in
        activate() or silently degrading to a non-PTY capture.
        """
        if sys.platform == "win32" and not _PYWINPTY_AVAILABLE:
            return False
        return shutil.which("claude") is not None

    def activate(self) -> None:
        """In replay mode, a no-op besides marking activated. In live mode,
        forks+execs the wrapped command under a pty; see _spawn_and_pump()
        (a direct port of cli_wrapper_demo/wrapper.py's main()) on POSIX,
        or _spawn_and_pump_windows() (pywinpty/ConPTY) on Windows.
        """
        if self._replay_events is None:
            if sys.platform == "win32":
                self._spawn_and_pump_windows()
            else:
                self._spawn_and_pump()
        self._activated = True

    def _emit(self, stream: str, chunk_bytes: bytes) -> None:
        text = chunk_bytes.decode("utf-8", errors="replace")
        self._queue.put(
            {
                "timestamp": datetime.datetime.now().isoformat(),
                "stream": stream,
                "content": text,
                "wrapped_command": " ".join(self._command),
                # CSV-grounded fields the CLI wrapper technique specifically
                # (and uniquely) can supply -- see module docstring citations
                # for operator_username / hostname ("CLI wrapper" column).
                "operator_username": operator_username(),
                "hostname": collector_hostname(),
            }
        )

    def _spawn_and_pump(self) -> None:
        """Direct, minimally-adapted port of cli_wrapper_demo/wrapper.py's
        pty-fork-and-pump loop, narrowed to `claude` and run as a background
        thread so activate()/capture() can follow this framework's
        lifecycle instead of owning stdin/stdout directly.
        """
        master_fd, slave_fd = pty.openpty()

        if sys.stdout.isatty():
            rows, cols, xpix, ypix = _get_win_size(sys.stdout.fileno())
            _set_win_size(slave_fd, rows, cols, xpix, ypix)

        err_r, err_w = os.pipe()

        pid = os.fork()
        if pid == 0:
            os.close(master_fd)
            os.close(err_r)
            os.setsid()
            fcntl.ioctl(slave_fd, termios.TIOCSCTTY, 0)
            os.dup2(slave_fd, 0)
            os.dup2(slave_fd, 1)
            os.dup2(err_w, 2)
            if slave_fd > 2:
                os.close(slave_fd)
            os.close(err_w)
            try:
                os.execvp(self._command[0], self._command)
            except FileNotFoundError:
                os._exit(127)

        os.close(slave_fd)
        os.close(err_w)
        self._child_pid = pid
        self._master_fd = master_fd

        def pump_loop():
            try:
                while True:
                    try:
                        rlist, _, _ = select.select([master_fd, err_r], [], [], 0.2)
                    except (InterruptedError, OSError):
                        break

                    if master_fd in rlist:
                        try:
                            data = os.read(master_fd, CHUNK_SIZE)
                        except OSError:
                            data = b""
                        if data:
                            self._emit("stdout", data)
                        else:
                            break

                    if err_r in rlist:
                        try:
                            data = os.read(err_r, CHUNK_SIZE)
                        except OSError:
                            data = b""
                        if data:
                            self._emit("stderr", data)

                    done_pid, _status = os.waitpid(pid, os.WNOHANG)
                    if done_pid == pid:
                        break
            finally:
                for fd in (master_fd, err_r):
                    try:
                        os.close(fd)
                    except OSError:
                        pass
                self._queue.put(None)

        self._pump_thread = threading.Thread(target=pump_loop, daemon=True)
        self._pump_thread.start()

    def _emit_windows(self, text: str) -> None:
        """Windows counterpart of _emit(). ConPTY (via pywinpty) merges the
        child's stdout/stderr into a single stream -- unlike the POSIX path
        above, which pipes stderr separately. Rather than fabricating a
        stderr split that doesn't actually happen on Windows, every
        Windows-sourced event is tagged stream="stdout" plus an explicit
        platform="windows" marker so downstream consumers can tell the two
        capture paths apart."""
        self._queue.put(
            {
                "timestamp": datetime.datetime.now().isoformat(),
                "stream": "stdout",
                "platform": "windows",
                "content": text,
                "wrapped_command": " ".join(self._command),
                "operator_username": operator_username(),
                "hostname": collector_hostname(),
            }
        )

    def _spawn_and_pump_windows(self) -> None:
        """Windows PTY backend: spawns the wrapped command under a real
        ConPTY via pywinpty (https://github.com/andfoy/pywinpty), the
        closest Windows equivalent of the POSIX pty.openpty()/os.fork()
        machinery in _spawn_and_pump() above. A real PTY (not plain pipes)
        matters here because these wrapped CLIs behave differently under
        isatty() checks -- a subprocess.Popen pipe fallback would silently
        produce a non-equivalent, degraded capture.
        """
        if not _PYWINPTY_AVAILABLE:
            raise RuntimeError(
                f"{self.collector_id}: pywinpty is not installed; cannot spawn on Windows "
                "(detect() should have already reported this technique as not viable)"
            )

        proc = _WinPtyProcess.spawn(self._command, encoding="utf-8", errors="replace")
        self._winpty_proc = proc

        def pump_loop():
            try:
                while True:
                    try:
                        data = proc.read(CHUNK_SIZE)
                    except EOFError:
                        break
                    except Exception:
                        break
                    if data:
                        self._emit_windows(data)
                    if not proc.isalive():
                        break
            finally:
                self._queue.put(None)

        self._pump_thread = threading.Thread(target=pump_loop, daemon=True)
        self._pump_thread.start()

    def capture(self) -> Iterator[RawEvent]:
        if not self._activated:
            raise RuntimeError(f"{self.collector_id}: capture() called before activate()")

        if self._replay_events is not None:
            for chunk in self._replay_events:
                yield RawEvent(
                    timestamp=RawEvent.now_iso(),
                    collector_id=self.collector_id,
                    agent=AGENT_NAME,
                    raw_payload=chunk,
                )
            return

        while True:
            try:
                item = self._queue.get(timeout=1.0)
            except Empty:
                if self._pump_thread is not None and not self._pump_thread.is_alive():
                    return
                continue
            if item is None:
                return
            yield RawEvent(
                timestamp=RawEvent.now_iso(),
                collector_id=self.collector_id,
                agent=AGENT_NAME,
                raw_payload=item,
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

        if self._child_pid is not None:
            try:
                os.kill(self._child_pid, signal.SIGTERM)
            except OSError:
                pass
        self._activated = False


if __name__ == "__main__":
    print("=== ClaudeCodeCLIWrapperCollector standalone test ===")

    collector = ClaudeCodeCLIWrapperCollector()
    detected = collector.detect()
    print(f"detect() -> {detected} (checks `claude` on PATH)")

    real_evidence_path = (
        Path(__file__).resolve().parents[4] / "cli_wrapper_demo" / "captured_events.jsonl"
    )

    if real_evidence_path.is_file():
        print(
            f"\nSIMULATED FROM CAPTURED DATA (NOTE: this is REAL captured data, "
            f"but from cli_wrapper_demo/captured_events.jsonl, whose "
            f"wrapped_command is `copilot -p ...` -- a DIFFERENT agent's real "
            f"pty-wrapper capture. No Claude-specific captured_events.jsonl "
            f"exists anywhere in this project; reused here ONLY to exercise "
            f"this collector's own plumbing end-to-end, per copilot_fs_watcher.py's "
            f"same cross-agent-replay honesty pattern):"
        )
        replay_chunks = []
        with open(real_evidence_path) as f:
            for line in f:
                line = line.strip()
                if line:
                    replay_chunks.append(json.loads(line))

        replay_collector = ClaudeCodeCLIWrapperCollector(replay_events=replay_chunks)
        replay_collector.activate()
        reassembled = []
        shown = 0
        for event in replay_collector.capture():
            reassembled.append(event.raw_payload.get("content", ""))
            if shown < 5:
                print(
                    f"  [{shown}] collector_id={event.collector_id!r} agent={event.agent!r} "
                    f"stream={event.raw_payload.get('stream')!r} "
                    f"content={event.raw_payload.get('content')!r}"
                )
                shown += 1
        replay_collector.deactivate()
        print(f"  ... ({len(reassembled)} total chunks captured)")
    else:
        print(f"Real evidence file not found at {real_evidence_path} -- skipping replay demonstration.")

    print(
        "\nNOTE: live-mode spawn (_spawn_and_pump) was intentionally NOT "
        "exercised here -- it requires an authenticated `claude` binary and "
        "would launch a real subprocess/pty, out of scope for a "
        "non-interactive self-test. Demonstrating operator_username()/"
        "collector_hostname() (the CSV-grounded CLI-wrapper-only fields) "
        "directly instead:"
    )
    print(f"  operator_username() -> {operator_username()!r}")
    print(f"  collector_hostname() -> {collector_hostname()!r}")
