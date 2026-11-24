"""Red → green: live local_hooks HTTP ingest (LIVE_LOCAL_HOOKS.md)."""

from __future__ import annotations

import json
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from unittest import TestCase

from collectors.antigravity.antigravity_local_hooks import AntigravityLocalHooksCollector
from collectors.codex.codex_local_hooks import CodexLocalHooksCollector
from collectors.copilot.copilot_local_hooks import CopilotLocalHooksCollector


def _post(url: str, payload: dict) -> None:
    data = json.dumps(payload).encode("utf-8")
    last_exc: Exception = RuntimeError("post failed")
    for _ in range(20):
        req = urllib.request.Request(
            url, data=data, headers={"Content-Type": "application/json"}, method="POST"
        )
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                resp.read()
            return
        except (urllib.error.URLError, ConnectionError, OSError) as exc:
            last_exc = exc
            time.sleep(0.05)
    raise last_exc


def _drain(collector, bucket: list) -> None:
    for ev in collector.capture():
        bucket.append(ev)


class ReplayLocalHooksTests(TestCase):
    def test_copilot_replay(self) -> None:
        payload = {"type": "onDidChangeTextDocument", "file": "a.py"}
        c = CopilotLocalHooksCollector(replay_events=[payload])
        c.activate()
        events = list(c.capture())
        c.deactivate()
        self.assertEqual(events[0].agent, "copilot")
        self.assertEqual(events[0].raw_payload, payload)

    def test_antigravity_replay_path(self) -> None:
        path = Path(tempfile.mkdtemp()) / "ide.jsonl"
        rec = {"type": "undo_detected_unmatched"}
        path.write_text(json.dumps(rec) + "\n", encoding="utf-8")
        c = AntigravityLocalHooksCollector(replay_path=path)
        c.activate()
        events = list(c.capture())
        c.deactivate()
        self.assertEqual(events[0].agent, "antigravity")
        self.assertEqual(events[0].raw_payload["type"], "undo_detected_unmatched")


class LiveLocalHooksTdd(TestCase):
    def test_copilot_post(self) -> None:
        c = CopilotLocalHooksCollector(replay_events=None, listen_port=0)
        c.activate()
        got: list = []
        thread = threading.Thread(target=_drain, args=(c, got), daemon=True)
        thread.start()
        time.sleep(0.05)
        payload = {"type": "probable_accept", "file": "x.ts"}
        _post(f"http://127.0.0.1:{c.listen_port}/webhook/copilot/local", payload)
        time.sleep(0.05)
        c.deactivate()
        thread.join(timeout=5)
        self.assertEqual(got[0].agent, "copilot")
        self.assertEqual(got[0].raw_payload, payload)

    def test_codex_post(self) -> None:
        c = CodexLocalHooksCollector(replay_events=None, listen_port=0)
        c.activate()
        got: list = []
        thread = threading.Thread(target=_drain, args=(c, got), daemon=True)
        thread.start()
        time.sleep(0.05)
        payload = {"type": "onDidOpenTerminal", "name": ""}
        _post(f"http://127.0.0.1:{c.listen_port}/webhook/codex/local", payload)
        time.sleep(0.05)
        c.deactivate()
        thread.join(timeout=5)
        self.assertEqual(got[0].agent, "codex")
        self.assertEqual(got[0].raw_payload["name"], "")

    def test_antigravity_post(self) -> None:
        c = AntigravityLocalHooksCollector(replay_path=None, listen_port=0)
        c.activate()
        got: list = []
        thread = threading.Thread(target=_drain, args=(c, got), daemon=True)
        thread.start()
        time.sleep(0.05)
        payload = {"type": "environment_snapshot", "detected_ai_extensions": {}}
        _post(f"http://127.0.0.1:{c.listen_port}/webhook/antigravity/local", payload)
        time.sleep(0.05)
        c.deactivate()
        thread.join(timeout=5)
        self.assertEqual(got[0].agent, "antigravity")
        self.assertEqual(got[0].raw_payload, payload)
