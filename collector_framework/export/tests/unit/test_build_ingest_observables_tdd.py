"""Unit tests: build_ingest_body observables contract (TDD)."""

from __future__ import annotations

import unittest

from export.edge_client import build_ingest_body


class BuildIngestBodyObservablesTdd(unittest.TestCase):
    def test_omits_observables_key_when_none(self) -> None:
        body = build_ingest_body(
            ingest_batch_id="b1",
            events=[{"payload": {}}],
            tenant={"tenant_id": "t"},
            device={"device_id": "d", "tenant_id": "t"},
            observables=None,
        )
        self.assertNotIn("observables", body)

    def test_omits_observables_key_when_empty(self) -> None:
        body = build_ingest_body(
            ingest_batch_id="b1",
            events=[{"payload": {}}],
            tenant={"tenant_id": "t"},
            device={"device_id": "d", "tenant_id": "t"},
            observables=[],
        )
        self.assertNotIn("observables", body)

    def test_includes_observables_copies(self) -> None:
        obs = [{"dedup_key": "d1", "tool": "antigravity", "observable_type": "tool_request"}]
        body = build_ingest_body(
            ingest_batch_id="b1",
            events=[{"payload": {}}],
            tenant={"tenant_id": "t"},
            device={"device_id": "d", "tenant_id": "t"},
            observables=obs,
        )
        self.assertEqual(body["observables"], obs)
        self.assertIsNot(body["observables"][0], obs[0])


if __name__ == "__main__":
    unittest.main()
