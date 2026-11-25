"""Red → green: Antigravity live hooks + dedup (FR-H16)."""

from __future__ import annotations

import json
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from unittest import TestCase

from collectors.antigravity.antigravity_hooks import AntigravityHooksCollector
from collectors.antigravity.hook_settings import SIGNAL_BLOCK, merge_antigravity_command_hooks


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


class MergeAntigravityHooksTests(TestCase):
    def test_named_block_grouped_and_flat(self) -> None:
        merged = merge_antigravity_command_hooks({}, "python3 fwd.py http://x", ("PreToolUse", "Stop"))
        block = merged[SIGNAL_BLOCK]
        self.assertTrue(block["enabled"])
        self.assertEqual(block["PreToolUse"][0]["matcher"], "*")
        self.assertEqual(block["Stop"][0]["type"], "command")


class AntigravityLiveHooksTdd(TestCase):
    def test_replay_dedup(self) -> None:
        payload = {"event_type": "Stop", "conversationId": "a"}
        c = AntigravityHooksCollector(replay_events=[payload, payload], dedup=True)
        c.activate()
        events = list(c.capture())
        c.deactivate()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].agent, "antigravity")

    def test_live_post_dedup_and_restore(self) -> None:
        hooks = Path(tempfile.mkdtemp()) / "hooks.json"
        hooks.write_text("{}\n", encoding="utf-8")
        c = AntigravityHooksCollector(
            replay_events=None,
            hooks_path=hooks,
            listen_port=0,
            install_hooks=True,
            dedup=True,
        )
        c.activate()
        installed = json.loads(hooks.read_text(encoding="utf-8"))
        self.assertIn(SIGNAL_BLOCK, installed)
        got: list = []

        def _drain() -> None:
            for ev in c.capture():
                got.append(ev)

        thread = threading.Thread(target=_drain, daemon=True)
        thread.start()
        time.sleep(0.05)
        payload = {"event_type": "Stop", "conversationId": "live"}
        url = f"http://127.0.0.1:{c.listen_port}/webhook/antigravity/event"
        _post(url, payload)
        _post(url, payload)
        time.sleep(0.08)
        c.deactivate()
        thread.join(timeout=5)
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0].raw_payload["conversationId"], "live")
        self.assertEqual(json.loads(hooks.read_text(encoding="utf-8")), {})
