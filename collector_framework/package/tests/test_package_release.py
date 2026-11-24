"""Red/green tests for Phase 2 Win32 detect pin + staging helpers."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path


class RenderWin32DetectTests(unittest.TestCase):
    def test_embeds_digest_and_tracked_files(self) -> None:
        from package.package_release import render_win32_detect_ps1

        script = render_win32_detect_ps1("abcd1234abcd1234", 62)
        self.assertIn('$ExpectedDigest   = "abcd1234abcd1234"', script)
        self.assertIn("$ExpectedTracked  = 62", script)
        self.assertIn("Write-Output", script)
        self.assertIn(r"C:\Program Files\ProjectSignal\signal-agent.exe", script)

    def test_rejects_bad_digest(self) -> None:
        from package.package_release import render_win32_detect_ps1

        with self.assertRaises(ValueError):
            render_win32_detect_ps1("not-hex", 55)


class StageWin32Tests(unittest.TestCase):
    def test_copies_exe_and_scripts(self) -> None:
        from package.package_release import stage_win32_dir

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            exe = root / "signal-agent.exe"
            exe.write_bytes(b"MZ-fake")
            scripts = root / "scripts"
            scripts.mkdir()
            (scripts / "install.ps1").write_text("install", encoding="utf-8")
            (scripts / "uninstall.ps1").write_text("uninstall", encoding="utf-8")
            detect = root / "detect.ps1"
            detect.write_text("detect", encoding="utf-8")
            out = root / "stage"
            stage_win32_dir(
                exe_path=exe,
                install_ps1=scripts / "install.ps1",
                uninstall_ps1=scripts / "uninstall.ps1",
                detect_ps1=detect,
                out_dir=out,
            )
            self.assertTrue((out / "signal-agent.exe").is_file())
            self.assertEqual((out / "install.ps1").read_text(encoding="utf-8"), "install")
            self.assertEqual((out / "uninstall.ps1").read_text(encoding="utf-8"), "uninstall")
            self.assertEqual((out / "detect.ps1").read_text(encoding="utf-8"), "detect")


class WriteDetectFromVersionJsonTests(unittest.TestCase):
    def test_writes_detect_from_version_json(self) -> None:
        import json

        from package.package_release import write_detect_from_version_json

        with tempfile.TemporaryDirectory() as tmp:
            vpath = Path(tmp) / "version.json"
            out = Path(tmp) / "detect.ps1"
            vpath.write_text(
                json.dumps({"digest": "abcd1234abcd1234", "tracked_files": 62, "semver": "1.0.0"}),
                encoding="utf-8",
            )
            write_detect_from_version_json(vpath, out)
            text = out.read_text(encoding="utf-8")
            self.assertIn("abcd1234abcd1234", text)
            self.assertIn("62", text)


if __name__ == "__main__":
    unittest.main()
