"""Red → green: live FS Watcher (LIVE_FS.md)."""

from __future__ import annotations

import json
import tempfile
import threading
import time
from pathlib import Path
from unittest import TestCase

from collectors.antigravity.antigravity_fs_watcher import AntigravityFSWatcherCollector
from collectors.claude_code.claude_code_fs_watcher import ClaudeCodeFSWatcherCollector
from collectors.codex.codex_fs_watcher import CodexFSWatcherCollector
from collectors.copilot.copilot_fs_watcher import CopilotFSWatcherCollector
from collectors.cursor.cursor_fs_watcher import CursorFSWatcherCollector
from collectors.live_fs import fs_record, require_watchdog


def _drain(collector, bucket: list) -> None:
    for ev in collector.capture():
        bucket.append(ev)


def _wait_path(bucket: list, needle: str, timeout: float = 2.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if any(needle in str((ev.raw_payload or {}).get("path", "")) or needle in json.dumps(ev.raw_payload) for ev in bucket):
            return
        time.sleep(0.05)
    raise AssertionError(f"no event containing {needle!r} in { [e.raw_payload for e in bucket] }")


class LiveFsHelperTests(TestCase):
    def test_fs_record_shape(self) -> None:
        rec = fs_record("created", "/tmp/x", False)
        self.assertEqual(rec["event_type"], "created")
        self.assertEqual(rec["path"], "/tmp/x")
        self.assertIn("size_bytes", rec)

    def test_require_watchdog_when_present(self) -> None:
        require_watchdog()


class ReplayFsWatcherTests(TestCase):
    def test_claude_replay(self) -> None:
        payload = {"type": "assistant", "message": {"usage": {"input_tokens": 1}}}
        c = ClaudeCodeFSWatcherCollector(replay_events=[payload])
        c.activate()
        events = list(c.capture())
        c.deactivate()
        self.assertEqual(events[0].agent, "claude_code")
        self.assertEqual(events[0].raw_payload, payload)

    def test_antigravity_replay_path(self) -> None:
        path = Path(tempfile.mkdtemp()) / "events.jsonl"
        rec = {"event_type": "created", "path": "/brain/a", "is_directory": True}
        path.write_text(json.dumps(rec) + "\n", encoding="utf-8")
        c = AntigravityFSWatcherCollector(replay_path=path)
        c.activate()
        events = list(c.capture())
        c.deactivate()
        self.assertEqual(events[0].agent, "antigravity")
        self.assertEqual(events[0].raw_payload["path"], "/brain/a")


class LiveFsWatcherTdd(TestCase):
    def test_copilot_write_becomes_raw_event(self) -> None:
        root = Path(tempfile.mkdtemp())
        c = CopilotFSWatcherCollector(replay_events=None, watch_roots=[str(root)])
        c.activate()
        got: list = []
        thread = threading.Thread(target=_drain, args=(c, got), daemon=True)
        thread.start()
        time.sleep(0.15)
        target = root / "process-live.log"
        target.write_text("hello-fs\n", encoding="utf-8")
        _wait_path(got, str(target))
        c.deactivate()
        thread.join(timeout=5)
        self.assertEqual(got[0].agent, "copilot")
        self.assertIn(str(target), got[0].raw_payload["path"])

    def test_codex_includes_watch_root(self) -> None:
        root = Path(tempfile.mkdtemp())
        c = CodexFSWatcherCollector(replay_events=None, watch_paths=[str(root)])
        c.activate()
        got: list = []
        thread = threading.Thread(target=_drain, args=(c, got), daemon=True)
        thread.start()
        time.sleep(0.15)
        target = root / "rollout-live.jsonl"
        target.write_text("{}\n", encoding="utf-8")
        _wait_path(got, str(target))
        c.deactivate()
        thread.join(timeout=5)
        self.assertEqual(got[0].agent, "codex")
        self.assertIn("watch_root", got[0].raw_payload)

    def test_cursor_live_needs_project_root(self) -> None:
        c = CursorFSWatcherCollector(
            replay_events=None, project_root=None, use_detected_root=False
        )
        with self.assertRaises(RuntimeError):
            c.activate()

    def test_cursor_write_under_project_root(self) -> None:
        root = Path(tempfile.mkdtemp())
        c = CursorFSWatcherCollector(replay_events=None, project_root=root)
        c.activate()
        got: list = []
        thread = threading.Thread(target=_drain, args=(c, got), daemon=True)
        thread.start()
        time.sleep(0.15)
        target = root / "notes.md"
        target.write_text("x\n", encoding="utf-8")
        _wait_path(got, str(target))
        c.deactivate()
        thread.join(timeout=5)
        self.assertEqual(got[0].agent, "cursor")

    def test_claude_jsonl_tail(self) -> None:
        root = Path(tempfile.mkdtemp())
        session = root / "proj"
        session.mkdir()
        transcript = session / "sess.jsonl"
        transcript.write_text("", encoding="utf-8")
        c = ClaudeCodeFSWatcherCollector(replay_events=None, watch_roots=[str(root)])
        c.activate()
        got: list = []
        thread = threading.Thread(target=_drain, args=(c, got), daemon=True)
        thread.start()
        time.sleep(0.15)
        line = {"type": "assistant", "session_id": "live-fs"}
        with transcript.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(line) + "\n")
        deadline = time.time() + 2.0
        while time.time() < deadline:
            if any(ev.raw_payload.get("session_id") == "live-fs" for ev in got):
                break
            time.sleep(0.05)
        c.deactivate()
        thread.join(timeout=5)
        matched = [ev for ev in got if ev.raw_payload.get("session_id") == "live-fs"]
        self.assertEqual(len(matched), 1)
        self.assertEqual(matched[0].agent, "claude_code")

    def test_antigravity_mkdir_and_write(self) -> None:
        root = Path(tempfile.mkdtemp()) / "brain"
        c = AntigravityFSWatcherCollector(watch_roots=[root], replay_path=None)
        c.activate()
        self.assertTrue(root.is_dir())
        got: list = []
        thread = threading.Thread(target=_drain, args=(c, got), daemon=True)
        thread.start()
        time.sleep(0.15)
        target = root / "conv.txt"
        target.write_text("n\n", encoding="utf-8")
        _wait_path(got, str(target))
        c.deactivate()
        thread.join(timeout=5)
        self.assertEqual(got[0].agent, "antigravity")
