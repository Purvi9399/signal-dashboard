"""
Antigravity Filesystem Watcher collector.

Grounded in the real, empirical test performed for this project. Primary
sources (read-only, never modified):

  Antigravity_FSWatcher/RESULTS.md -- full write-up: a real `watchdog`-based
    recursive watch over both of Antigravity's real per-surface "brain"
    roots, run alongside a live session that included plain tool calls
    and a subagent delegation, 78 real raw events captured.
  Antigravity_FSWatcher/fs_watcher.py -- the actual watcher used, unmodified
    logic ported here: watches both WATCH_ROOTS recursively via
    watchdog.observers.Observer, logs on_created/on_modified/on_deleted/
    on_moved to a JSONL file with the exact
    {timestamp, event_type, path, is_directory, size_bytes[, dest_path]}
    shape.
  Antigravity_FSWatcher/captured_fs_events.jsonl -- 78 real captured events
    from one live session (parent conversation + subagent conversation,
    both under ~/.gemini/antigravity-cli/brain/).
  Antigravity_FSWatcher/directory_structure.md -- ground-truth layout:
    <root>/<conversationId>/{.system_generated/{logs/{transcript.jsonl,
    transcript_full.jsonl}, messages/ (only if delegation/messaging
    occurred)}, .user_uploaded/, scratch/} -- confirmed identical shape
    across all 13 real conversations examined.
  Antigravity_FSWatcher/sample_transcript_parent.jsonl,
  sample_message.json -- real transcript-line and inter-agent-message
    content shapes (referenced in comments below; NOT parsed/injected into
    raw_payload by this collector, since the real fs_watcher.py never
    reads file content either -- see KNOWN LIMITATION).

HEADLINE FINDING this collector's design reflects (RESULTS.md section 0
and section 3a): FS Watcher alone tells you WHEN something happened
(sub-200ms real-time latency, confirmed via a Hooks timing cross-reference
in RESULTS.md section 6) and gives strong structural signals (new
conversation = new top-level UUID dir; delegation = new NESTED UUID dir
appearing mid-session; live message-queue transitions under
.system_generated/messages/undelivered/ that exist for a fraction of a
second and are invisible to every other collector in this series) -- but
it CANNOT tell you WHAT happened (tool name, args, output) without parsing
file content, which this collector deliberately does NOT do, matching the
real fs_watcher.py's own scope and the framework's no-normalization
constraint. It is also structurally blind to the agent's actual
deliverable files, which land in a global scratch path OUTSIDE the watched
brain/ roots entirely (RESULTS.md section 3a) -- flagged here, not
silently "fixed" by widening the watch scope beyond what was actually
tested.
"""

from __future__ import annotations

import json
import os
import shutil
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, List, Optional

import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parents[2]))
from core import Collector, RawEvent

AGENT_NAME = "antigravity"
COLLECTOR_ID = "antigravity.fs_watcher"

# Real watch roots, confirmed in Antigravity_FSWatcher/fs_watcher.py and
# cross-checked against directory_structure.md: two entirely separate
# roots, one per surface (IDE vs CLI), NOT workspace-relative -- both live
# under the global ~/.gemini/ tree.
WATCH_ROOTS = [
    Path.home() / ".gemini" / "antigravity" / "brain",       # IDE surface
    Path.home() / ".gemini" / "antigravity-cli" / "brain",   # CLI surface
]


def _safe_size(path: str) -> Optional[int]:
    try:
        return os.path.getsize(path)
    except OSError:
        return None


def _load_jsonl(path: Path) -> List[dict]:
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


class AntigravityFSWatcherCollector(Collector):
    """Standalone filesystem-event collector for Antigravity's real
    per-conversation "brain" directories.

    Live mode (real deployment): activate() starts a watchdog Observer
    recursively over both real WATCH_ROOTS (mirroring
    Antigravity_FSWatcher/fs_watcher.py's Handler exactly), pushing every
    on_created/on_modified/on_deleted/on_moved event into an internal
    queue in the exact real captured shape:
      {timestamp, event_type, path, is_directory, size_bytes[, dest_path]}
    capture() drains that queue as a live generator.

    Requires the third-party `watchdog` package for live mode (same
    dependency the real fs_watcher.py used) -- NOT stdlib, so this is
    imported lazily inside activate()/only for live mode; replay mode
    (feeding capture() from a real captured_fs_events.jsonl) requires no
    extra dependency, keeping this file importable even where `watchdog`
    isn't installed, and the __main__ block below exercises replay mode
    so `python3 antigravity_fs_watcher.py` works standalone.

    KNOWN LIMITATION (RESULTS.md section 3, 3a -- not solved here, flagged
    per the framework's no-guessing rule): this collector, like the real
    one it's ported from, does NOT read file content -- it cannot tell you
    which tool ran or what it did, only that transcript.jsonl grew by N
    bytes. It also never sees the agent's actual deliverable files, which
    the CLI Wrapper test confirmed land in a *global* scratch path (not
    under either watched brain/ root) whenever no explicit
    project/workspace is active -- a real architectural blind spot, not an
    oversight in this port.
    """

    def __init__(
        self,
        watch_roots: Optional[List[Path]] = None,
        replay_path: Optional[Path] = None,
    ) -> None:
        self._watch_roots = watch_roots or WATCH_ROOTS
        self._replay_path = replay_path
        self._activated = False
        self._observer = None
        self._events: "list[dict]" = []
        self._lock = threading.Lock()

    @property
    def collector_id(self) -> str:
        return COLLECTOR_ID

    def detect(self) -> bool:
        """Real, cheap filesystem check: True iff at least one of the two
        real watched brain/ roots already exists on disk (confirmed real
        paths, Antigravity_FSWatcher/directory_structure.md section
        "Two separate roots, one per surface"). Unlike antigravity_hooks.py
        / antigravity_mcp_proxy.py, this does NOT create the directory as
        a side effect during detect() (detect() must stay side-effect-free
        per core.py's contract) -- directory creation, if desired, belongs
        in activate() only, mirroring the real fs_watcher.py's own
        os.makedirs() call which happens in main(), not at import time.
        """
        return any(root.is_dir() for root in self._watch_roots)

    def activate(self) -> None:
        """Starts a real watchdog Observer over whichever of the two real
        roots exist (creating them if missing, exactly as the real
        fs_watcher.py's main() does via os.makedirs(root, exist_ok=True),
        so a freshly-installed Antigravity with no conversations yet still
        gets watched from the start). No-op in replay mode."""
        if self._replay_path is not None:
            self._activated = True
            return

        try:
            from watchdog.observers import Observer
            from watchdog.events import FileSystemEventHandler
        except ImportError as exc:
            raise RuntimeError(
                f"{self.collector_id}: live mode requires the third-party "
                f"'watchdog' package (pip install watchdog) -- same "
                f"dependency the real Antigravity_FSWatcher/fs_watcher.py "
                f"used. Use replay_path= for replay-only testing."
            ) from exc

        events_ref = self._events
        lock_ref = self._lock

        class _Handler(FileSystemEventHandler):
            def _log(self, event_type, path, is_directory, extra=None):
                record = {
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "event_type": event_type,
                    "path": path,
                    "is_directory": is_directory,
                    "size_bytes": None if is_directory else _safe_size(path),
                }
                if extra:
                    record.update(extra)
                with lock_ref:
                    events_ref.append(record)

            def on_created(self, event):
                self._log("created", event.src_path, event.is_directory)

            def on_modified(self, event):
                self._log("modified", event.src_path, event.is_directory)

            def on_deleted(self, event):
                self._log("deleted", event.src_path, event.is_directory)

            def on_moved(self, event):
                self._log(
                    "moved", event.src_path, event.is_directory,
                    extra={"dest_path": event.dest_path},
                )

        observer = Observer()
        handler = _Handler()
        for root in self._watch_roots:
            root.mkdir(parents=True, exist_ok=True)
            observer.schedule(handler, str(root), recursive=True)
        observer.start()
        self._observer = observer
        self._activated = True

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

        # Live mode: drain whatever the background Observer thread has
        # queued so far. This is a finite drain per call (matching
        # core.py's note that capture() "may be a live/blocking generator
        # ... or a finite one"); a long-running consumer is expected to
        # call capture() repeatedly (registry.merged_capture() does this
        # implicitly via round-robin re-entry into fresh iterators is NOT
        # how this is written -- here one capture() call drains the
        # snapshot taken at call time, mirroring how the real
        # fs_watcher.py just appends to a growing file that a separate
        # tailer would re-read).
        with self._lock:
            pending = list(self._events)
            self._events.clear()
        for payload in pending:
            yield RawEvent(
                timestamp=RawEvent.now_iso(),
                collector_id=self.collector_id,
                agent=AGENT_NAME,
                raw_payload=payload,
            )

    def deactivate(self) -> None:
        if self._observer is not None:
            self._observer.stop()
            self._observer.join(timeout=2.0)
            self._observer = None
        self._activated = False


if __name__ == "__main__":
    collector = AntigravityFSWatcherCollector()
    print(f"detect() -> {collector.detect()} (checked: {[str(p) for p in WATCH_ROOTS]})")

    real_dir = Path(__file__).resolve().parents[3].parent / "Antigravity_FSWatcher"
    replay_file = real_dir / "captured_fs_events.jsonl"

    print(f"\nSIMULATED FROM CAPTURED DATA ({replay_file}):")
    if replay_file.is_file():
        replay_collector = AntigravityFSWatcherCollector(replay_path=replay_file)
        replay_collector.activate()
        events = list(replay_collector.capture())
        print(f"  {len(events)} RawEvent(s) total (RESULTS.md reports 78 real raw events)")
        for ev in events[:5]:
            print(f"  {ev}")
        # Show the delegation-detection moment specifically, since that's
        # the headline structural finding (RESULTS.md section 4): a second
        # top-level conversation directory appearing mid-session.
        created_dirs = [
            ev for ev in events
            if ev.raw_payload.get("event_type") == "created" and ev.raw_payload.get("is_directory")
            and ev.raw_payload.get("path", "").rstrip("/").count("/") == str(WATCH_ROOTS[1]).count("/") + 1
        ]
        print(f"\n  top-level conversation dirs created this session: {len(created_dirs)}")
        for ev in created_dirs:
            print(f"    {ev.raw_payload['path']}")
        replay_collector.deactivate()
    else:
        print(f"  no real captured file found at {replay_file}, skipping replay demo")

    try:
        import watchdog  # noqa: F401
        print("\n(watchdog package IS installed -- live mode would also be available)")
    except ImportError:
        print("\n(watchdog package not installed -- live mode unavailable in this environment, "
              "replay mode above used real captured data instead)")
