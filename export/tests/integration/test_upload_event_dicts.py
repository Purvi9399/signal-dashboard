"""Integration: in-memory events upload via mocked edge client."""

from __future__ import annotations

import unittest
from unittest import mock

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


class UploadEventDictsIntegrationTests(unittest.TestCase):
    def test_uploads_mapped_rows(self) -> None:
        events = [
            {
                "timestamp": "2026-09-10T09:47:22.490610+00:00",
                "collector_id": "antigravity.hooks",
                "agent": "antigravity",
                "raw_payload": {
                    "conversationId": "c1",
                    "initialNumSteps": 1,
                    "modelName": "m",
                },
            }
        ]

        def fake_finish(settings, rows, batch, result, observables):
            out = dict(result)
            out["inserted"] = 1
            out["observables_upserted"] = len(observables)
            out["ingest_batch_id"] = "batch-1"
            return out

        with mock.patch(
            "export.upload_events.finish_upload",
            side_effect=fake_finish,
        ) as finish:
            result = upload_event_dicts(events, settings=_settings(), origin="live-capture")
        self.assertEqual(result["inserted"], 1)
        self.assertGreaterEqual(result["observables_curated"], 1)
        self.assertEqual(result["observables_upserted"], result["observables_curated"])
        self.assertTrue(finish.called)
        rows = finish.call_args[0][1]
        self.assertEqual(rows[0]["tool"], "antigravity")
        self.assertEqual(rows[0]["collector"], "local_hooks")
        observables = finish.call_args[0][4]
        self.assertEqual(observables[0]["tool"], "antigravity")


if __name__ == "__main__":
    unittest.main()
