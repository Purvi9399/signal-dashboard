"""
Codex FS Watcher collector.

Grounded in the real, empirical capture performed for this project.
Mechanism (watchdog Observer + FileSystemEventHandler, JSONL sink) is
structurally the same pattern used across every FS-watcher collector in this
project; ported here from the real Codex-specific script, with the watched
paths grounded in Codex's own confirmed on-disk layout (NOT copied blindly
from Antigravity_FSWatcher, which has a structurally different directory
layout -- see note on WATCH_PATHS below). Primary sources (read-only, never
modified):

  Codex_FSWatcher/RESULTS.md              -- the full write-up this file is built from
  Codex_FSWatcher/fs_watcher.py           -- real, Codex-specific watcher script; exact
                                              record shape this collector's replay mode
                                              consumes ({timestamp, event_type, path,
                                              is_directory, size, watch_root})
  Codex_FSWatcher/directory_structure.md  -- ground-truth map of ~/.codex/ confirming
                                              sessions/, config.toml, hooks.json, auth.json,
                                              history.jsonl, cache/, skills/, shell_snapshots/
  Codex_FSWatcher/captured_fs_events.jsonl,
  Codex_FSWatcher/captured_fs_events_crossref.jsonl
                                           -- real captured watcher output
  Codex_FSWatcher/hooks_crossref_captured_events.jsonl
                                           -- simultaneous Hooks capture used for timing
                                              cross-reference (RESULTS.md section 5)
  codex_hooks.py (this framework)         -- corroborates the real transcript_path pattern
                                              (~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl)
                                              seen inside real Hooks payloads

Mechanism-only reference (NOT the source of any Codex-specific path/finding
below -- only used to confirm the generic watchdog-based structure is a
reasonable one to port):
  Antigravity_FSWatcher/fs_watcher.py

This collector performs NO normalization: each captured filesystem event
keeps exactly the {timestamp, event_type, path, is_directory, size,
watch_root} shape Codex_FSWatcher/fs_watcher.py's own _emit() writes.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Iterator, List, Optional

import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parents[2]))
from collectors.live_fs import LiveFSWatch, QueuedFSHandler, safe_size  # noqa: E402
from collectors.live_http import LiveEventFeed  # noqa: E402
from core import Collector, RawEvent

AGENT_NAME = "codex"
COLLECTOR_ID = "codex.fs_watcher"

# CONFIRMED real on-disk layout (Codex_FSWatcher/RESULTS.md section 1,
# directory_structure.md): unlike Antigravity (which has a separate
# internal `brain/<conversationId>/` staging dir that real user-facing
# output can "escape" from, entirely un-watched), Codex's on-disk footprint
# is ONE unified tree, `~/.codex/` (CODEX_HOME, no env override observed on
# the test machine), and the real user-facing output always lands exactly
# in the working directory passed via `-C` -- there is no equivalent
# staging area to watch separately. So the natural two watch roots for a
# Codex FS watcher are:
#   1. ~/.codex/sessions/  -- transcript rollout files (session/subagent
#      lifecycle signal; see ROLLOUT_PATH_PATTERN below)
#   2. the actual working directory the agent was invoked against -- for
#      the real output files themselves
# This mirrors exactly what Codex_FSWatcher/fs_watcher.py's own real test
# runs watched (its captured_fs_events.jsonl watch_root values are
# `~/.codex/sessions` plus a workspace dir).
def default_watch_paths(workspace_dir: Optional[str] = None) -> List[str]:
    paths = [str(Path.home() / ".codex" / "sessions")]
    if workspace_dir:
        paths.append(workspace_dir)
    return paths

# Real rollout transcript filename pattern, confirmed by both
# Codex_FSWatcher/RESULTS.md section 1 and codex_hooks.py's own
# `transcript_path` field seen in real Hooks payloads:
#   ~/.codex/sessions/YYYY/MM/DD/rollout-<ISO-timestamp>-<session-uuid>.jsonl
# A NEW rollout file is created per session AND per subagent (confirmed:
# subagents get their own independent rollout file, linked back to the
# parent via `forked_from_id` inside the transcript content itself -- not
# visible from the filename or from a bare FS event).
ROLLOUT_PATH_PATTERN = "sessions/*/*/*/rollout-*.jsonl"

# CONFIRMED, IMPORTANT CAVEAT (Codex_FSWatcher/RESULTS.md section 2 + 3):
# the OS (macOS FSEvents) heavily coalesces dozens of individual transcript
# appends into a small, FIXED number of filesystem notifications -- a real
# 42-line parent rollout produced only 2 "modified" events (one right after
# creation, one at session end). Raw event COUNTS are not a usable proxy for
# session activity volume. This collector does not attempt to compensate
# for this (e.g. by re-reading file content to count real appends) -- that
# would require content-reading beyond what a pure FS-event collector does,
# and would risk violating the "no normalization" contract by inventing
# derived counts not actually present in any single raw event.
EVENT_COUNTS_ARE_COALESCED_NOT_RELIABLE = True

# CONFIRMED (Codex_FSWatcher/RESULTS.md section 5): FS events give a WHEN,
# not a WHAT. Tool-level FS events (e.g. apply_patch writing a real output
# file) correlate tightly with Hooks PreToolUse/PostToolUse (sub-200ms), but
# the very FIRST FS event of a session (rollout file `created`) precedes the
# real `SessionStart` hook by ~4 seconds (rollout file is created during
# early process bootstrap, before hook-dispatch machinery is ready) -- a
# watcher-only "first FS event = session start" heuristic is systematically
# ~4s early. End-of-session flush events align much better (~200ms) with
# Stop/SessionEnd. This collector does not correct for this skew -- it is a
# real property of the raw signal, not a bug to paper over here.
SESSION_START_FS_EVENT_PRECEDES_HOOK_SESSIONSTART_BY_SECONDS = 4.2  # observed, not guaranteed


def find_codex_home() -> Path:
    return Path.home() / ".codex"


class CodexFSWatcherCollector(Collector):
    """Filesystem-watcher-based collector for the Codex CLI (OpenAI),
    watching `~/.codex/sessions/` (transcript rollout files) plus an
    optional real working directory, per Codex_FSWatcher/RESULTS.md.

    Live mode (real deployment): activate() would start a `watchdog`
    Observer recursively watching default_watch_paths(), exactly as
    Codex_FSWatcher/fs_watcher.py does, appending
    {timestamp, event_type, path, is_directory, size, watch_root} records
    to a JSONL sink for created/modified/deleted/moved events. capture()
    would then tail that sink.

    This build supports feeding capture() from already-captured JSONL
    records (Codex_FSWatcher/captured_fs_events.jsonl and friends) to
    exercise it deterministically against real sample data, without a live
    watchdog Observer or actual filesystem activity.
    """

    def __init__(
        self,
        replay_events: Optional[List[dict]] = None,
        watch_paths: Optional[List[str]] = None,
    ) -> None:
        self._replay_events = replay_events
        self._watch_paths = watch_paths or default_watch_paths()
        self._activated = False
        self._live_feed: Optional[LiveEventFeed] = None
        self._watch: Optional[LiveFSWatch] = None

    @property
    def collector_id(self) -> str:
        return COLLECTOR_ID

    def detect(self) -> bool:
        """Real, cheap heuristics for "is Codex CLI present, with a
        watchable on-disk footprint, on this machine", per
        Codex_FSWatcher/RESULTS.md:
          1. `codex` binary on PATH (consistent with the other Codex
             collectors).
          2. ~/.codex directory exists (CODEX_HOME).
          3. ~/.codex/sessions/ directory exists (proof at least one real
             session has run and left a watchable transcript tree --
             confirmed real path per directory_structure.md and every
             captured Hooks transcript_path value).
        Unlike hooks.json/config.toml's [otel] table (which are themselves
        the mechanism being detected), `~/.codex/sessions/` existing is
        evidence of past Codex USE, not of any watcher-specific
        configuration -- there is nothing to configure for this mechanism
        (no Codex-side opt-in at all, confirmed in RESULTS.md section 0:
        "standalone watchdog-based script, zero dependency on
        Hooks/OTel/MCP Proxy").
        """
        found_any = False

        if shutil.which("codex") is not None:
            found_any = True

        codex_home = find_codex_home()
        if codex_home.is_dir():
            found_any = True

        if (codex_home / "sessions").is_dir():
            found_any = True

        return found_any

    def activate(self) -> None:
        """No precondition-checking trap here -- FS watching needs no
        Codex-side trust/config state (unlike codex_hooks.py's trust gate).
        The only real caveat is documented above
        (EVENT_COUNTS_ARE_COALESCED_NOT_RELIABLE,
        SESSION_START_FS_EVENT_PRECEDES_HOOK_SESSIONSTART_BY_SECONDS) --
        informational, not blocking.
        """
        self._activated = True
        if self._replay_events is not None:
            return
        self._start_live()

    def _start_live(self) -> None:
        feed = LiveEventFeed()
        handler = QueuedFSHandler(feed, extra_for=self._codex_extra)
        watch = LiveFSWatch(feed, handler)
        watch.start(self._watch_paths)
        self._live_feed = feed
        self._watch = watch

    def _root_for(self, path: str) -> str:
        resolved = str(Path(path).resolve())
        for root in self._watch_paths:
            prefix = str(Path(root).resolve())
            if resolved == prefix or resolved.startswith(prefix + "/"):
                return root
        return self._watch_paths[0] if self._watch_paths else ""

    def _codex_extra(self, _event_type: str, path: str) -> dict:
        return {"watch_root": self._root_for(path), "size": safe_size(path)}

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
        watch = self._watch
        self._watch = None
        if watch is not None:
            watch.stop()


def _load_sample_fs_events(max_events: int = 8) -> List[dict]:
    """Best-effort loader for real captured FS watcher events out of
    Codex_FSWatcher/captured_fs_events.jsonl, for the __main__ demo below.
    """
    candidates = [
        Path(__file__).resolve().parents[4] / "Codex_FSWatcher" / "captured_fs_events.jsonl",
        Path("/Users/malharshinde/Desktop/SOAISEC/Project_Signal/Codex_FSWatcher/captured_fs_events.jsonl"),
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
    collector = CodexFSWatcherCollector()

    print(f"[{collector.collector_id}] detect() -> {collector.detect()}")
    print(f"[{collector.collector_id}] default watch paths -> {collector._watch_paths}")

    sample_events = _load_sample_fs_events(max_events=8)
    if sample_events:
        print(f"[{collector.collector_id}] loaded {len(sample_events)} real captured FS "
              f"events from Codex_FSWatcher/captured_fs_events.jsonl")
        collector = CodexFSWatcherCollector(replay_events=sample_events)
    else:
        print(f"[{collector.collector_id}] captured_fs_events.jsonl not reachable from this "
              f"checkout -- capture() will yield nothing (no synthetic fallback).")

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
        print(f"event_type:   {event.raw_payload.get('event_type')}")
        print(f"path:         {event.raw_payload.get('path')}")
        print(f"size:         {event.raw_payload.get('size')}")
        if count >= 8:
            break

    collector.deactivate()
    print(f"\n[{collector.collector_id}] deactivate() ok. Total events yielded: {count}")
