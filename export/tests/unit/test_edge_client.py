"""Unit tests for EdgeIngestClient request construction (offline)."""

from __future__ import annotations

import json
import unittest
from unittest import mock

from export.edge_client import EdgeIngestClient, build_ingest_body


class BuildIngestBodyTests(unittest.TestCase):
    def test_body_includes_batch_and_events(self) -> None:
        rows = [{"tenant_id": "t", "device_id": "d", "payload": {}}]
        body = build_ingest_body(
            ingest_batch_id="batch-1",
            events=rows,
            tenant={"tenant_id": "t", "name": "Pilot"},
            device={"device_id": "d", "tenant_id": "t"},
        )
        self.assertEqual(body["ingest_batch_id"], "batch-1")
        self.assertEqual(body["events"], rows)
        self.assertEqual(body["tenant"]["name"], "Pilot")
        self.assertEqual(body["device"]["device_id"], "d")
        self.assertNotIn("observables", body)

    def test_body_includes_observables_when_present(self) -> None:
        obs = [
            {
                "dedup_key": "d1",
                "tool": "antigravity",
                "observable_type": "tool_request",
            }
        ]
        body = build_ingest_body(
            ingest_batch_id="batch-1",
            events=[{"payload": {}}],
            tenant={"tenant_id": "t", "name": "Pilot"},
            device={"device_id": "d", "tenant_id": "t"},
            observables=obs,
        )
        self.assertEqual(body["observables"], obs)


class EdgeIngestClientTests(unittest.TestCase):
    def test_headers_include_upload_secret(self) -> None:
        client = EdgeIngestClient(
            ingest_url="https://example.test/functions/v1/signal-ingest",
            upload_secret="sec-abc",
        )
        headers = client.request_headers()
        self.assertEqual(headers["Content-Type"], "application/json")
        self.assertEqual(headers["x-signal-upload-secret"], "sec-abc")

    def test_post_events_parses_success(self) -> None:
        client = EdgeIngestClient(
            ingest_url="https://example.test/functions/v1/signal-ingest",
            upload_secret="sec-abc",
        )
        body = build_ingest_body(
            ingest_batch_id="batch-1",
            events=[{"tenant_id": "t", "device_id": "d", "payload": {}}],
            tenant={"tenant_id": "t", "name": "Pilot"},
            device={"device_id": "d", "tenant_id": "t"},
        )
        fake_resp = json.dumps({"inserted": 1, "ingest_batch_id": "batch-1"}).encode()
        with mock.patch("export.edge_client.urlopen_json", return_value=fake_resp) as post:
            result = client.post_events(body)
        self.assertEqual(result["inserted"], 1)
        self.assertEqual(result["ingest_batch_id"], "batch-1")
        self.assertTrue(post.called)


if __name__ == "__main__":
    unittest.main()
