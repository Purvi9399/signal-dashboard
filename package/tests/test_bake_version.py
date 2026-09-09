"""Red/green tests for baking BAKED_SEMVER into _baked_version.py."""

from __future__ import annotations

import importlib
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


class BakeVersionSemverTests(unittest.TestCase):
    def test_bake_writes_baked_semver(self) -> None:
        from package import build_exe

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            version_file = root / "VERSION"
            version_file.write_text("1.0.0\n", encoding="utf-8")
            package_dir = root / "package"
            package_dir.mkdir()
            baked_path = package_dir / "_baked_version.py"

            fake_manifest = mock.Mock()
            fake_manifest.digest.return_value = "abcd1234abcd1234"
            fake_manifest.files = {f"f{i}": "h" for i in range(3)}

            with mock.patch.object(build_exe, "COLLECTOR_FRAMEWORK_ROOT", root):
                with mock.patch("update.update_engine.UpdateEngine") as engine_cls:
                    engine_cls.return_value.snapshot.return_value = fake_manifest
                    build_exe._bake_version()

            text = baked_path.read_text(encoding="utf-8")
            self.assertIn("BAKED_SEMVER = '1.0.0'", text)
            self.assertIn("BAKED_VERSION_DIGEST = 'abcd1234abcd1234'", text)
            self.assertIn("BAKED_TRACKED_FILES = 3", text)

    def test_bake_fails_on_invalid_version(self) -> None:
        from package import build_exe

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "VERSION").write_text("bad\n", encoding="utf-8")
            (root / "package").mkdir()

            fake_manifest = mock.Mock()
            fake_manifest.digest.return_value = "abcd1234abcd1234"
            fake_manifest.files = {}

            with mock.patch.object(build_exe, "COLLECTOR_FRAMEWORK_ROOT", root):
                with mock.patch("update.update_engine.UpdateEngine") as engine_cls:
                    engine_cls.return_value.snapshot.return_value = fake_manifest
                    with self.assertRaises(ValueError):
                        build_exe._bake_version()


if __name__ == "__main__":
    unittest.main()
