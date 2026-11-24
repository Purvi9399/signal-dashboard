"""Red → green: Copilot live HTTP hooks + required env (LIVE_HOOKS.md FR-H14)."""

from __future__ import annotations

import json
import os
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from unittest import TestCase

from collectors.antigravity.antigravity_hooks import AntigravityHooksCollector
from collectors.codex.codex_hooks import CodexHooksCollector
from collectors.copilot.copilot_hooks import REQUIRED_ENV_VARS, CopilotHooksCollector
from collectors.copilot.hook_settings import merge_copilot_http_hooks


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


class MergeCopilotHttpHooksTests(TestCase):
    def test_both_casings_and_keeps_existing(self) -> None:
        original = {"hooks": {"PreToolUse": [{"type": "command", "command": "true"}]}}
        merged = merge_copilot_http_hooks(
            original, "http://127.0.0.1:8000/webhook/copilot/event", ("PreToolUse", "preToolUse")
        )
        self.assertEqual(merged["hooks"]["PreToolUse"][0]["command"], "true")
        self.assertEqual(len(merged["hooks"]["PreToolUse"]), 2)
        self.assertEqual(merged["hooks"]["preToolUse"][0]["type"], "http")


class CopilotLiveHooksTdd(TestCase):
    def test_replay_yields(self) -> None:
        payload = {"hook_event_name": "PreToolUse", "session_id": "p1"}
        c = CopilotHooksCollector(replay_events=[payload])
        c.activate()
        events = list(c.capture())
        c.deactivate()
        self.assertEqual(events[0].agent, "copilot")
        self.assertEqual(events[0].raw_payload, payload)

    def test_live_post_and_env_restore(self) -> None:
        prev = {k: os.environ.get(k) for k in REQUIRED_ENV_VARS}
        hooks = Path(tempfile.mkdtemp()) / "hooks.json"
        hooks.write_text("{}", encoding="utf-8")
        c = CopilotHooksCollector(
            replay_events=None, hooks_path=hooks, listen_port=0, install_hooks=True
        )
        c.activate()
        for key, value in REQUIRED_ENV_VARS.items():
            self.assertEqual(os.environ.get(key), value)
        got: list = []

        def _drain() -> None:
            for ev in c.capture():
                got.append(ev)

        thread = threading.Thread(target=_drain, daemon=True)
        thread.start()
        time.sleep(0.05)
        _post(
            f"http://127.0.0.1:{c.listen_port}/webhook/copilot/event",
            {"hook_event_name": "SessionStart"},
        )
        time.sleep(0.05)
        installed = json.loads(hooks.read_text(encoding="utf-8"))
        self.assertIn("PreToolUse", installed["hooks"])
        c.deactivate()
        thread.join(timeout=5)
        self.assertEqual(got[0].raw_payload["hook_event_name"], "SessionStart")
        restored = json.loads(hooks.read_text(encoding="utf-8"))
        self.assertEqual(restored, {})
        for key, old in prev.items():
            self.assertEqual(os.environ.get(key), old)

    def test_replay_does_not_write_or_set_env(self) -> None:
        prev = {k: os.environ.get(k) for k in REQUIRED_ENV_VARS}
        path = Path(tempfile.mkdtemp()) / "hooks.json"
        c = CopilotHooksCollector(replay_events=[], hooks_path=path, listen_port=0)
        c.activate()
        list(c.capture())
        c.deactivate()
        self.assertFalse(path.exists())
        for key, old in prev.items():
            self.assertEqual(os.environ.get(key), old)


class SharedListenerRemainingAgentsTdd(TestCase):
    def test_copilot_codex_antigravity_share_port(self) -> None:
        tmp = Path(tempfile.mkdtemp())
        copilot = CopilotHooksCollector(
            replay_events=None,
            hooks_path=tmp / "copilot.json",
            listen_port=0,
            install_hooks=True,
        )
        copilot.activate()
        port = copilot.listen_port
        codex = CodexHooksCollector(
            replay_events=None,
            require_trust=False,
            hooks_path=tmp / "codex.json",
            listen_port=port,
            install_hooks=True,
        )
        agy = AntigravityHooksCollector(
            replay_events=None,
            hooks_path=tmp / "agy.json",
            listen_port=port,
            install_hooks=True,
        )
        codex.activate()
        agy.activate()
        got_p: list = []
        got_x: list = []
        got_a: list = []

        def _drain(collector, bucket: list) -> None:
            for ev in collector.capture():
                bucket.append(ev)

        threads = [
            threading.Thread(target=_drain, args=(copilot, got_p), daemon=True),
            threading.Thread(target=_drain, args=(codex, got_x), daemon=True),
            threading.Thread(target=_drain, args=(agy, got_a), daemon=True),
        ]
        for t in threads:
            t.start()
        time.sleep(0.05)
        _post(f"http://127.0.0.1:{port}/webhook/copilot/event", {"hook_event_name": "Stop"})
        _post(f"http://127.0.0.1:{port}/webhook/codex/event", {"hook_event_name": "Stop"})
        _post(f"http://127.0.0.1:{port}/webhook/antigravity/event", {"event_type": "Stop"})
        time.sleep(0.08)
        agy.deactivate()
        codex.deactivate()
        copilot.deactivate()
        for t in threads:
            t.join(timeout=5)
        self.assertEqual(got_p[0].agent, "copilot")
        self.assertEqual(got_x[0].agent, "codex")
        self.assertEqual(got_a[0].agent, "antigravity")
