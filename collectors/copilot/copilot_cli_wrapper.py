"""
GitHub Copilot CLI Wrapper collector.

Grounded in TWO real folders (adapted, not blindly copied):

  cli_wrapper_demo/  -- the generic pty-wrapper MECHANISM, and critically,
      its already-run verified end-to-end test was performed against the
      REAL `copilot` CLI binary specifically (not a placeholder target):
        python3 wrapper.py -- copilot -p "List the files in the current
        directory and briefly describe what wrapper.py does in one
        sentence." --allow-all-tools
      This produced 79 real captured chunk events in
      cli_wrapper_demo/captured_events.jsonl, e.g.:
        {"timestamp": "2026-07-27T19:39:34.764987", "stream": "stdout",
         "content": "Listing", "wrapped_command": "copilot -p List the
         files in the current directory ... --allow-all-tools",
         "received_at": "2026-07-27T19:39:34.799019"}
      Reassembling `content` in order (cli_wrapper_demo/README.md) shows the
      real Copilot CLI transcript, including its footer
      ("Changes +0 -0", "AI Credits 0.59 (19s)", "Tokens ...", "Resume
      copilot --resume=<uuid>") -- a real, scrapable session-id signal that
      only exists as printed text, not a structured field (see
      cli_wrapper_demo/README.md's comparison table).

  Copilot_VSCode_CLI/RESULTS.md -- confirms the wrapper mechanism's
      fundamental premise (spawn the real CLI as a child process, observe
      its I/O) generalizes to the VS-Code-embedded copy of the same binary
      too: VS Code itself spawns `@github/copilot-darwin-x64/index.js
      --headless --stdio` as a child process at window startup (see
      section a, "AgentHostProcessManager: agent host started" in the main
      process log) -- i.e. Copilot's own CLI binary is *already* run this
      way by its own host application, which is exactly the wrapper
      technique's own supervising-parent-process shape. This collector
      focuses on the standalone `copilot` binary path (the one actually
      demonstrated end-to-end with real captured output in
      cli_wrapper_demo/), and documents the VS Code embedded variant as an
      important but separately-scoped case (different binary invocation
      --headless --stdio, not a plain interactive pty session; see
      detect()'s VS Code note below).

This collector does NOT reimplement the pty-fork machinery from scratch --
`_spawn_and_pump()` below is a direct, minimally-adapted port of
cli_wrapper_demo/wrapper.py's core loop (pty.openpty, fork/exec, select-loop
copy of stdin<->pty and pty->stdout, background-thread event delivery),
narrowed specifically to wrap the `copilot` binary and to speak RawEvent
instead of doing its own HTTP POST to a receiver.

No normalization: raw_payload is the exact {timestamp, stream, content,
wrapped_command} shape cli_wrapper_demo/wrapper.py already produces and
cli_wrapper_demo/captured_events.jsonl already contains -- untouched. As
documented in cli_wrapper_demo/README.md, this is deliberately a WEAKER,
coarser signal than a real hook/OTel event: raw terminal bytes in arbitrary
chunk boundaries, not structured/typed fields. Tool/action attribution is
NOT directly available here (would require scraping the printed text) --
this collector does not attempt that inference, to avoid fabricating a
signal the raw data doesn't actually provide structurally.
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
from queue import Empty, Queue
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

AGENT_NAME = "copilot"
COLLECTOR_ID = "copilot.cli_wrapper"

CHUNK_SIZE = 4096

# Real footer fields the wrapped `copilot` CLI prints at the end of a `-p`
# run, per cli_wrapper_demo/README.md's reassembled transcript -- kept here
# only as documentation of what IS scrapeable from raw text, not used by
# this collector to auto-parse/normalize raw_payload (that would violate the
# "no normalization" contract; a later pipeline stage's job).
KNOWN_FOOTER_MARKERS = ("AI Credits", "Tokens", "Resume", "Changes")


def _get_win_size(fd: int):
    packed = fcntl.ioctl(fd, termios.TIOCGWINSZ, b"\x00" * 8)
    return struct.unpack("HHHH", packed)


def _set_win_size(fd: int, rows: int, cols: int, xpix: int = 0, ypix: int = 0) -> None:
    packed = struct.pack("HHHH", rows, cols, xpix, ypix)
    fcntl.ioctl(fd, termios.TIOCSWINSZ, packed)


class CopilotCLIWrapperCollector(Collector):
    """CLI-wrapper-based collector for the standalone `copilot` CLI binary.

    Live mode (real deployment): activate() would fork+exec the real
    `copilot` binary under a pty (direct port of cli_wrapper_demo/wrapper.py
    -- see _spawn_and_pump()), and capture() would drain a queue of
    {timestamp, stream, content, wrapped_command} chunk events as the child
    process runs, exactly like cli_wrapper_demo/receiver.py does over HTTP
    but delivered in-process as RawEvent objects instead.

    This build supports feeding capture() from already-captured JSONL chunk
    dicts (cli_wrapper_demo/captured_events.jsonl, the REAL 79-event capture
    of `copilot -p ...` running under this exact mechanism) to exercise it
    deterministically without spawning a live process.
    """

    def __init__(
        self,
        command: Optional[List[str]] = None,
        replay_events: Optional[List[dict]] = None,
    ) -> None:
        # Default command mirrors cli_wrapper_demo/README.md's verified
        # invocation shape: `wrapper.py -- copilot ...`.
        self._command = command or ["copilot"]
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
        """Real, cheap heuristic: is the `copilot` binary on PATH at all?
        This is the ONLY real precondition the wrapper technique needs --
        unlike copilot_hooks.py it does not depend on any hook config file
        or env var being set correctly (that is the whole point of the
        wrapper fallback technique, per cli_wrapper_demo/README.md's "Why
        it matters" section: it doesn't depend on the target program
        cooperating at all, only on being spawnable as a child process).

        On Windows, this technique additionally depends on pywinpty being
        installed (see module-level import guard above). If it isn't,
        report not-viable honestly here rather than crashing later in
        activate() or silently degrading to a non-PTY capture.
        """
        if sys.platform == "win32" and not _PYWINPTY_AVAILABLE:
            return False
        return shutil.which("copilot") is not None

    def activate(self) -> None:
        """In replay mode (self._replay_events set), this is a no-op besides
        marking activated -- there is no live process to spawn. In live
        mode, this would fork+exec the wrapped command under a pty; that
        spawn logic is implemented in _spawn_and_pump() below (a direct
        port of cli_wrapper_demo/wrapper.py's main()) on POSIX, or
        _spawn_and_pump_windows() (pywinpty/ConPTY) on Windows -- only
        invoked here, not during __init__, per the Collector contract's
        activate() boundary.
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
            }
        )

    def _spawn_and_pump(self) -> None:
        """Direct, minimally-adapted port of cli_wrapper_demo/wrapper.py's
        pty-fork-and-pump loop. Kept as a background thread here (rather
        than wrapper.py's blocking foreground select loop) since this
        collector must return control to a caller's activate()/capture()
        lifecycle rather than owning the process's stdin/stdout directly.
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
                            break  # child closed the pty -> exited

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
                self._queue.put(None)  # sentinel: pump thread finished

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
            }
        )

    def _spawn_and_pump_windows(self) -> None:
        """Windows PTY backend: spawns the wrapped command under a real
        ConPTY via pywinpty (https://github.com/andfoy/pywinpty), the
        closest Windows equivalent of the POSIX pty.openpty()/os.fork()
        machinery in _spawn_and_pump() above. A real PTY (not plain pipes)
        matters here because the real `copilot` CLI's footer output
        (see module docstring) only appears when it thinks it's running
        interactively -- a subprocess.Popen pipe fallback would silently
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
                # raw_payload is the exact {timestamp, stream, content,
                # wrapped_command[, received_at]} shape from
                # cli_wrapper_demo/captured_events.jsonl -- untouched, not
                # reshaped to look like a hook event.
                yield RawEvent(
                    timestamp=RawEvent.now_iso(),
                    collector_id=self.collector_id,
                    agent=AGENT_NAME,
                    raw_payload=chunk,
                )
            return

        # Live mode: drain the queue the background pump thread fills,
        # until it signals completion with the None sentinel.
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
    print("=== CopilotCLIWrapperCollector standalone test ===")

    collector = CopilotCLIWrapperCollector()
    detected = collector.detect()
    print(f"detect() -> {detected} (checks `copilot` on PATH)")

    real_evidence_path = (
        Path(__file__).resolve().parents[4]
        / "cli_wrapper_demo" / "captured_events.jsonl"
    )

    if real_evidence_path.is_file():
        print(f"Found real captured wrapper output at {real_evidence_path}")
        replay_chunks = []
        with open(real_evidence_path) as f:
            for line in f:
                line = line.strip()
                if line:
                    replay_chunks.append(json.loads(line))

        replay_collector = CopilotCLIWrapperCollector(replay_events=replay_chunks)
        replay_collector.activate()
        print(
            f"\nSIMULATED FROM CAPTURED DATA (real {len(replay_chunks)} chunk "
            f"events from cli_wrapper_demo/captured_events.jsonl, wrapping "
            f"the real `copilot -p ...` binary):"
        )
        reassembled = []
        shown = 0
        for event in replay_collector.capture():
            reassembled.append(event.raw_payload.get("content", ""))
            if shown < 5:
                print(
                    f"  [{shown}] collector_id={event.collector_id!r} "
                    f"stream={event.raw_payload.get('stream')!r} "
                    f"content={event.raw_payload.get('content')!r}"
                )
                shown += 1
        replay_collector.deactivate()
        print(f"  ... ({len(reassembled)} total chunks captured)")
        print("\nReassembled transcript (content fields joined in order):")
        print("  " + "".join(reassembled).replace("\n", "\n  ")[:600] + " ...")
    else:
        print(
            f"Real evidence file not found at {real_evidence_path} -- "
            f"skipping replay demonstration."
        )

    print(
        "\nNOTE: live-mode spawn (_spawn_and_pump) was intentionally NOT "
        "exercised in this __main__ block -- it requires an authenticated "
        "`copilot` binary and would launch a real subprocess/pty, which is "
        "out of scope for a non-interactive self-test. The replay-mode "
        "demonstration above uses real previously-captured data instead, "
        "per this project's other collectors' convention (see "
        "copilot_hooks.py's __main__ block for the same pattern)."
    )
