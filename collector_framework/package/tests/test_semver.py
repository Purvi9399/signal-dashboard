"""Red/green tests for package.semver (normalize + file load)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from package.semver import load_semver_file, normalize_semver


class NormalizeSemverTests(unittest.TestCase):
    def test_accepts_major_minor_patch(self) -> None:
        self.assertEqual(normalize_semver("1.0.0"), "1.0.0")

    def test_strips_whitespace(self) -> None:
        self.assertEqual(normalize_semver("  2.3.4\n"), "2.3.4")

    def test_rejects_v_prefix(self) -> None:
        with self.assertRaises(ValueError):
            normalize_semver("v1.0.0")

    def test_rejects_prerelease(self) -> None:
        with self.assertRaises(ValueError):
            normalize_semver("1.0.0-rc.1")

    def test_rejects_two_component(self) -> None:
        with self.assertRaises(ValueError):
            normalize_semver("1.0")

    def test_rejects_empty(self) -> None:
        with self.assertRaises(ValueError):
            normalize_semver("   ")


class LoadSemverFileTests(unittest.TestCase):
    def test_loads_single_line_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "VERSION"
            path.write_text("1.2.3\n", encoding="utf-8")
            self.assertEqual(load_semver_file(path), "1.2.3")

    def test_missing_file_raises(self) -> None:
        with self.assertRaises(FileNotFoundError):
            load_semver_file(Path("/nonexistent/VERSION"))

    def test_invalid_contents_raise(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "VERSION"
            path.write_text("not-a-version\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                load_semver_file(path)


if __name__ == "__main__":
    unittest.main()
