"""Integration: mixed-agent hooks JSONL dry-run curates multiple tools."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from export.upload_config import UploadMode, UploadSettings
from export.upload_events import upload_event_dicts


def _settings() -> UploadSettings:
    return UploadSettings(
        mode=UploadMode.EDGE,
        device_id="pilot-mac-1",
        tenant_id="00000000-0000-0000-0000-000000000001",
        tenant_name="Pilot",
        redaction_level="redacted",
        batch_size=100,
        supabase_url="https://example.test",
        service_role_key="",
        ingest_url="https://example.test/functions/v1/signal-ingest",
        upload_secret="sec",
        hostname="host",
        os_name="Darwin",
    )


_MIXED = [
    {
        "timestamp": "2026-09-21T10:00:00+00:00",
        "collector_id": "claude_code.hooks",
        "agent": "claude_code",
        "raw_payload": {
            "hook_event_name": "UserPromptSubmit",
            "session_id": "s1",
            "prompt": "hi",
        },
    },
    {
        "timestamp": "2026-09-21T10:00:01+00:00",
        "collector_id": "cursor.hooks",
        "agent": "cursor",
        "raw_payload": {
            "event_type": "beforeSubmitPrompt",
            "conversation_id": "c1",
            "prompt": "hello",
        },
    },
    {
        "timestamp": "2026-09-21T10:00:02+00:00",
        "collector_id": "antigravity.hooks",
        "agent": "antigravity",
        "raw_payload": {
            "conversationId": "a1",
            "initialNumSteps": 1,
            "modelName": "m",
        },
    },
    {
        "timestamp": "2026-09-21T10:00:03+00:00",
        "collector_id": "codex.hooks",
        "agent": "codex",
        "raw_payload": {
            "hook_event_name": "SessionStart",
            "session_id": "x1",
        },
    },
    {
        "timestamp": "2026-09-21T10:00:04+00:00",
        "collector_id": "copilot.hooks",
        "agent": "copilot",
        "raw_payload": {
            "hook_event_name": "PreToolUse",
            "session_id": "p1",
            "tool_name": "bash",
            "tool_input": {},
        },
    },
]


class MixedHooksDryRunTdd(unittest.TestCase):
    def test_dry_run_curates_all_five_tools(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "mixed.jsonl"
            path.write_text("\n".join(json.dumps(e) for e in _MIXED) + "\n")
            events = [json.loads(line) for line in path.read_text().splitlines() if line]
        result = upload_event_dicts(
            events, settings=_settings(), dry_run=True, origin="mixed-tdd"
        )
        self.assertEqual(result["events_read"], 5)
        self.assertGreaterEqual(result["observables_curated"], 5)
        tools = set()
        # sample is one row; re-curate via build_observables for full set
        from export.upload_events import build_observables

        rows = build_observables(events, _settings(), "batch-mixed")
        tools = {r["tool"] for r in rows}
        self.assertEqual(
            tools,
            {"claude-code", "cursor", "antigravity", "codex", "copilot"},
        )


if __name__ == "__main__":
    unittest.main()
