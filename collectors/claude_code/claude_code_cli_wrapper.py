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

import getpass
import json
import os
import shutil
import socket
import sys
from pathlib import Path
from typing import Iterator, List, Optional

_PYWINPTY_AVAILABLE = False
if sys.platform == "win32":
    try:
        from winpty import PtyProcess as _WinPtyProcess  # noqa: F401

        _PYWINPTY_AVAILABLE = True
    except ImportError:
        _WinPtyProcess = None  # type: ignore[assignment]

import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parents[2]))
from collectors.live_cli import LivePtySession  # noqa: E402
from collectors.live_http import LiveEventFeed  # noqa: E402
from core import Collector, RawEvent

AGENT_NAME = "claude_code"
COLLECTOR_ID = "claude_code.cli_wrapper"


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




def _claude_extra() -> dict:
    return {
        "operator_username": operator_username(),
        "hostname": collector_hostname(),
    }


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
        self._live_feed: Optional[LiveEventFeed] = None
        self._session: Optional[LivePtySession] = None

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
        fork+exec the wrapped command under a PTY (LIVE_CLI.md).
        """
        self._activated = True
        if self._replay_events is not None:
            return
        self._start_live()

    def _start_live(self) -> None:
        feed = LiveEventFeed()
        self._live_feed = feed
        session = LivePtySession(self._command, feed, extra_for=_claude_extra)
        session.start()
        self._session = session

    def capture(self) -> Iterator[RawEvent]:
        if not self._activated:
            raise RuntimeError(f"{self.collector_id}: capture() called before activate()")
        source = self._event_source()
        if source is None:
            return
        for chunk in source:
            yield RawEvent(
                timestamp=RawEvent.now_iso(),
                collector_id=self.collector_id,
                agent=AGENT_NAME,
                raw_payload=chunk,
            )

    def _event_source(self):
        if self._replay_events is not None:
            return self._replay_events
        return self._live_feed

    def deactivate(self) -> None:
        self._activated = False
        session = self._session
        self._session = None
        if session is not None:
            session.stop()
            return
        feed = self._live_feed
        if feed is not None:
            feed.stop()

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
        "\nNOTE: live-mode spawn was intentionally NOT exercised here -- "
        "it requires an authenticated `claude` binary. Demonstrating "
        "operator_username()/collector_hostname() directly instead:"
    )
    print(f"  operator_username() -> {operator_username()!r}")
    print(f"  collector_hostname() -> {collector_hostname()!r}")
