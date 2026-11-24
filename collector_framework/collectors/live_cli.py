"""Shared live PTY CLI wrapper (LIVE_CLI.md)."""

from __future__ import annotations

import os
import select
import signal
import struct
import sys
import threading
from datetime import datetime
from typing import Any, Callable, Optional, Sequence

from collectors.live_http import LiveEventFeed

if sys.platform != "win32":
    import fcntl
    import pty
    import termios

_PYWINPTY_AVAILABLE = False
_WinPtyProcess = None
if sys.platform == "win32":
    try:
        from winpty import PtyProcess as _WinPtyProcess

        _PYWINPTY_AVAILABLE = True
    except ImportError:
        _WinPtyProcess = None  # type: ignore[assignment]

CHUNK_SIZE = 4096
ExtraFn = Callable[[], dict]


def build_chunk(
    stream: str,
    content: str,
    wrapped_command: str,
    extra: Optional[dict] = None,
) -> dict:
    record = {
        "timestamp": datetime.now().isoformat(),
        "stream": stream,
        "content": content,
        "wrapped_command": wrapped_command,
    }
    if extra:
        record.update(extra)
    return record


def _get_win_size(fd: int):
    packed = fcntl.ioctl(fd, termios.TIOCGWINSZ, b"\x00" * 8)
    return struct.unpack("HHHH", packed)


def _set_win_size(fd: int, rows: int, cols: int, xpix: int = 0, ypix: int = 0) -> None:
    packed = struct.pack("HHHH", rows, cols, xpix, ypix)
    fcntl.ioctl(fd, termios.TIOCSWINSZ, packed)


class LivePtySession:
    """Spawn a command under a PTY and put chunks on a feed."""

    def __init__(
        self,
        command: Sequence[str],
        feed: LiveEventFeed,
        extra_for: Optional[ExtraFn] = None,
        merge_stderr: bool = False,
    ) -> None:
        self._command = list(command)
        self._feed = feed
        self._extra_for = extra_for
        self._merge_stderr = merge_stderr
        self._child_pid: Optional[int] = None
        self._winpty_proc: Optional[Any] = None
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        if sys.platform == "win32":
            self._start_windows()
            return
        self._start_posix()

    def _wrapped(self) -> str:
        return " ".join(self._command)

    def _extra(self, base: Optional[dict] = None) -> Optional[dict]:
        extra = dict(base or {})
        if self._extra_for is not None:
            extra.update(self._extra_for())
        return extra or None

    def _put(self, stream: str, text: str, base: Optional[dict] = None) -> None:
        self._feed.put(build_chunk(stream, text, self._wrapped(), self._extra(base)))

    def _start_posix(self) -> None:
        master_fd, slave_fd = pty.openpty()
        self._size_slave(slave_fd)
        err_r, err_w = os.pipe()
        pid = os.fork()
        if pid == 0:
            self._child_exec(master_fd, slave_fd, err_r, err_w)
        os.close(slave_fd)
        os.close(err_w)
        self._child_pid = pid
        thread = threading.Thread(
            target=self._pump_posix, args=(master_fd, err_r, pid), daemon=True
        )
        thread.start()
        self._thread = thread

    def _size_slave(self, slave_fd: int) -> None:
        if not sys.stdout.isatty():
            return
        try:
            rows, cols, xpix, ypix = _get_win_size(sys.stdout.fileno())
            _set_win_size(slave_fd, rows, cols, xpix, ypix)
        except OSError:
            return

    def _child_exec(self, master_fd: int, slave_fd: int, err_r: int, err_w: int) -> None:
        os.close(master_fd)
        os.close(err_r)
        os.setsid()
        fcntl.ioctl(slave_fd, termios.TIOCSCTTY, 0)
        os.dup2(slave_fd, 0)
        os.dup2(slave_fd, 1)
        os.dup2(slave_fd if self._merge_stderr else err_w, 2)
        if slave_fd > 2:
            os.close(slave_fd)
        os.close(err_w)
        try:
            os.execvp(self._command[0], self._command)
        except FileNotFoundError:
            os._exit(127)

    def _pump_posix(self, master_fd: int, err_r: int, pid: int) -> None:
        try:
            self._posix_loop(master_fd, err_r, pid)
        finally:
            self._close_fds(master_fd, err_r)
            self._feed.stop()

    def _posix_loop(self, master_fd: int, err_r: int, pid: int) -> None:
        while True:
            rlist = self._select_fds(master_fd, err_r)
            if rlist is None:
                return
            if not self._drain(master_fd, rlist, "stdout"):
                return
            self._drain(err_r, rlist, "stderr")
            if self._child_done(pid):
                return

    def _select_fds(self, master_fd: int, err_r: int) -> Optional[list]:
        try:
            rlist, _, _ = select.select([master_fd, err_r], [], [], 0.2)
            return rlist
        except (InterruptedError, OSError):
            return None

    def _drain(self, fd: int, rlist: list, stream: str) -> bool:
        if fd not in rlist:
            return True
        data = self._read_fd(fd)
        if data:
            self._put(stream, data.decode("utf-8", errors="replace"))
            return True
        return stream != "stdout"

    def _read_fd(self, fd: int) -> bytes:
        try:
            return os.read(fd, CHUNK_SIZE)
        except OSError:
            return b""

    def _child_done(self, pid: int) -> bool:
        done_pid, _status = os.waitpid(pid, os.WNOHANG)
        return done_pid == pid

    def _close_fds(self, *fds: int) -> None:
        for fd in fds:
            try:
                os.close(fd)
            except OSError:
                continue

    def _start_windows(self) -> None:
        if not _PYWINPTY_AVAILABLE:
            raise RuntimeError("live CLI wrapper requires pywinpty on Windows")
        proc = _WinPtyProcess.spawn(self._command)
        self._winpty_proc = proc
        thread = threading.Thread(target=self._pump_windows, args=(proc,), daemon=True)
        thread.start()
        self._thread = thread

    def _pump_windows(self, proc) -> None:
        try:
            self._windows_loop(proc)
        finally:
            self._feed.stop()

    def _windows_loop(self, proc) -> None:
        while True:
            data = self._win_read(proc)
            if data is None:
                return
            if data:
                self._put("stdout", data, {"platform": "windows"})
            if not proc.isalive():
                return

    def _win_read(self, proc) -> Optional[str]:
        try:
            return proc.read(CHUNK_SIZE)
        except EOFError:
            return None
        except Exception:
            return None

    def stop(self) -> None:
        self._kill()
        self._feed.stop()

    def _kill(self) -> None:
        if sys.platform == "win32":
            self._kill_windows()
            return
        pid = self._child_pid
        self._child_pid = None
        if pid is None:
            return
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            return

    def _kill_windows(self) -> None:
        proc = self._winpty_proc
        self._winpty_proc = None
        if proc is None:
            return
        try:
            proc.terminate(force=True)
        except Exception:
            return
