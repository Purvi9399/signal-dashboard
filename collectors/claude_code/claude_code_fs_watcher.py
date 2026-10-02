"""
Claude Code Filesystem Watcher collector.

UNLIKE the other three new Claude Code collectors in this batch (OTel, CLI
Wrapper, MCP Proxy), this one has REAL, on-this-machine, directly-verified
ground truth to build on -- Claude Code's own transcript files -- so it is
NOT flagged UNVERIFIED/BEST-EFFORT the way copilot_fs_watcher.py had to be.

WATCH ROOT: `~/.claude/projects/<project-dir>/<session-id>.jsonl`

  This is the exact path claude_code_hooks.py already extracts as
  `transcript_path` from every real hook payload (see extract_hook_event_name
  neighbors in that file and projectsetup/backend/webhook_server.py's
  claude_event() handler, which reads `data.get("transcript_path")` off
  every hook POST). Verified directly on this machine:
    $ find ~/.claude/projects -iname "*.jsonl"
    /Users/malharshinde/.claude/projects/-Users-malharshinde-Documents-Expenditure-Tracker/01b9e91f-....jsonl
    ... (multiple real session transcript files, one per project-dir/session)
  Directory-name encoding (`-Users-malharshinde-Desktop-...`) is the real,
  observed convention: the project's absolute path with `/` replaced by `-`.

WHAT'S EXTRACTED FROM EACH TRANSCRIPT LINE, AND WHERE THAT'S GROUNDED:
  projectsetup/backend/webhook_server.py's `_parse_claude_transcript()`
  (lines ~2295-2420) is the real, working parser this project already has
  for these exact files -- read in full while building this collector. Its
  logic (NOT reimplemented wholesale here, since normalizing/summing tokens
  is a later pipeline stage's job per core.py's scope note, but its field
  NAMES are the direct grounding for this collector's field-level docs and
  its own real sample line at the top of this file's __main__ block) shows:
    - `msg.get("usage")` / `log.get("usage")` dict, with real keys
      input_tokens, output_tokens, cache_read_input_tokens,
      cache_creation_input_tokens -- summed "per assistant line" by that
      parser. Matches the CSV's row: input_tokens/output_tokens/
      cache_read_tokens/cache_write_tokens all cited as "FS watcher" for
      Claude Code, Note: "THE KEY ASYMMETRY. CC in the transcript, Cursor
      in the hook payload." -- i.e. per the CSV, these fields are NOT on
      Claude Code's hook payloads at all, only recoverable by watching/
      reading this transcript file. This collector's raw_payload preserves
      the untouched `usage` sub-dict exactly as it appears on each line,
      so a later stage can do that summing -- this collector does not sum
      anything itself (no normalization).
    - `content` blocks of `type == "thinking"` inside an assistant message
      -- matches CSV row "thinking_blocks": "FS watcher" / Note: "Counted
      from the transcript." and "reasoning_text": "FS watcher" / Note: "CC
      thinking blocks live in the transcript."
    - `msg.get("model")` on assistant lines -- real field, used by
      `_parse_claude_transcript()` to set `state["dynamic_model"]`.
    - `log.get("agentId")` / `log.get("isSidechain")` -- real fields the
      existing parser uses to attribute sub-agent transcript lines
      correctly (trusted over hook attribution per that function's own
      comment: "Claude tags every sub-agent (sidechain) entry with the
      owning agentId... Trust it over the hook attribution").
    - `log.get("type") == "system", log.get("subtype") == "turn_duration"`
      with a `durationMs` field -- real system-line shape observed in that
      parser.
  This collector's capture() does NOT parse/extract/normalize any of the
  above out of raw_payload -- per core.py's hard "raw_payload must stay
  completely unmodified" constraint, each yielded RawEvent's raw_payload is
  one untouched parsed JSON line from the transcript file, exactly as
  `_parse_claude_transcript()` itself reads it (`json.loads(line)`) before
  that function goes on to do its own (out-of-scope-for-this-collector)
  extraction. The field citations above document WHAT a downstream
  normalizer would pull out of these lines, grounding why this watch
  target matters, not what this collector itself computes.

MECHANISM: adapted from Antigravity_FSWatcher/fs_watcher.py (the generic
watchdog-based recursive fs-watching approach: on_created/on_modified/
on_deleted/on_moved -> one JSON record per fs event) -- reused purely as a
MECHANISM reference. Since transcripts are APPENDED to (not replaced) while
a session is live, this collector's live-mode handler additionally tails
newly-appended lines out of any watched file it sees `modified` events for
(mirroring `_parse_claude_transcript()`'s own real `incremental`/
`transcript_offset` resume-from-last-line approach), so raw_payload for a
transcript file carries actual parsed transcript LINES, not just a bare fs
"modified" stat record the way copilot_fs_watcher.py's weaker/unverified
watch necessarily stayed at.

No normalization: raw_payload is either (a) one untouched parsed JSON
object from a transcript line (`json.loads(line)`, exactly as
`_parse_claude_transcript()` itself parses each line), for txt-append
events on a `.jsonl` transcript file, or (b) the same generic
{event_type, path, is_directory, size_bytes} shape Antigravity_FSWatcher's
Handler._log() produces, for any other fs event (e.g. a new session file
being created) where there is no new line content yet to parse.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Iterator, List, Optional

import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parents[2]))
from collectors.live_fs import (  # noqa: E402
    LiveFSWatch,
    QueuedFSHandler,
    WATCHDOG_AVAILABLE,
)
from collectors.live_http import LiveEventFeed  # noqa: E402
from core import Collector, RawEvent

AGENT_NAME = "claude_code"
COLLECTOR_ID = "claude_code.fs_watcher"

# Real, directly-verified watch root on this machine -- see module docstring.
# Claude Code writes one JSONL transcript file per session, nested under a
# per-project directory whose name is the project's absolute path with `/`
# replaced by `-` (observed convention, e.g.
# ~/.claude/projects/-Users-malharshinde-Desktop-SOAISEC-Project-Signal-.../<uuid>.jsonl).
CLAUDE_PROJECTS_ROOT = os.path.expanduser("~/.claude/projects")

WATCH_ROOTS = [CLAUDE_PROJECTS_ROOT]


def read_new_transcript_lines(path: str, start_offset_lines: int) -> List[dict]:
    """Read and parse any transcript lines appended since start_offset_lines,
    mirroring projectsetup/backend/webhook_server.py's
    `_parse_claude_transcript()` incremental/`transcript_offset` resume
    logic -- but, per core.py's scope boundary, this collector stops at
    `json.loads(line)` and does NOT go on to extract/sum/normalize fields
    the way that function does; that is a later pipeline stage's job.
    Malformed lines (mid-write partial JSON, a real possibility for a file
    being actively appended to) are skipped, same as that function's own
    `try: json.loads(line) except Exception: continue`.
    """
    lines_out: List[dict] = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line_no, line in enumerate(f):
                if line_no < start_offset_lines:
                    continue
                line = line.strip()
                if not line:
                    continue
                try:
                    lines_out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except OSError:
        pass
    return lines_out


class ClaudeCodeFSWatcherCollector(Collector):
    """Filesystem-watcher collector for Claude Code's transcript directory
    (~/.claude/projects/<project-dir>/<session-id>.jsonl -- real,
    directly-verified path, see module docstring).

    Live mode (real deployment, requires the third-party `watchdog`
    package, NOT bundled with this stdlib-only framework): activate()
    starts a watchdog Observer on ~/.claude/projects; capture() drains
    parsed transcript lines (for `.jsonl` append events) and generic fs
    events (for anything else, e.g. a brand-new session file being
    created) the handler collects.

    This build also supports feeding capture() from an already-captured
    JSONL records list (either real transcript lines read directly off
    this machine's own ~/.claude/projects files -- see __main__ below,
    which does exactly that -- or a generic fs-event record list) to
    exercise it deterministically.
    """

    def __init__(
        self,
        replay_events: Optional[List[dict]] = None,
        watch_roots: Optional[List[str]] = None,
    ) -> None:
        self._replay_events = replay_events
        self._watch_roots = watch_roots or list(WATCH_ROOTS)
        self._activated = False
        self._live_feed: Optional[LiveEventFeed] = None
        self._watch: Optional[LiveFSWatch] = None
        self._line_offsets: dict = {}

    @property
    def collector_id(self) -> str:
        return COLLECTOR_ID

    def detect(self) -> bool:
        """Real, cheap, side-effect-free check: does ~/.claude/projects
        exist on this machine? (Directory existence check only -- no
        reads of transcript contents, no network.) This directory is
        created by Claude Code itself the first time any session runs
        (same lifecycle claude_code_hooks.py's detect() relies on for
        ~/.claude existing at all)."""
        return any(Path(root).is_dir() for root in self._watch_roots)

    def activate(self) -> None:
        self._activated = True
        if self._replay_events is not None:
            return
        self._start_live()

    def _start_live(self) -> None:
        feed = LiveEventFeed()
        handler = QueuedFSHandler(feed, tail_file=self._tail)
        watch = LiveFSWatch(feed, handler)
        watch.start(self._watch_roots)
        self._live_feed = feed
        self._watch = watch

    def _tail(self, path: str) -> Optional[List[dict]]:
        if not path.endswith(".jsonl"):
            return None
        offset = self._line_offsets.get(path, 0)
        new_lines = read_new_transcript_lines(path, offset)
        self._line_offsets[path] = offset + len(new_lines)
        return new_lines

    def capture(self) -> Iterator[RawEvent]:
        if not self._activated:
            raise RuntimeError(f"{self.collector_id}: capture() called before activate()")
        source = self._event_source()
        if source is None:
            return
        for record in source:
            yield RawEvent(
                timestamp=RawEvent.now_iso(),
                collector_id=self.collector_id,
                agent=AGENT_NAME,
                raw_payload=record,
            )

    def _event_source(self):
        if self._replay_events is not None:
            return self._replay_events
        return self._live_feed

    def deactivate(self) -> None:
        self._activated = False
        watch = self._watch
        self._watch = None
        if watch is not None:
            watch.stop()


if __name__ == "__main__":
    print("=== ClaudeCodeFSWatcherCollector standalone test ===")
    print(f"Watch root: {CLAUDE_PROJECTS_ROOT}")
    print(f"watchdog package available for live mode: {WATCHDOG_AVAILABLE}")

    collector = ClaudeCodeFSWatcherCollector()
    detected = collector.detect()
    print(f"\ndetect() -> {detected}")

    # Find a REAL transcript file on this machine (not synthetic) to replay
    # from -- ~/.claude/projects genuinely exists here with real session
    # data, unlike the Copilot fs-watcher case which had nothing real to
    # point at for Copilot specifically.
    real_lines: List[dict] = []
    source_file = None
    projects_root = Path(CLAUDE_PROJECTS_ROOT)
    if projects_root.is_dir():
        for jsonl_path in projects_root.rglob("*.jsonl"):
            try:
                with open(jsonl_path, "r", encoding="utf-8") as f:
                    lines = [json.loads(l) for l in f if l.strip()]
            except (OSError, json.JSONDecodeError):
                continue
            if lines:
                source_file = jsonl_path
                real_lines = lines[:8]
                break

    if source_file is not None:
        print(
            f"\nSIMULATED FROM CAPTURED DATA (REAL transcript lines read "
            f"directly off this machine's own {source_file}, the exact "
            f"file/path shape this collector watches for):"
        )
        replay_collector = ClaudeCodeFSWatcherCollector(replay_events=real_lines)
        replay_collector.activate()
        for i, event in enumerate(replay_collector.capture()):
            payload = event.raw_payload
            kind = payload.get("type", "?")
            usage = None
            msg = payload.get("message") if isinstance(payload.get("message"), dict) else {}
            if isinstance(msg, dict):
                usage = msg.get("usage")
            print(
                f"  [{i}] collector_id={event.collector_id!r} agent={event.agent!r} "
                f"line_type={kind!r} has_usage={usage is not None}"
            )
        replay_collector.deactivate()
    else:
        print(
            "\nNo real transcript file with parseable lines found under "
            f"{CLAUDE_PROJECTS_ROOT} on this machine -- falling back to a "
            "clearly-labeled synthetic placeholder shaped like the real "
            "fields documented in projectsetup/backend/webhook_server.py's "
            "_parse_claude_transcript():"
        )
        synthetic = [
            {
                "_synthetic_placeholder": True,
                "_note": "No real transcript file found on this machine at capture time.",
                "type": "assistant",
                "isSidechain": False,
                "message": {
                    "role": "assistant",
                    "model": "claude-sonnet-5",
                    "usage": {
                        "input_tokens": 0,
                        "output_tokens": 0,
                        "cache_read_input_tokens": 0,
                        "cache_creation_input_tokens": 0,
                    },
                    "content": [{"type": "thinking", "thinking": "placeholder"}],
                },
            }
        ]
        replay_collector = ClaudeCodeFSWatcherCollector(replay_events=synthetic)
        replay_collector.activate()
        for ev in replay_collector.capture():
            print(f"  {ev}")
        replay_collector.deactivate()
