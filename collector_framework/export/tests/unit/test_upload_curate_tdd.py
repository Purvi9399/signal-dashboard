"""Unit/integration TDD: upload path curates and forwards observables."""

from __future__ import annotations

import unittest
from unittest import mock

from export.upload_config import UploadMode, UploadSettings
from export.upload_events import build_observables, finish_upload, upload_event_dicts


def _settings(mode: UploadMode = UploadMode.EDGE) -> UploadSettings:
    return UploadSettings(
        mode=mode,
        device_id="pilot-mac-1",
        tenant_id="00000000-0000-0000-0000-000000000001",
        tenant_name="Pilot",
        redaction_level="redacted",
        batch_size=100,
        supabase_url="https://example.test",
        service_role_key="service-role",
        ingest_url="https://example.test/functions/v1/signal-ingest",
        upload_secret="sec",
        hostname="host",
        os_name="Darwin",
    )


_SESSION_EVENT = {
    "timestamp": "2026-08-26T15:22:28+00:00",
    "collector_id": "antigravity.hooks",
    "agent": "antigravity",
    "raw_payload": {
        "conversationId": "sess-up",
        "initialNumSteps": 1,
        "modelName": "m",
    },
}


class BuildObservablesTdd(unittest.TestCase):
    def test_builds_stamped_rows(self) -> None:
        rows = build_observables([_SESSION_EVENT], _settings(), "batch-obs")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["tool"], "antigravity")
        self.assertEqual(rows[0]["ingest_batch_id"], "batch-obs")


class FinishUploadObservablesTdd(unittest.TestCase):
    def test_edge_passes_observables_to_upload_edge(self) -> None:
        obs = [{"dedup_key": "d1", "tool": "antigravity", "observable_type": "session_start"}]
        result = {
            "inserted": 0,
            "observables_upserted": 0,
            "ingest_batch_id": "batch-1",
        }
        with mock.patch(
            "export.upload_events.upload_edge",
            return_value={
                "inserted": 1,
                "observables_upserted": 1,
                "ingest_batch_id": "batch-1",
            },
        ) as edge:
            out = finish_upload(
                _settings(UploadMode.EDGE),
                [{"payload": {}}],
                "batch-1",
                result,
                obs,
            )
        self.assertEqual(out["observables_upserted"], 1)
        self.assertEqual(edge.call_args[0][3], obs)

    def test_direct_passes_observables_to_upload_direct(self) -> None:
        obs = [{"dedup_key": "d1", "tool": "antigravity", "observable_type": "session_start"}]
        result = {
            "inserted": 0,
            "observables_upserted": 0,
            "ingest_batch_id": "batch-1",
        }
        with mock.patch(
            "export.upload_events.upload_direct",
            return_value={"inserted": 2, "observables_upserted": 1},
        ) as direct:
            out = finish_upload(
                _settings(UploadMode.DIRECT),
                [{"payload": {}}],
                "batch-1",
                result,
                obs,
            )
        self.assertEqual(out["inserted"], 2)
        self.assertEqual(out["observables_upserted"], 1)
        self.assertEqual(direct.call_args[0][2], obs)


class UploadEventDictsCurateTdd(unittest.TestCase):
    def test_dry_run_reports_curated_count_and_sample(self) -> None:
        result = upload_event_dicts(
            [_SESSION_EVENT],
            settings=_settings(),
            dry_run=True,
            origin="tdd",
        )
        self.assertTrue(result["dry_run"])
        self.assertEqual(result["observables_curated"], 1)
        self.assertEqual(result["observables_upserted"], 0)
        self.assertEqual(result["sample_observable"]["tool"], "antigravity")
        self.assertEqual(result["sample_observable"]["observable_type"], "session_start")

    def test_live_upload_forwards_observables(self) -> None:
        def fake_finish(settings, rows, batch, result, observables):
            out = dict(result)
            out["inserted"] = len(rows)
            out["observables_upserted"] = len(observables)
            return out

        with mock.patch(
            "export.upload_events.finish_upload", side_effect=fake_finish
        ) as finish:
            result = upload_event_dicts(
                [_SESSION_EVENT],
                settings=_settings(),
                dry_run=False,
                origin="tdd",
            )
        self.assertEqual(result["observables_curated"], 1)
        self.assertEqual(result["observables_upserted"], 1)
        self.assertEqual(len(finish.call_args[0][4]), 1)


class UploadEdgeBodyTdd(unittest.TestCase):
    def test_upload_edge_includes_observables_in_post_body(self) -> None:
        from export.upload_events import upload_edge

        obs = [
            {
                "dedup_key": "d1",
                "tool": "antigravity",
                "observable_type": "session_start",
            }
        ]
        captured = {}

        class FakeClient:
            def __init__(self, **kwargs):
                pass

            def post_events(self, body):
                captured["body"] = body
                return {
                    "inserted": 1,
                    "observables_upserted": 1,
                    "ingest_batch_id": "batch-1",
                }

        with mock.patch("export.upload_events.EdgeIngestClient", FakeClient):
            remote = upload_edge(
                _settings(),
                [{"payload": {"x": 1}}],
                "batch-1",
                obs,
            )
        self.assertEqual(remote["observables_upserted"], 1)
        self.assertEqual(captured["body"]["observables"], obs)
        self.assertEqual(len(captured["body"]["events"]), 1)


if __name__ == "__main__":
    unittest.main()
