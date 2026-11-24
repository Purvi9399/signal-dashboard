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

import json
import shutil
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

AGENT_NAME = "antigravity"
COLLECTOR_ID = "antigravity.cli_wrapper"

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
        self._live_feed: Optional[LiveEventFeed] = None
        self._session: Optional[LivePtySession] = None

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
        """If a replay_path was given, activation is a no-op. Otherwise
        spawn under a PTY (LIVE_CLI.md)."""
        self._activated = True
        if self._replay_path is not None:
            return
        self._start_live()

    def _start_live(self) -> None:
        feed = LiveEventFeed()
        self._live_feed = feed
        cmd = self._command or ["agy", "--output-format", RECOMMENDED_OUTPUT_FORMAT]
        session = LivePtySession(cmd, feed, merge_stderr=True)
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
        if self._replay_path is not None:
            return _load_jsonl(self._replay_path)
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
