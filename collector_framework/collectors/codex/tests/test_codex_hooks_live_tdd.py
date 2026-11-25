"""Red → green: Codex live command hooks + fail-closed trust (FR-H15)."""

from __future__ import annotations

import json
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from unittest import TestCase

from collectors.codex.codex_hooks import CodexHooksCollector, CodexTrustNotGrantedError
from collectors.codex.hook_settings import merge_codex_command_hooks


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


class MergeCodexCommandHooksTests(TestCase):
    def test_does_not_clobber_existing_string(self) -> None:
        original = {"hooks": {"PreToolUse": "echo keep"}}
        merged = merge_codex_command_hooks(original, "python3 fwd.py http://x", ("PreToolUse", "Stop"))
        self.assertEqual(merged["hooks"]["PreToolUse"], "echo keep")
        self.assertIn("fwd.py", merged["hooks"]["Stop"])


class CodexLiveHooksTdd(TestCase):
    def test_replay_yields(self) -> None:
        payload = {"hook_event_name": "SessionStart", "session_id": "x"}
        c = CodexHooksCollector(replay_events=[payload], require_trust=False)
        c.activate()
        events = list(c.capture())
        c.deactivate()
        self.assertEqual(events[0].agent, "codex")
        self.assertEqual(events[0].raw_payload, payload)

    def test_untrusted_live_does_not_write(self) -> None:
        home = Path(tempfile.mkdtemp())
        hooks = home / "hooks.json"
        c = CodexHooksCollector(
            replay_events=None,
            require_trust=True,
            codex_home=home,
            hooks_path=hooks,
            listen_port=0,
        )
        with self.assertRaises(CodexTrustNotGrantedError):
            c.activate()
        self.assertFalse(hooks.exists())

    def test_live_post_with_trust_bypass(self) -> None:
        hooks = Path(tempfile.mkdtemp()) / "hooks.json"
        hooks.write_text("{}", encoding="utf-8")
        c = CodexHooksCollector(
            replay_events=None,
            require_trust=False,
            hooks_path=hooks,
            listen_port=0,
            install_hooks=True,
        )
        c.activate()
        got: list = []

        def _drain() -> None:
            for ev in c.capture():
                got.append(ev)

        thread = threading.Thread(target=_drain, daemon=True)
        thread.start()
        time.sleep(0.05)
        _post(
            f"http://127.0.0.1:{c.listen_port}/webhook/codex/event",
            {"hook_event_name": "PreToolUse"},
        )
        time.sleep(0.05)
        c.deactivate()
        thread.join(timeout=5)
        self.assertEqual(got[0].agent, "codex")
        self.assertEqual(json.loads(hooks.read_text(encoding="utf-8")), {})
