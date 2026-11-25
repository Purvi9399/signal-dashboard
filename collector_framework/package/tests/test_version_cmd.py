"""Red/green tests for `signal-agent --json version` semver field."""

from __future__ import annotations

import argparse
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock


class VersionCommandSemverTests(unittest.TestCase):
    def test_json_version_includes_semver_from_version_file(self) -> None:
        import package.cli as cli

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "VERSION").write_text("1.0.0\n", encoding="utf-8")

            fake_manifest = mock.Mock()
            fake_manifest.digest.return_value = "deadbeefdeadbeef"
            fake_manifest.files = {"a": "1", "b": "2"}
            fake_manifest.root = str(root)

            args = argparse.Namespace(json=True)

            with mock.patch.object(cli, "FROZEN", False):
                with mock.patch.object(cli, "COLLECTOR_FRAMEWORK_ROOT", root):
                    with mock.patch.object(cli, "BAKED_VERSION_DIGEST", None):
                        with mock.patch("package.cli.UpdateEngine") as engine_cls:
                            engine_cls.return_value.snapshot.return_value = fake_manifest
                            buf = io.StringIO()
                            with redirect_stdout(buf):
                                code = cli.cmd_version(args)

            self.assertEqual(code, 0)
            data = json.loads(buf.getvalue())
            self.assertEqual(data["semver"], "1.0.0")
            self.assertEqual(data["digest"], "deadbeefdeadbeef")
            self.assertEqual(data["tracked_files"], 2)

    def test_frozen_json_version_uses_baked_semver(self) -> None:
        import package.cli as cli

        args = argparse.Namespace(json=True)
        with mock.patch.object(cli, "FROZEN", True):
            with mock.patch.object(cli, "BAKED_VERSION_DIGEST", "aabbccddeeff0011"):
                with mock.patch.object(cli, "BAKED_TRACKED_FILES", 55):
                    with mock.patch.object(cli, "BAKED_SEMVER", "1.0.0"):
                        buf = io.StringIO()
                        with redirect_stdout(buf):
                            code = cli.cmd_version(args)

        self.assertEqual(code, 0)
        data = json.loads(buf.getvalue())
        self.assertEqual(data["semver"], "1.0.0")
        self.assertEqual(data["digest"], "aabbccddeeff0011")
        self.assertEqual(data["tracked_files"], 55)


if __name__ == "__main__":
    unittest.main()
