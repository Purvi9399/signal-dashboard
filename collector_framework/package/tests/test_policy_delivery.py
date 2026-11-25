"""Red/green tests for Intune Windows policy delivery helpers."""

from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path

from policy.policy_engine import PolicyConfig


FRAMEWORK_ROOT = Path(__file__).resolve().parents[2]
ORG_POLICY = FRAMEWORK_ROOT / "package" / "intune" / "windows" / "policy" / "org_policy.json"


class PolicyPathTests(unittest.TestCase):
    def test_windows_policy_path(self) -> None:
        from package.policy_delivery import WINDOWS_POLICY_PATH

        self.assertEqual(
            WINDOWS_POLICY_PATH,
            r"C:\ProgramData\ProjectSignal\policy.json",
        )

    def test_macos_policy_path(self) -> None:
        from package.policy_delivery import MACOS_POLICY_PATH

        self.assertEqual(
            MACOS_POLICY_PATH,
            "/Library/Application Support/ProjectSignal/policy.json",
        )


class Sha256Tests(unittest.TestCase):
    def test_sha256_bytes_lowercase_hex(self) -> None:
        from package.policy_delivery import sha256_bytes

        digest = sha256_bytes(b"abc")
        self.assertEqual(
            digest,
            hashlib.sha256(b"abc").hexdigest(),
        )
        self.assertEqual(digest, digest.lower())


class OrgPolicyTests(unittest.TestCase):
    def test_org_policy_loads(self) -> None:
        self.assertTrue(ORG_POLICY.is_file(), f"missing {ORG_POLICY}")
        cfg = PolicyConfig.load(str(ORG_POLICY))
        self.assertIn(cfg.default_mode, ("allow", "deny"))


class RenderScriptTests(unittest.TestCase):
    def test_detect_contains_hash_of_org_policy(self) -> None:
        from package.policy_delivery import render_detect_ps1, sha256_bytes

        raw = ORG_POLICY.read_bytes()
        expected = sha256_bytes(raw)
        script = render_detect_ps1(expected_hash=expected)
        self.assertIn(expected, script)
        self.assertIn(r"C:\ProgramData\ProjectSignal\policy.json", script)

    def test_remediate_contains_org_policy_text(self) -> None:
        from package.policy_delivery import render_remediate_ps1
        import base64

        text = ORG_POLICY.read_text(encoding="utf-8")
        script = render_remediate_ps1(policy_text=text)
        self.assertIn(r"C:\ProgramData\ProjectSignal\policy.json", script)
        self.assertIn("FromBase64String", script)
        # Payload bytes round-trip to the org policy file contents.
        marker = '$b64 = "'
        start = script.index(marker) + len(marker)
        end = script.index('"', start)
        decoded = base64.b64decode(script[start:end]).decode("utf-8")
        self.assertEqual(decoded, text)

    def test_round_trip_hash_matches_detect_pin(self) -> None:
        from package.policy_delivery import sha256_bytes

        raw = ORG_POLICY.read_bytes()
        pin = sha256_bytes(raw)
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "policy.json"
            out.write_bytes(raw)
            self.assertEqual(sha256_bytes(out.read_bytes()), pin)


class WriteScriptsTests(unittest.TestCase):
    def test_write_scripts_align_hash(self) -> None:
        from package.policy_delivery import sha256_bytes, write_windows_policy_scripts

        with tempfile.TemporaryDirectory() as tmp:
            out_dir = Path(tmp)
            write_windows_policy_scripts(ORG_POLICY, out_dir)
            detect = (out_dir / "detect.ps1").read_text(encoding="utf-8")
            remediate = (out_dir / "remediate.ps1").read_text(encoding="utf-8")
            pin = sha256_bytes(ORG_POLICY.read_bytes())
            self.assertIn(pin, detect)
            self.assertIn("ProjectSignal", remediate)

    def test_write_macos_deploy_aligns_hash(self) -> None:
        from package.policy_delivery import sha256_bytes, write_macos_policy_script

        with tempfile.TemporaryDirectory() as tmp:
            out_dir = Path(tmp)
            write_macos_policy_script(ORG_POLICY, out_dir)
            script = (out_dir / "deploy-policy.sh").read_text(encoding="utf-8")
            pin = sha256_bytes(ORG_POLICY.read_bytes())
            self.assertIn(pin, script)
            self.assertIn("/Library/Application Support/ProjectSignal/policy.json", script)


class MacosRenderTests(unittest.TestCase):
    def test_macos_deploy_embeds_policy_bytes(self) -> None:
        import base64

        from package.policy_delivery import render_macos_deploy_sh, sha256_bytes

        text = ORG_POLICY.read_text(encoding="utf-8")
        pin_hash = sha256_bytes(ORG_POLICY.read_bytes())
        script = render_macos_deploy_sh(policy_text=text, expected_hash=pin_hash)
        self.assertIn("base64", script)
        marker = 'B64="'
        start = script.index(marker) + len(marker)
        end = script.index('"', start)
        decoded = base64.b64decode(script[start:end]).decode("utf-8")
        self.assertEqual(decoded, text)


if __name__ == "__main__":
    unittest.main()
