"""
GitHub Copilot Filesystem Watcher collector.

STATUS: UNVERIFIED / BEST-EFFORT.

No dedicated Copilot FS Watcher folder exists in this project (checked for
typos/variants: `Copilot_FSWatcher`, `Copilot_FS_Watcher`, `CopilotFSWatcher`,
`Copilot_FileWatcher`, `Copilot_Filesystem*` -- none exist; only
`Antigravity_FSWatcher` and `Codex_FSWatcher` exist as siblings, for
different agents). This collector was therefore never empirically tested
against a real Copilot local-state directory the way copilot_hooks.py,
copilot_mcp_proxy.py, and copilot_cli_wrapper.py were against their real
capture folders. Every path below is inference from real evidence found
*incidentally* in other Copilot test folders while investigating unrelated
surfaces -- not from a dedicated fs-watching test. Treat this file as a
starting point that needs a real live-capture pass before being trusted,
exactly as the task instructions require flagging.

WHAT REAL EVIDENCE EXISTS, AND WHAT IT DOES/DOESN'T SUPPORT:

  Copilot/RESULTS.md (section 5, finding 5) -- the CLI's own bundled app.js
      documents (and this was directly read, not assumed) that repo hooks
      also load from an inline `hooks` key in `settings.json` /
      `settings.local.json` / a global `config.json`, and that "personal
      (non-repo) hooks load from `~/.copilot/hooks`". This is real evidence
      of a real local directory Copilot reads from -- but it is evidence of
      a *config load path*, not proof that Copilot itself *writes* ongoing
      state there that would be meaningful to watch for changes. Watching
      it would mostly catch a human/tool editing hook config, not Copilot's
      own runtime activity.

  Copilot_MCP_Proxy/RESULTS.md (section f) -- confirms Copilot CLI writes
      real, live log files to `~/.copilot/logs/process-*.log`, and that this
      is, in fact, currently the ONLY place a real signal (MCP connection
      failures) is observable at all -- explicitly NOT observable via any
      wire-level proxy. This is the strongest real evidence in the project
      that `~/.copilot/logs/` is worth watching: it is independently
      confirmed to contain information no other collector in this project
      surfaces.

  Copilot_LocalHooks/RESULTS.md -- confirms VS Code extension state lives
      under a `globalStorageUri`/`globalStoragePath` per-extension directory
      (standard VS Code extension API, referenced by the pylance skill file
      found during path-searching, not something copilot-chat specific was
      directly observed writing to in this project's captures) -- NOT used
      as a load-bearing detect()/watch path here since no real Copilot-
      chat-specific write to such a directory was actually observed; noted
      only as a documented gap for a future real test.

WHAT THIS COLLECTOR DOES NOT CLAIM: there is no real evidence in this
project of Copilot CLI writing session transcripts, brain/artifact
directories, or any other structured on-disk state analogous to what
Antigravity_FSWatcher/fs_watcher.py watches for Antigravity (~/.gemini/
antigravity/brain, ~/.gemini/antigravity-cli/brain). This collector does
NOT invent an equivalent Copilot brain-directory path -- the two watch
roots below (~/.copilot/logs, ~/.copilot/hooks) are the only ones with any
real, cited evidence at all, and even those are best-effort/inferred rather
than confirmed via a dedicated fs-watcher test.

MECHANISM: adapted from Antigravity_FSWatcher/fs_watcher.py (the generic
watchdog-based recursive fs-watching approach: on_created/on_modified/
on_deleted/on_moved -> one JSON record per event) -- reused purely as a
MECHANISM reference, per the task instructions; the watch roots themselves
are Copilot-specific and grounded in the citations above, not copied from
Antigravity's own paths.

No normalization: raw_payload is the same {event_type, path, is_directory,
size_bytes[, dest_path]} shape Antigravity_FSWatcher/fs_watcher.py's
Handler._log() already produces -- untouched.
"""

from __future__ import annotations

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

AGENT_NAME = "copilot"
COLLECTOR_ID = "copilot.fs_watcher"

# BEST-EFFORT / UNVERIFIED watch roots -- see module docstring for exactly
# which citation supports each one. Both are real, confirmed-to-exist local
# Copilot directories (per Copilot/RESULTS.md section 5 and
# Copilot_MCP_Proxy/RESULTS.md section f respectively) -- but neither was
# ever exercised under a real fs-watcher test in this project.
WATCH_ROOTS = [
    os.path.expanduser("~/.copilot/logs"),   # Copilot_MCP_Proxy/RESULTS.md section f
    os.path.expanduser("~/.copilot/hooks"),  # Copilot/RESULTS.md section 5, finding 5
]


class CopilotFSWatcherCollector(Collector):
    """UNVERIFIED / BEST-EFFORT filesystem-watcher collector for Copilot's
    local state directories. See module docstring for exactly which paths
    are grounded in which real evidence, and why this is explicitly weaker
    evidence than the other four Copilot collectors in this framework.

    Live mode (real deployment, requires the third-party `watchdog`
    package, NOT bundled with this stdlib-only framework): activate()
    starts a watchdog Observer on WATCH_ROOTS; capture() drains events the
    handler collects.

    This build supports feeding capture() from already-captured JSONL
    records shaped like Antigravity_FSWatcher/captured_fs_events.jsonl
    (the only real fs-watcher capture file that exists anywhere in this
    project -- for a DIFFERENT agent, used here only to exercise this
    collector's plumbing end-to-end, clearly labeled as such, never
    presented as real Copilot data).
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

    @property
    def collector_id(self) -> str:
        return COLLECTOR_ID

    def detect(self) -> bool:
        """Real, cheap, side-effect-free check: does at least one of the
        best-effort watch roots exist on this machine? (Directory
        existence check only -- no reads of file contents, no network.)
        A machine with the `copilot` CLI installed but never yet run will
        legitimately fail this (neither directory would exist yet), which
        is an honest reflection of how thin this collector's grounding is.
        """
        return any(Path(root).is_dir() for root in self._watch_roots)

    def activate(self) -> None:
        self._activated = True
        if self._replay_events is not None:
            return
        self._start_live()

    def _start_live(self) -> None:
        feed = LiveEventFeed()
        handler = QueuedFSHandler(feed)
        watch = LiveFSWatch(feed, handler)
        watch.start(self._watch_roots)
        self._live_feed = feed
        self._watch = watch

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
    import json

    print("=== CopilotFSWatcherCollector standalone test ===")
    print(
        "STATUS: UNVERIFIED / BEST-EFFORT -- no dedicated Copilot FS Watcher "
        "evidence folder exists in this project; watch roots are inferred "
        "from incidental evidence in Copilot/RESULTS.md and "
        "Copilot_MCP_Proxy/RESULTS.md. See module docstring for exact "
        "citations."
    )

    collector = CopilotFSWatcherCollector()
    detected = collector.detect()
    print(f"\ndetect() -> {detected} (checks for existence of: {WATCH_ROOTS})")
    print(f"watchdog package available for live mode: {WATCHDOG_AVAILABLE}")

    # There is no real Copilot fs-watcher capture file anywhere in this
    # project (that is precisely the gap this file documents). To still
    # exercise capture()'s plumbing end-to-end with real data (not invented
    # data), we replay the one real fs-watcher capture that DOES exist in
    # this project -- Antigravity's -- while being explicit that it is a
    # different agent's real data, used only to prove the collector's own
    # code path works, never presented as real Copilot output.
    antigravity_evidence_path = (
        Path(__file__).resolve().parents[4]
        / "Antigravity_FSWatcher" / "captured_fs_events.jsonl"
    )

    if antigravity_evidence_path.is_file():
        print(
            f"\nSIMULATED FROM CAPTURED DATA (NOTE: this is REAL captured "
            f"data, but from Antigravity_FSWatcher/captured_fs_events.jsonl "
            f"-- a DIFFERENT agent's real fs-watcher capture, reused here "
            f"ONLY to exercise this collector's own plumbing, since no real "
            f"Copilot fs-watcher capture exists anywhere in this project):"
        )
        records = []
        with open(antigravity_evidence_path) as f:
            for line in f:
                line = line.strip()
                if line:
                    records.append(json.loads(line))
        records = records[:5]

        replay_collector = CopilotFSWatcherCollector(replay_events=records)
        replay_collector.activate()
        for i, event in enumerate(replay_collector.capture()):
            print(
                f"  [{i}] collector_id={event.collector_id!r} agent={event.agent!r} "
                f"event_type={event.raw_payload.get('event_type')!r} "
                f"path={event.raw_payload.get('path')!r}"
            )
        replay_collector.deactivate()
    else:
        print(
            f"\nNo evidence file found at {antigravity_evidence_path} either "
            f"-- skipping replay demonstration entirely."
        )

    print(
        "\nCONCLUSION: this collector is functional plumbing but its "
        "Copilot-specific grounding is weak. A real live-capture pass "
        "(actually running `copilot` and watching ~/.copilot/logs and "
        "~/.copilot/hooks for real writes) is needed before trusting the "
        "watch roots above -- flagged per the task's explicit instruction "
        "to mark this UNVERIFIED / BEST-EFFORT rather than presenting it "
        "as equally solid to the other four Copilot collectors."
    )
