"""Red → green: Cursor live hooks on the shared listener (LIVE_HOOKS.md FR-H13)."""

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
from collectors.cursor.cursor_hooks import CursorHooksCollector
from collectors.cursor.hook_settings import merge_cursor_command_hooks


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


class MergeCursorCommandHooksTests(TestCase):
    def test_keeps_existing_command(self) -> None:
        original = {"hooks": {"preToolUse": [{"command": "echo keep"}]}}
        merged = merge_cursor_command_hooks(original, "python3 fwd.py http://x", ("preToolUse",))
        cmds = [b["command"] for b in merged["hooks"]["preToolUse"]]
        self.assertEqual(cmds[0], "echo keep")
        self.assertEqual(len(cmds), 2)
        self.assertEqual(merged["version"], 1)
        self.assertEqual(original["hooks"]["preToolUse"][0]["command"], "echo keep")

    def test_idempotent(self) -> None:
        cmd = "python3 fwd.py http://x"
        once = merge_cursor_command_hooks({}, cmd, ("stop",))
        again = merge_cursor_command_hooks(once, cmd, ("stop",))
        self.assertEqual(len(again["hooks"]["stop"]), 1)


class ReplayRegressionTests(TestCase):
    def test_replay_list_still_yields(self) -> None:
        payload = {"event_type": "preToolUse", "session_id": "c1"}
        collector = CursorHooksCollector(replay_events=[payload])
        collector.activate()
        events = list(collector.capture())
        collector.deactivate()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].raw_payload, payload)
        self.assertEqual(events[0].agent, "cursor")
        self.assertEqual(events[0].collector_id, "cursor.hooks")

    def test_replay_does_not_write_hooks_json(self) -> None:
        path = Path(tempfile.mkdtemp()) / "hooks.json"
        collector = CursorHooksCollector(
            replay_events=[], hooks_path=path, listen_port=0
        )
        collector.activate()
        list(collector.capture())
        collector.deactivate()
        self.assertFalse(path.exists())


class LiveCursorHooksTdd(TestCase):
    def test_http_post_becomes_raw_event(self) -> None:
        hooks = Path(tempfile.mkdtemp()) / "hooks.json"
        hooks.write_text("{}", encoding="utf-8")
        collector = CursorHooksCollector(
            replay_events=None,
            hooks_path=hooks,
            listen_host="127.0.0.1",
            listen_port=0,
            install_hooks=True,
        )
        collector.activate()
        port = collector.listen_port
        self.assertGreater(port, 0)
        payload = {"event_type": "beforeSubmitPrompt", "session_id": "live-c"}
        got: list = []
        thread = threading.Thread(target=_drain, args=(collector, got), daemon=True)
        thread.start()
        time.sleep(0.05)
        _post(f"http://127.0.0.1:{port}/webhook/cursor/event", payload)
        time.sleep(0.05)
        collector.deactivate()
        thread.join(timeout=5)
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0].raw_payload["session_id"], "live-c")
        self.assertEqual(got[0].agent, "cursor")

    def test_deactivate_restores_hooks_backup(self) -> None:
        hooks = Path(tempfile.mkdtemp()) / "hooks.json"
        hooks.write_text('{"version": 1, "hooks": {}}\n', encoding="utf-8")
        collector = CursorHooksCollector(
            replay_events=None,
            hooks_path=hooks,
            listen_port=0,
            install_hooks=True,
        )
        collector.activate()
        installed = json.loads(hooks.read_text(encoding="utf-8"))
        self.assertIn("preToolUse", installed["hooks"])
        self.assertTrue(any("forward_hook.py" in (h.get("command") or "") for h in installed["hooks"]["preToolUse"]))
        collector.deactivate()
        restored = json.loads(hooks.read_text(encoding="utf-8"))
        self.assertEqual(restored["hooks"], {})


class SharedListenerTdd(TestCase):
    def test_claude_and_cursor_share_port_by_path(self) -> None:
        tmp = Path(tempfile.mkdtemp())
        claude = ClaudeCodeHooksCollector(
            replay_events=None,
            settings_path=tmp / "settings.json",
            listen_port=0,
            install_hooks=True,
        )
        claude.activate()
        port = claude.listen_port
        cursor = CursorHooksCollector(
            replay_events=None,
            hooks_path=tmp / "hooks.json",
            listen_port=port,
            install_hooks=True,
        )
        cursor.activate()
        self.assertEqual(cursor.listen_port, port)
        got_c: list = []
        got_u: list = []
        t1 = threading.Thread(target=_drain, args=(claude, got_c), daemon=True)
        t2 = threading.Thread(target=_drain, args=(cursor, got_u), daemon=True)
        t1.start()
        t2.start()
        time.sleep(0.05)
        _post(f"http://127.0.0.1:{port}/webhook/claude/event", {"hook_event_name": "Stop"})
        _post(f"http://127.0.0.1:{port}/webhook/cursor/event", {"event_type": "stop"})
        time.sleep(0.05)
        cursor.deactivate()
        claude.deactivate()
        t1.join(timeout=5)
        t2.join(timeout=5)
        self.assertEqual(got_c[0].agent, "claude_code")
        self.assertEqual(got_u[0].agent, "cursor")
        self.assertEqual(got_c[0].raw_payload["hook_event_name"], "Stop")
        self.assertEqual(got_u[0].raw_payload["event_type"], "stop")
