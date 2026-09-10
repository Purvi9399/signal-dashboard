"""Red/green tests for Phase 3 Intune promote payloads and intunewin metadata."""

from __future__ import annotations

import tempfile
import unittest
import zipfile
from pathlib import Path
from xml.etree.ElementTree import Element, SubElement, tostring


def _sample_detection_xml() -> bytes:
    root = Element("ApplicationInfo")
    SubElement(root, "Name").text = "signal-agent"
    SubElement(root, "FileName").text = "IntunePackage.intunewin"
    SubElement(root, "UnencryptedContentSize").text = "12345"
    enc = SubElement(root, "EncryptionInfo")
    SubElement(enc, "EncryptionKey").text = "encKey=="
    SubElement(enc, "MacKey").text = "macKey=="
    SubElement(enc, "InitializationVector").text = "iv=="
    SubElement(enc, "Mac").text = "mac=="
    SubElement(enc, "ProfileIdentifier").text = "ProfileVersion1"
    SubElement(enc, "FileDigest").text = "digest=="
    SubElement(enc, "FileDigestAlgorithm").text = "SHA256"
    return tostring(root, encoding="utf-8", xml_declaration=True)


class Win32PayloadTests(unittest.TestCase):
    def test_app_body_has_stable_name_and_commands(self) -> None:
        from package.intune_promote.payloads import build_win32_app_body

        body = build_win32_app_body(
            display_version="1.0.0",
            detect_script='Write-Output "ok"\nexit 0\n',
        )
        self.assertEqual(body["@odata.type"], "#microsoft.graph.win32LobApp")
        self.assertEqual(body["displayName"], "Project Signal Agent")
        self.assertIn("install.ps1", body["installCommandLine"])
        self.assertIn("uninstall.ps1", body["uninstallCommandLine"])
        self.assertEqual(body["installExperience"]["runAsAccount"], "system")
        rules = body["rules"]
        self.assertEqual(len(rules), 1)
        self.assertEqual(rules[0]["ruleType"], "detection")
        self.assertIn("scriptContent", rules[0])

    def test_detection_rule_base64(self) -> None:
        import base64

        from package.intune_promote.payloads import build_powershell_detection_rule

        script = "exit 0\n"
        rule = build_powershell_detection_rule(script)
        decoded = base64.b64decode(rule["scriptContent"]).decode("utf-8")
        self.assertEqual(decoded, script)
        self.assertNotIn("runAsAccount", rule)

    def test_assignment_body(self) -> None:
        from package.intune_promote.payloads import build_required_assignment

        body = build_required_assignment("group-guid")
        self.assertEqual(body["target"]["@odata.type"], "#microsoft.graph.groupAssignmentTarget")
        self.assertEqual(body["target"]["groupId"], "group-guid")
        self.assertEqual(body["intent"], "required")

    def test_commit_encryption_body(self) -> None:
        from package.intune_promote.payloads import build_file_encryption_commit

        meta = {
            "encryption_key": "a",
            "mac_key": "b",
            "initialization_vector": "c",
            "mac": "d",
            "profile_identifier": "ProfileVersion1",
            "file_digest": "e",
            "file_digest_algorithm": "SHA256",
        }
        body = build_file_encryption_commit(meta)
        self.assertEqual(body["fileEncryptionInfo"]["encryptionKey"], "a")


class IntuneWinMetaTests(unittest.TestCase):
    def test_parse_intunewin_metadata(self) -> None:
        from package.intune_promote.intunewin_meta import parse_intunewin_metadata

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "install.intunewin"
            with zipfile.ZipFile(path, "w") as zf:
                zf.writestr("IntuneWinPackage/Metadata/Detection.xml", _sample_detection_xml())
                zf.writestr("IntuneWinPackage/Contents/IntunePackage.intunewin", b"encrypted-bytes")
            meta = parse_intunewin_metadata(path)
            self.assertEqual(meta["file_name"], "IntunePackage.intunewin")
            self.assertEqual(meta["unencrypted_content_size"], 12345)
            self.assertEqual(meta["encryption_key"], "encKey==")
            self.assertEqual(meta["encrypted_content"], b"encrypted-bytes")
            self.assertGreater(meta["size_encrypted"], 0)


class SmokeIssueBodyTests(unittest.TestCase):
    def test_issue_body_mentions_devices(self) -> None:
        from package.intune_promote.payloads import build_post_promote_issue_body

        body = build_post_promote_issue_body("1.0.0", "abcd1234abcd1234")
        self.assertIn("DESKTOP-HFAP22B", body)
        self.assertIn("1.0.0", body)
        self.assertIn("abcd1234abcd1234", body)
        self.assertIn("macOS", body)


if __name__ == "__main__":
    unittest.main()
