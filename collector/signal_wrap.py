#!/usr/bin/env python3
"""
Signal Collect — CLI wrapper (collector 1 of 6)

Runs any command inside a pseudo-terminal that we own, relays everything
through untouched, and records what passed. The developer sees no
difference; we get the full terminal session plus Signal observable rows.

Works on any CLI tool. No tool-specific code:
    ./signal_wrap.py claude
    ./signal_wrap.py cursor-agent
    ./signal_wrap.py codex
    ./signal_wrap.py bash

Outputs (in ./signal_sessions/ by default):
    <session>.raw     full byte stream, ANSI codes intact (replayable)
    <session>.txt     same stream, ANSI stripped (readable)
    <session>.jsonl   Signal observable rows

Env:
    SIGNAL_SESSION_DIR   where to write        (default ./signal_sessions)
    SIGNAL_TOOL          label for this tool   (default: the binary name)
    SIGNAL_STALL_SECONDS silence before we call it a stall (default 8)
    SIGNAL_OPERATOR      who is running it     (default: $USER)
"""

import fcntl
import json
import os
import pty
import re
import select
import signal
import struct
import sys
import termios
import time
import tty
import uuid
from datetime import datetime, timezone

SESSION_DIR = os.environ.get("SIGNAL_SESSION_DIR", "signal_sessions")
STALL_SECONDS = float(os.environ.get("SIGNAL_STALL_SECONDS", "8"))
OPERATOR = os.environ.get("SIGNAL_OPERATOR", os.environ.get("USER", ""))

ANSI = re.compile(
    rb"\x1B[PX^_][^\x1B]*\x1B\\"          # DCS / SOS / PM / APC strings
    rb"|\x1B\][^\x07\x1B]*(?:\x07|\x1B\\)"  # OSC
    rb"|\x1B\[[0-?]*[ -/]*[@-~]"           # CSI
    rb"|\x1B[@-Z\\-_]"                     # two-character escapes
)


def now_iso():
    return datetime.now(timezone.utc).isoformat()


class SessionRecorder:
    """Writes the byte streams and the observable rows."""

    def __init__(self, tool, argv, cwd):
        os.makedirs(SESSION_DIR, exist_ok=True)
        self.session_id = uuid.uuid4().hex[:16]
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        base = os.path.join(SESSION_DIR, f"{stamp}-{tool}-{self.session_id[:8]}")
        self.raw = open(base + ".raw", "wb")
        self.txt = open(base + ".txt", "wb")
        self.rows = open(base + ".jsonl", "a")
        self.base = base

        self.tool = tool
        self.argv = argv
        self.cwd = cwd
        self.started = time.time()

        self.bytes_in = 0        # typed by the human
        self.bytes_out = 0       # printed by the tool
        self.input_buffer = b""  # keystrokes until Enter
        self.row_count = 0
        self.awaiting_output = True   # nothing typed yet, so silence counts
        self.ever_typed = False       # has the human typed anything at all
        self.esc = 0                  # escape-sequence parser state

    # ---------- observable rows ----------

    def row(self, interaction_type, **extra):
        r = {
            "observable_id": uuid.uuid4().hex,
            "timestamp": now_iso(),
            "agent_id": self.session_id,
            "agent_type": "coding_agent",
            "agent_tool": self.tool,
            "operator": OPERATOR,
            "company": "real_fleet",
            "surface": "cli",
            "collector": "cli_wrapper",
            "interaction_type": interaction_type,
            "protocol": "pty",
            "connector": "shell",
            "permission_status": "not_applicable",
            "latency_ms": 0,
            "tokens_total": 0,
            "cost_usd": 0.0,
            "sensitivity_tier": 1,
            "cascade_depth": 0,
            "violation_count": 0,
            "escalation_flag": False,
            "success": True,
            "error_type": "",
            "detail": {},
        }
        r.update(extra)
        self.rows.write(json.dumps(r, default=str) + "\n")
        self.rows.flush()
        self.row_count += 1
        return r

    # ---------- stream capture ----------

    def on_output(self, data):
        self.bytes_out += len(data)
        self.raw.write(data)
        self.raw.flush()
        self.txt.write(ANSI.sub(b"", data))
        self.txt.flush()

    def on_input(self, data):
        """Capture what the human typed, ignoring terminal escape sequences.

        Terminals send handshake and mouse-tracking sequences on stdin that
        look like typed text. A small state machine discards them.
        """
        self.bytes_in += len(data)
        self.raw.write(data)
        for byte in data:
            b = bytes([byte])

            if self.esc == 1:                      # just saw ESC
                if b == b"[":
                    self.esc = 2
                elif b in (b"P", b"]", b"X", b"^", b"_"):
                    self.esc = 3
                else:
                    self.esc = 0
                continue
            if self.esc == 2:                      # inside CSI
                if 0x40 <= byte <= 0x7E:
                    self.esc = 0
                continue
            if self.esc == 3:                      # inside a string escape
                if byte == 0x07:
                    self.esc = 0
                elif byte == 0x1B:
                    self.esc = 4
                continue
            if self.esc == 4:                      # string escape, saw ESC
                self.esc = 0 if b == b"\\" else (4 if byte == 0x1B else 3)
                continue

            if byte == 0x1B:
                self.esc = 1
                continue

            if b in (b"\r", b"\n"):
                line = self.input_buffer.strip()
                self.input_buffer = b""
                text = line.decode("utf-8", "replace").strip()
                if text:
                    self.ever_typed = True
                    self.awaiting_output = True
                    self.row("user_input",
                             detail={"text": text, "length": len(text)})
            elif b == b"\x7f":                     # backspace
                self.input_buffer = self.input_buffer[:-1]
            elif b == b"\x03":                     # ctrl-c
                self.input_buffer = b""
                self.ever_typed = True
                self.row("interrupt", detail={"signal": "SIGINT"},
                         success=False, error_type="user_interrupt")
            elif byte >= 32:
                self.input_buffer += b

    def on_stall(self, seconds):
        self.row(
            "stall",
            latency_ms=int(seconds * 1000),
            detail={"silent_seconds": round(seconds, 1),
                    "note": "no output while process alive"},
        )

    def close(self, exit_code):
        duration_ms = int((time.time() - self.started) * 1000)
        self.row(
            "session_end",
            latency_ms=duration_ms,
            success=(exit_code == 0),
            error_type="" if exit_code == 0 else f"exit_{exit_code}",
            detail={
                "exit_code": exit_code,
                "duration_ms": duration_ms,
                "bytes_typed": self.bytes_in,
                "bytes_printed": self.bytes_out,
            },
        )
        for f in (self.raw, self.txt, self.rows):
            f.close()
        return duration_ms


def terminal_size(fd):
    try:
        return struct.unpack("hhhh", fcntl.ioctl(fd, termios.TIOCGWINSZ, b"\0" * 8))
    except Exception:
        return (24, 80, 0, 0)


def main():
    args = sys.argv[1:]
    if args and args[0] == "--":
        args = args[1:]
    if not args:
        print("usage: signal_wrap.py <command> [args...]", file=sys.stderr)
        return 2

    tool = os.environ.get("SIGNAL_TOOL") or os.path.basename(args[0])
    rec = SessionRecorder(tool, args, os.getcwd())

    stdin_is_tty = sys.stdin.isatty()

    rec.row(
        "session_start",
        detail={
            "command": " ".join(args),
            "cwd": rec.cwd,
            "shell": os.environ.get("SHELL", ""),
            "term": os.environ.get("TERM", ""),
            "interactive": stdin_is_tty,
        },
    )

    pid, master = pty.fork()
    if pid == 0:
        # child: become the real tool
        try:
            os.execvp(args[0], args)
        except FileNotFoundError:
            sys.stderr.write(f"signal-wrap: command not found: {args[0]}\n")
            os._exit(127)

    # parent: relay everything, record as it passes
    if stdin_is_tty:
        rows, cols, _, _ = terminal_size(sys.stdin.fileno())
        fcntl.ioctl(master, termios.TIOCSWINSZ, struct.pack("hhhh", rows, cols, 0, 0))

        def on_resize(*_):
            r, c, _, _ = terminal_size(sys.stdin.fileno())
            fcntl.ioctl(master, termios.TIOCSWINSZ, struct.pack("hhhh", r, c, 0, 0))
        signal.signal(signal.SIGWINCH, on_resize)

        old_attrs = termios.tcgetattr(sys.stdin.fileno())
        tty.setraw(sys.stdin.fileno())
    else:
        old_attrs = None

    last_output = time.time()
    stall_reported = False
    exit_code = 0

    try:
        while True:
            try:
                readable, _, _ = select.select([sys.stdin, master], [], [], 1.0)
            except (InterruptedError, OSError):
                continue

            if sys.stdin in readable:
                data = os.read(sys.stdin.fileno(), 4096)
                if data:
                    rec.on_input(data)
                    os.write(master, data)

            if master in readable:
                try:
                    data = os.read(master, 65536)
                except OSError:
                    break
                if not data:
                    break
                rec.on_output(data)
                os.write(sys.stdout.fileno(), data)
                last_output = time.time()
                stall_reported = False
                if rec.ever_typed:
                    rec.awaiting_output = False

            silent = time.time() - last_output
            waiting_on_tool = (not rec.ever_typed) or rec.awaiting_output
            if silent > STALL_SECONDS and not stall_reported and waiting_on_tool:
                rec.on_stall(silent)
                stall_reported = True
    finally:
        if old_attrs is not None:
            termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, old_attrs)
        try:
            _, status = os.waitpid(pid, 0)
            exit_code = os.WEXITSTATUS(status) if os.WIFEXITED(status) else 1
        except ChildProcessError:
            exit_code = 0
        duration_ms = rec.close(exit_code)

    sys.stderr.write(
        f"\n[signal-wrap] session {rec.session_id[:8]}  tool={tool}  "
        f"exit={exit_code}  {duration_ms/1000:.1f}s  "
        f"{rec.row_count} observable rows -> {rec.base}.jsonl\n"
    )
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
