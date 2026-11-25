"""Unit tests for upload mode and edge config (offline)."""

from __future__ import annotations

import os
import unittest
from unittest import mock

from export.upload_config import UploadMode, load_upload_settings


class UploadModeTests(unittest.TestCase):
    def test_default_mode_is_direct(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=True):
            os.environ["SIGNAL_DEVICE_ID"] = "dev-1"
            os.environ["SUPABASE_SERVICE_ROLE_KEY"] = "svc"
            settings = load_upload_settings(dry_run=False)
        self.assertEqual(settings.mode, UploadMode.DIRECT)

    def test_edge_mode_requires_url_and_secret(self) -> None:
        env = {
            "SIGNAL_UPLOAD_MODE": "edge",
            "SIGNAL_DEVICE_ID": "dev-1",
            "SIGNAL_INGEST_URL": "https://example.test/functions/v1/signal-ingest",
            "SIGNAL_UPLOAD_SECRET": "upload-secret",
        }
        with mock.patch.dict(os.environ, env, clear=True):
            settings = load_upload_settings(dry_run=False)
        self.assertEqual(settings.mode, UploadMode.EDGE)
        self.assertEqual(settings.ingest_url, env["SIGNAL_INGEST_URL"])
        self.assertEqual(settings.upload_secret, "upload-secret")
        self.assertEqual(settings.service_role_key, "")

    def test_edge_mode_rejects_missing_secret(self) -> None:
        env = {
            "SIGNAL_UPLOAD_MODE": "edge",
            "SIGNAL_DEVICE_ID": "dev-1",
            "SIGNAL_INGEST_URL": "https://example.test/functions/v1/signal-ingest",
        }
        with mock.patch.dict(os.environ, env, clear=True):
            with self.assertRaises(EnvironmentError):
                load_upload_settings(dry_run=False)

    def test_dry_run_edge_without_secret_ok(self) -> None:
        env = {
            "SIGNAL_UPLOAD_MODE": "edge",
            "SIGNAL_DEVICE_ID": "dev-1",
            "SIGNAL_INGEST_URL": "https://example.test/functions/v1/signal-ingest",
        }
        with mock.patch.dict(os.environ, env, clear=True):
            settings = load_upload_settings(dry_run=True)
        self.assertEqual(settings.mode, UploadMode.EDGE)


if __name__ == "__main__":
    unittest.main()
