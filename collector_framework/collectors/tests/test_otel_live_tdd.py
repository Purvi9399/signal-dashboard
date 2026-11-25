"""Red → green: live OTel OTLP/HTTP (LIVE_OTEL.md)."""

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

from collectors.antigravity.antigravity_otel import AntigravityOTelCollector
from collectors.claude_code.claude_code_otel import ClaudeCodeOTelCollector
from collectors.codex.codex_otel import CodexOTelCollector
from collectors.copilot.copilot_otel import CopilotOTelCollector
from collectors.cursor.cursor_otel import CursorOTelCollector
from collectors.live_otel import merge_codex_otel


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


class MergeCodexOtelTests(TestCase):
    def test_appends_when_missing(self) -> None:
        merged = merge_codex_otel("", "http://127.0.0.1:9/otel/codex")
        self.assertIn("[otel]", merged)
        self.assertIn("/v1/logs", merged)

    def test_keeps_existing(self) -> None:
        original = "[otel]\nenvironment = \"keep\"\n"
        self.assertEqual(merge_codex_otel(original, "http://x"), original)


class ReplayOtelTests(TestCase):
    def test_claude_replay(self) -> None:
        payload = {"attributes": {"prompt.id": "p1"}}
        c = ClaudeCodeOTelCollector(replay_events=[payload])
        c.activate()
        events = list(c.capture())
        c.deactivate()
        self.assertEqual(events[0].agent, "claude_code")
        self.assertEqual(events[0].raw_payload, payload)

    def test_copilot_replay_batches(self) -> None:
        batch = {"resource": {}, "scopeSpans": []}
        c = CopilotOTelCollector(replay_otlp_export={"batches": [batch]})
        c.activate()
        events = list(c.capture())
        c.deactivate()
        self.assertEqual(events[0].raw_payload, batch)
        self.assertEqual(events[0].agent, "copilot")

    def test_antigravity_live_yields_nothing(self) -> None:
        c = AntigravityOTelCollector()
        c.activate()
        events = list(c.capture())
        c.deactivate()
        self.assertEqual(events, [])


class LiveOtelTdd(TestCase):
    def test_claude_post_and_env_restore(self) -> None:
        keys = (
            "CLAUDE_CODE_ENABLE_TELEMETRY",
            "OTEL_EXPORTER_OTLP_ENDPOINT",
            "OTEL_EXPORTER_OTLP_PROTOCOL",
        )
        prev = {k: os.environ.get(k) for k in keys}
        c = ClaudeCodeOTelCollector(replay_events=None, listen_port=0)
        c.activate()
        self.assertEqual(os.environ.get("CLAUDE_CODE_ENABLE_TELEMETRY"), "1")
        got: list = []
        thread = threading.Thread(target=_drain, args=(c, got), daemon=True)
        thread.start()
        time.sleep(0.05)
        body = {"resourceSpans": [{"scopeSpans": []}]}
        _post(f"http://127.0.0.1:{c.listen_port}/otel/claude/v1/traces", body)
        time.sleep(0.05)
        c.deactivate()
        thread.join(timeout=5)
        self.assertEqual(got[0].raw_payload["resourceSpans"], body["resourceSpans"])
        for key, old in prev.items():
            self.assertEqual(os.environ.get(key), old)

    def test_copilot_logs_path(self) -> None:
        c = CopilotOTelCollector(replay_otlp_export=None, listen_port=0)
        c.activate()
        got: list = []
        thread = threading.Thread(target=_drain, args=(c, got), daemon=True)
        thread.start()
        time.sleep(0.05)
        body = {"resourceLogs": []}
        _post(f"http://127.0.0.1:{c.listen_port}/otel/copilot/v1/logs", body)
        time.sleep(0.05)
        c.deactivate()
        thread.join(timeout=5)
        self.assertEqual(got[0].agent, "copilot")
        self.assertEqual(got[0].raw_payload, body)

    def test_codex_config_restore(self) -> None:
        home = Path(tempfile.mkdtemp())
        cfg = home / "config.toml"
        cfg.write_text("model = \"x\"\n", encoding="utf-8")
        c = CodexOTelCollector(
            replay_events=None,
            require_configured=False,
            listen_port=0,
            codex_home=home,
            install_config=True,
        )
        c.activate()
        text = cfg.read_text(encoding="utf-8")
        self.assertIn("[otel]", text)
        self.assertIn("model = \"x\"", text)
        got: list = []
        thread = threading.Thread(target=_drain, args=(c, got), daemon=True)
        thread.start()
        time.sleep(0.05)
        _post(
            f"http://127.0.0.1:{c.listen_port}/otel/codex/v1/metrics",
            {"resourceMetrics": []},
        )
        time.sleep(0.05)
        c.deactivate()
        thread.join(timeout=5)
        self.assertEqual(got[0].agent, "codex")
        self.assertEqual(cfg.read_text(encoding="utf-8"), "model = \"x\"\n")

    def test_cursor_binds_without_env(self) -> None:
        prev = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT")
        c = CursorOTelCollector(replay_events=None, listen_port=0)
        c.activate()
        self.assertEqual(os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT"), prev)
        got: list = []
        thread = threading.Thread(target=_drain, args=(c, got), daemon=True)
        thread.start()
        time.sleep(0.05)
        _post(f"http://127.0.0.1:{c.listen_port}/otel/cursor/v1/traces", {"ok": True})
        time.sleep(0.05)
        c.deactivate()
        thread.join(timeout=5)
        self.assertEqual(got[0].raw_payload["ok"], True)
