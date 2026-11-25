"""Integration tests: client contract with mocked HTTP."""

from __future__ import annotations

import json
import unittest
from unittest import mock

from export.edge_client import EdgeIngestClient, build_ingest_body
from export.validate_ingest import validate_ingest_body


class EdgeContractIntegrationTests(unittest.TestCase):
    def test_post_sends_secret_and_json_body(self) -> None:
        client = EdgeIngestClient(
            ingest_url="https://example.test/functions/v1/signal-ingest",
            upload_secret="secret-1",
        )
        body = build_ingest_body(
            ingest_batch_id="batch-9",
            events=[{"tenant_id": "t", "device_id": "d", "payload": {"a": 1}}],
            tenant={"tenant_id": "t", "name": "Pilot"},
            device={"device_id": "d", "tenant_id": "t"},
        )
        validate_ingest_body(body)
        captured = {}

        def fake_urlopen(url, *, data, headers, timeout=60.0):
            captured["url"] = url
            captured["data"] = data
            captured["headers"] = headers
            return json.dumps({"inserted": 1, "ingest_batch_id": "batch-9"}).encode()

        with mock.patch("export.edge_client.urlopen_json", side_effect=fake_urlopen):
            result = client.post_events(body)

        self.assertEqual(result["inserted"], 1)
        self.assertEqual(captured["url"], client.ingest_url)
        self.assertEqual(captured["headers"]["x-signal-upload-secret"], "secret-1")
        sent = json.loads(captured["data"].decode("utf-8"))
        self.assertEqual(sent["ingest_batch_id"], "batch-9")
        self.assertEqual(len(sent["events"]), 1)

    def test_post_includes_observables_when_present(self) -> None:
        client = EdgeIngestClient(
            ingest_url="https://example.test/functions/v1/signal-ingest",
            upload_secret="secret-1",
        )
        obs = [
            {
                "dedup_key": "d1",
                "tool": "antigravity",
                "observable_type": "session_start",
            }
        ]
        body = build_ingest_body(
            ingest_batch_id="batch-9",
            events=[{"tenant_id": "t", "device_id": "d", "payload": {"a": 1}}],
            tenant={"tenant_id": "t", "name": "Pilot"},
            device={"device_id": "d", "tenant_id": "t"},
            observables=obs,
        )
        validate_ingest_body(body)
        captured = {}

        def fake_urlopen(url, *, data, headers, timeout=60.0):
            captured["data"] = data
            return json.dumps(
                {
                    "inserted": 1,
                    "observables_upserted": 1,
                    "ingest_batch_id": "batch-9",
                }
            ).encode()

        with mock.patch("export.edge_client.urlopen_json", side_effect=fake_urlopen):
            result = client.post_events(body)

        self.assertEqual(result["observables_upserted"], 1)
        sent = json.loads(captured["data"].decode("utf-8"))
        self.assertEqual(sent["observables"], obs)

    def test_http_error_surfaces_status(self) -> None:
        client = EdgeIngestClient(
            ingest_url="https://example.test/functions/v1/signal-ingest",
            upload_secret="bad",
        )
        body = build_ingest_body(
            ingest_batch_id="batch-9",
            events=[{"payload": {}}],
            tenant={"tenant_id": "t", "name": "Pilot"},
            device={"device_id": "d", "tenant_id": "t"},
        )
        with mock.patch(
            "export.edge_client.urlopen_json",
            side_effect=RuntimeError("ingest POST failed: HTTP 401: unauthorized"),
        ):
            with self.assertRaises(RuntimeError) as ctx:
                client.post_events(body)
        self.assertIn("401", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
