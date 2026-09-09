"""Red/green tests for CI/CD Phase 1 release helpers."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path


class ParseReleaseTagTests(unittest.TestCase):
    def test_strips_v_prefix(self) -> None:
        from package.release_manifest import parse_release_tag

        self.assertEqual(parse_release_tag("v1.2.3"), "1.2.3")

    def test_rejects_missing_v(self) -> None:
        from package.release_manifest import parse_release_tag

        with self.assertRaises(ValueError):
            parse_release_tag("1.2.3")

    def test_rejects_prerelease_tag(self) -> None:
        from package.release_manifest import parse_release_tag

        with self.assertRaises(ValueError):
            parse_release_tag("v1.2.3-rc.1")


class AssertTagMatchesVersionTests(unittest.TestCase):
    def test_ok_when_equal(self) -> None:
        from package.release_manifest import assert_tag_matches_version

        assert_tag_matches_version("v1.0.0", "1.0.0\n")

    def test_raises_when_mismatch(self) -> None:
        from package.release_manifest import assert_tag_matches_version

        with self.assertRaises(ValueError):
            assert_tag_matches_version("v1.0.0", "1.0.1\n")


class AssertBakedSemverTests(unittest.TestCase):
    def test_ok_when_semver_matches(self) -> None:
        from package.release_manifest import assert_baked_semver

        assert_baked_semver({"semver": "1.0.0", "digest": "abc", "tracked_files": 1}, "1.0.0")

    def test_raises_when_semver_mismatch(self) -> None:
        from package.release_manifest import assert_baked_semver

        with self.assertRaises(ValueError):
            assert_baked_semver({"semver": "1.0.1", "digest": "abc", "tracked_files": 1}, "1.0.0")


class BuildVersionManifestTests(unittest.TestCase):
    def test_builds_expected_shape(self) -> None:
        from package.release_manifest import build_version_manifest

        data = build_version_manifest(
            semver="1.0.0",
            digest="abcd1234abcd1234",
            tracked_files=55,
            git_sha="deadbeef",
            built_at="2026-09-09T12:00:00Z",
            artifacts={
                "windows_exe": "signal-agent.exe",
                "macos_bin": "signal-agent-macos",
            },
        )
        self.assertEqual(data["semver"], "1.0.0")
        self.assertEqual(data["digest"], "abcd1234abcd1234")
        self.assertEqual(data["tracked_files"], 55)
        self.assertEqual(data["git_sha"], "deadbeef")
        self.assertEqual(data["artifacts"]["windows_exe"], "signal-agent.exe")

    def test_round_trip_json_file(self) -> None:
        from package.release_manifest import build_version_manifest, write_version_manifest

        data = build_version_manifest(
            semver="1.0.0",
            digest="abcd1234abcd1234",
            tracked_files=55,
            git_sha="deadbeef",
            built_at="2026-09-09T12:00:00Z",
            artifacts={"windows_exe": "signal-agent.exe"},
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "version.json"
            write_version_manifest(path, data)
            loaded = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(loaded, data)


class AssertDigestsAgreeTests(unittest.TestCase):
    def test_ok_when_same(self) -> None:
        from package.release_manifest import assert_digests_agree

        assert_digests_agree(
            {"digest": "aa", "tracked_files": 1, "semver": "1.0.0"},
            {"digest": "aa", "tracked_files": 1, "semver": "1.0.0"},
        )

    def test_raises_when_digest_differs(self) -> None:
        from package.release_manifest import assert_digests_agree

        with self.assertRaises(ValueError):
            assert_digests_agree(
                {"digest": "aa", "tracked_files": 1, "semver": "1.0.0"},
                {"digest": "bb", "tracked_files": 1, "semver": "1.0.0"},
            )


if __name__ == "__main__":
    unittest.main()
