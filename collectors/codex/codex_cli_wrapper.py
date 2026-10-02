"""
Codex CLI Wrapper (pty passive-capture) collector.

Grounded in the real, empirical capture performed for this project, and
adapted (not copied) from the generic pty-wrapper mechanism demonstrated in
cli_wrapper_demo/wrapper.py + receiver.py. Primary sources (read-only, never
modified):

  Codex_CLIWrapper/RESULTS.md                    -- the full write-up this file is built from
  Codex_CLIWrapper/wrapper.py                     -- real, Codex-specific wrapper (itself
                                                      adapted from Antigravity's, per its own
                                                      docstring) -- exact JSONL record shape
                                                      this collector's replay mode consumes
  Codex_CLIWrapper/captured_output_exec_json.jsonl       -- `codex exec --json` real capture
  Codex_CLIWrapper/captured_output_exec_plaintext.jsonl  -- `codex exec` (no --json) real capture
  Codex_CLIWrapper/interactive_tui_raw.log / interactive_clean.txt
                                                   -- interactive TUI capture, heavy ANSI noise
  cli_wrapper_demo/wrapper.py, receiver.py, README.md
                                                   -- generic mechanism reference only (pty +
                                                      stdin passthrough + JSONL append); Codex's
                                                      own wrapper.py is the concrete blueprint
                                                      actually ported here, not this demo

This collector performs NO normalization: each captured chunk keeps exactly
the {timestamp, stream, content, wrapped_command} shape Codex_CLIWrapper/
wrapper.py actually writes (confirmed by inspecting its `emit()` function and
the real *.jsonl captures), including raw ANSI escape sequences in
interactive-mode content. `codex exec --json` mode additionally has
line-structured JSON *inside* `content` (Codex's own `item.started` /
`item.completed` event stream) -- this collector does not parse or promote
that inner JSON out of `content`; it stays exactly as captured.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Iterator, List, Optional

import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parents[2]))
from collectors.live_cli import LivePtySession  # noqa: E402
from collectors.live_http import LiveEventFeed  # noqa: E402
from core import Collector, RawEvent

AGENT_NAME = "codex"
COLLECTOR_ID = "codex.cli_wrapper"

# THE KEY FINDING this collector exists to surface (Codex_CLIWrapper/
# RESULTS.md section 0 + 3): unlike codex_hooks.py's PostToolUse
# (tool_response has no exit-code field) and codex_otel.py's
# codex.tool_result (success hardcoded "true"), `codex exec --json`'s raw
# terminal output DOES carry an accurate, structured exit_code/status on
# each command_execution item -- e.g.:
#   {"type":"item.completed","item":{"id":"item_6","type":"command_execution",
#    "command":"/bin/zsh -lc false","aggregated_output":"","exit_code":1,
#    "status":"failed"}}
# This is the first place in the whole Codex collector series where tool
# failure is reported correctly and unambiguously by a structured field.
# `codex exec` plain-text mode is also correct, via readable text
# ("succeeded in Nms:" vs "exited N in Nms:"). Interactive TUI mode is the
# OPPOSITE: a failing no-output command renders identically
# ("(no output)") to a successful no-output command -- no exit code, no
# color, no failure marker at the UI-element level (RESULTS.md section 3c).
# This collector does not attempt to reconcile these -- raw_payload stays
# whatever the wrapper actually captured for whichever mode was run.
MODES_WITH_STRUCTURED_OR_TEXTUAL_FAILURE_SIGNAL = ("exec_json", "exec_plaintext")
MODE_WITH_NO_RELIABLE_FAILURE_SIGNAL = "interactive_tui"


def find_codex_home() -> Path:
    return Path.home() / ".codex"


class CodexCLIWrapperCollector(Collector):
    """pty-wrapper-based collector for the Codex CLI (OpenAI).

    Live mode (real deployment): activate() would spawn `codex` (any of
    `codex exec --json ...`, `codex exec ...`, or bare `codex` interactive)
    behind a pseudo-TTY exactly as Codex_CLIWrapper/wrapper.py does --
    forwarding real stdin transparently, mirroring child output back to the
    real terminal, and appending every captured chunk to a JSONL file as
    {timestamp, stream, content, wrapped_command}. capture() would then
    tail/drain that file.

    This build supports feeding capture() from already-captured JSONL chunks
    (Codex_CLIWrapper/captured_output_exec_json.jsonl and friends) to
    exercise it deterministically against real sample data, without
    re-spawning a live pty child process.
    """

    def __init__(
        self,
        replay_events: Optional[List[dict]] = None,
        command: Optional[List[str]] = None,
    ) -> None:
        self._replay_events = replay_events
        self._command = command
        self._activated = False
        self._live_feed: Optional[LiveEventFeed] = None
        self._session: Optional[LivePtySession] = None

    @property
    def collector_id(self) -> str:
        return COLLECTOR_ID

    def detect(self) -> bool:
        """Real, cheap heuristics for "is Codex CLI present on this
        machine", per Codex_CLIWrapper/RESULTS.md (this collector wraps the
        same `codex` binary Hooks/OTel target, it just captures a different
        channel -- raw pty output rather than hook POSTs or OTLP exports):
          1. `codex` binary on PATH (same check as codex_hooks.py /
             codex_otel.py, for consistency across the whole Codex family).
          2. ~/.codex directory exists (CODEX_HOME).
        Unlike Hooks/OTel, there is no dedicated config file to check here
        (the wrapper mechanism needs no Codex-side configuration at all --
        Codex_CLIWrapper/RESULTS.md section 1 confirms it's purely an
        external pty wrapper around the unmodified `codex` binary), so
        detect() cannot get a third, stronger signal the way the other two
        collectors can from hooks.json / config.toml's [otel] table.
        """
        found_any = False

        if shutil.which("codex") is not None:
            found_any = True

        if find_codex_home().is_dir():
            found_any = True

        return found_any

    def activate(self) -> None:
        """No precondition-checking trap here (unlike codex_hooks.py's
        trust gate) -- the pty wrapper needs no Codex-side trust/config
        state at all; it passively captures whatever the wrapped process
        writes, confirmed to "just work" across all three surfaces tested
        (Codex_CLIWrapper/RESULTS.md section 1, "Wrapper transparency").
        """
        self._activated = True
        if self._replay_events is not None:
            return
        self._start_live()

    def _start_live(self) -> None:
        feed = LiveEventFeed()
        self._live_feed = feed
        if self._command is None:
            return
        session = LivePtySession(self._command, feed)
        session.start()
        self._session = session

    def capture(self) -> Iterator[RawEvent]:
        if not self._activated:
            raise RuntimeError(f"{self.collector_id}: capture() called before activate()")
        source = self._event_source()
        if source is None:
            return
        for payload in source:
            yield RawEvent(
                timestamp=RawEvent.now_iso(),
                collector_id=self.collector_id,
                agent=AGENT_NAME,
                raw_payload=payload,
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


def _load_sample_wrapper_chunks(max_events: int = 5) -> List[dict]:
    """Best-effort loader for a few real captured pty-wrapper chunks out of
    Codex_CLIWrapper/captured_output_exec_json.jsonl, for the __main__ demo
    below. Returns [] if unreachable from this checkout.
    """
    candidates = [
        Path(__file__).resolve().parents[4] / "Codex_CLIWrapper" / "captured_output_exec_json.jsonl",
        Path("/Users/malharshinde/Desktop/SOAISEC/Project_Signal/Codex_CLIWrapper/captured_output_exec_json.jsonl"),
    ]
    for path in candidates:
        if path.is_file():
            events = []
            try:
                with path.open() as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        events.append(json.loads(line))
                        if len(events) >= max_events:
                            break
            except (json.JSONDecodeError, OSError):
                continue
            return events
    return []


if __name__ == "__main__":
    collector = CodexCLIWrapperCollector()

    print(f"[{collector.collector_id}] detect() -> {collector.detect()}")

    sample_events = _load_sample_wrapper_chunks(max_events=5)
    if sample_events:
        print(f"[{collector.collector_id}] loaded {len(sample_events)} real captured "
              f"pty chunks from Codex_CLIWrapper/captured_output_exec_json.jsonl")
        collector = CodexCLIWrapperCollector(replay_events=sample_events)
    else:
        print(f"[{collector.collector_id}] captured_output_exec_json.jsonl not reachable "
              f"from this checkout -- capture() will yield nothing (no synthetic fallback).")

    collector.activate()
    print(f"[{collector.collector_id}] activate() ok")

    count = 0
    for event in collector.capture():
        count += 1
        label = "SIMULATED FROM CAPTURED DATA"
        print(f"\n--- RawEvent #{count} ({label}) ---")
        print(f"timestamp:    {event.timestamp}")
        print(f"collector_id: {event.collector_id}")
        print(f"agent:        {event.agent}")
        print(f"stream:       {event.raw_payload.get('stream')}")
        content_preview = (event.raw_payload.get("content") or "")[:120].replace("\n", "\\n")
        print(f"content[:120]: {content_preview!r}")
        if count >= 5:
            break

    collector.deactivate()
    print(f"\n[{collector.collector_id}] deactivate() ok. Total events yielded: {count}")
