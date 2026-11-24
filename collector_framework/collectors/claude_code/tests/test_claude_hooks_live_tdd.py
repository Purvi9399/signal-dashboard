"""Red → green: Claude Code live hooks listener + settings merge (LIVE_HOOKS.md)."""

from __future__ import annotations

import json
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from unittest import TestCase

from collectors.claude_code.claude_code_hooks import ClaudeCodeHooksCollector
from collectors.claude_code.hook_settings import merge_claude_http_hooks


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


class MergeClaudeHttpHooksTests(TestCase):
    def test_appends_http_hook_without_dropping_existing(self) -> None:
        url = "http://127.0.0.1:8000/webhook/claude/event"
        original = {
            "hooks": {
                "PreToolUse": [
                    {
                        "matcher": "Bash",
                        "hooks": [{"type": "command", "command": "true"}],
                    }
                ]
            }
        }
        merged = merge_claude_http_hooks(original, url, ("PreToolUse",))
        blocks = merged["hooks"]["PreToolUse"]
        self.assertEqual(len(blocks), 2)
        self.assertEqual(blocks[0]["hooks"][0]["type"], "command")
        self.assertEqual(blocks[1]["hooks"][0]["url"], url)
        self.assertEqual(original["hooks"]["PreToolUse"][0]["hooks"][0]["type"], "command")

    def test_idempotent_when_url_already_present(self) -> None:
        url = "http://127.0.0.1:8000/webhook/claude/event"
        settings = merge_claude_http_hooks({}, url, ("Stop",))
        again = merge_claude_http_hooks(settings, url, ("Stop",))
        self.assertEqual(len(again["hooks"]["Stop"]), 1)


class ReplayRegressionTests(TestCase):
    def test_replay_list_still_yields(self) -> None:
        payload = {"hook_event_name": "UserPromptSubmit", "session_id": "s1"}
        collector = ClaudeCodeHooksCollector(replay_events=[payload])
        collector.activate()
        events = list(collector.capture())
        collector.deactivate()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].raw_payload, payload)
        self.assertEqual(events[0].agent, "claude_code")
        self.assertEqual(events[0].collector_id, "claude_code.hooks")

    def test_replay_does_not_write_settings(self) -> None:
        path = Path(self.id().replace(".", "_") + "_settings.json")
        self.addCleanup(lambda: path.exists() and path.unlink())
        collector = ClaudeCodeHooksCollector(
            replay_events=[],
            settings_path=path,
            listen_port=0,
        )
        collector.activate()
        list(collector.capture())
        collector.deactivate()
        self.assertFalse(path.exists())


class LiveHooksTdd(TestCase):
    def test_http_post_becomes_raw_event(self) -> None:
        tmp = Path(tempfile.mkdtemp())
        settings = tmp / "settings.json"
        settings.write_text("{}", encoding="utf-8")
        collector = ClaudeCodeHooksCollector(
            replay_events=None,
            settings_path=settings,
            listen_host="127.0.0.1",
            listen_port=0,
            install_hooks=True,
        )
        collector.activate()
        port = collector.listen_port
        self.assertGreater(port, 0)
        payload = {
            "hook_event_name": "PreToolUse",
            "session_id": "live-1",
            "tool_name": "Read",
        }
        got = []

        def _drain() -> None:
            for ev in collector.capture():
                got.append(ev)

        thread = threading.Thread(target=_drain, daemon=True)
        thread.start()
        time.sleep(0.05)
        _post(f"http://127.0.0.1:{port}/webhook/claude/event", payload)
        time.sleep(0.05)
        collector.deactivate()
        thread.join(timeout=5)
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0].raw_payload["session_id"], "live-1")
        self.assertEqual(got[0].collector_id, "claude_code.hooks")

    def test_deactivate_restores_settings_backup(self) -> None:
        tmp = Path(tempfile.mkdtemp())
        settings = tmp / "settings.json"
        settings.write_text('{"model": "keep-me"}\n', encoding="utf-8")
        collector = ClaudeCodeHooksCollector(
            replay_events=None,
            settings_path=settings,
            listen_port=0,
            install_hooks=True,
        )
        collector.activate()
        installed = json.loads(settings.read_text(encoding="utf-8"))
        self.assertIn("hooks", installed)
        self.assertIn("PreToolUse", installed["hooks"])
        collector.deactivate()
        restored = json.loads(settings.read_text(encoding="utf-8"))
        self.assertEqual(restored["model"], "keep-me")
        self.assertNotIn("hooks", restored)
