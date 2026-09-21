"""Acceptance: antigravity JSONL curates observables on upload dry-run."""

from __future__ import annotations

import unittest
from pathlib import Path

from export.upload_config import UploadMode, UploadSettings
from export.upload_events import upload_event_dicts
from export.upload_jsonl import load_jsonl

_ROOT = Path(__file__).resolve().parents[4]
_SAMPLE = (
    _ROOT
    / "live_tests"
    / "antigravity_hooks_live"
    / "session_10prompts_20260910_151413"
    / "live_capture_output.jsonl"
)


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


class AntigravityUploadCurateAcceptance(unittest.TestCase):
    def test_dry_run_curates_antigravity_observables(self) -> None:
        if not _SAMPLE.is_file():
            self.skipTest(f"missing sample {_SAMPLE}")
        loaded = load_jsonl(_SAMPLE)
        events = [ev for _n, ev in loaded]
        result = upload_event_dicts(
            events, settings=_settings(), dry_run=True, origin=str(_SAMPLE)
        )
        self.assertGreater(result["events_read"], 0)
        self.assertGreater(result["observables_curated"], 0)
        sample = result.get("sample_observable")
        self.assertIsNotNone(sample)
        assert sample is not None
        self.assertEqual(sample["tool"], "antigravity")
        self.assertNotEqual(sample["observable_type"], "other")
        self.assertTrue(sample.get("dedup_key"))


if __name__ == "__main__":
    unittest.main()
