"""Intune policy delivery: Windows remediations + macOS shell deploy scripts.

See package/POLICY_DELIVERY.md. Keeps cyclomatic complexity low (pure helpers).
"""

from __future__ import annotations

import base64
import hashlib
from pathlib import Path

WINDOWS_POLICY_PATH = r"C:\ProgramData\ProjectSignal\policy.json"
WINDOWS_POLICY_DIR = r"C:\ProgramData\ProjectSignal"
MACOS_POLICY_PATH = "/Library/Application Support/ProjectSignal/policy.json"
MACOS_POLICY_DIR = "/Library/Application Support/ProjectSignal"


def sha256_bytes(data: bytes) -> str:
    """Return lowercase hex SHA-256 of raw bytes."""
    return hashlib.sha256(data).hexdigest()


def render_detect_ps1(expected_hash: str) -> str:
    """Intune Proactive Remediation detect script (exit 0 = compliant)."""
    return f"""# Project Signal — Intune Proactive Remediation DETECT (policy.json)
# Exit 0 = compliant (file present + SHA-256 matches). Exit 1 = remediate.
# Generated from package/policy_delivery.py — do not hand-edit hash/path.

$ErrorActionPreference = "Stop"
$PolicyPath = "{WINDOWS_POLICY_PATH}"
$ExpectedHash = "{expected_hash}"

if (-not (Test-Path -LiteralPath $PolicyPath)) {{
    exit 1
}}

$actual = (Get-FileHash -Algorithm SHA256 -LiteralPath $PolicyPath).Hash.ToLowerInvariant()
if ($actual -ne $ExpectedHash) {{
    exit 1
}}

Write-Output "policy compliant: $PolicyPath"
exit 0
"""


def render_remediate_ps1(policy_text: str) -> str:
    """Intune Proactive Remediation remediate script (exact UTF-8 file bytes)."""
    raw = policy_text.encode("utf-8")
    b64 = base64.b64encode(raw).decode("ascii")
    return f"""# Project Signal — Intune Proactive Remediation REMEDIATE (policy.json)
# Writes org policy to ProgramData as exact UTF-8 bytes (SHA-256 matches detect pin).
# Generated from package/policy_delivery.py — regenerate after editing org_policy.json.

$ErrorActionPreference = "Stop"
$PolicyDir = "{WINDOWS_POLICY_DIR}"
$PolicyPath = "{WINDOWS_POLICY_PATH}"

New-Item -ItemType Directory -Path $PolicyDir -Force | Out-Null

$b64 = "{b64}"
$bytes = [Convert]::FromBase64String($b64)
[System.IO.File]::WriteAllBytes($PolicyPath, $bytes)

if (-not (Test-Path -LiteralPath $PolicyPath)) {{
    Write-Error "Failed to write $PolicyPath"
    exit 1
}}

Write-Output "policy written: $PolicyPath"
exit 0
"""


def render_macos_deploy_sh(policy_text: str, expected_hash: str) -> str:
    """Intune macOS shell script: idempotent hash-check + write."""
    b64 = base64.b64encode(policy_text.encode("utf-8")).decode("ascii")
    return f"""#!/bin/bash
# Project Signal — Intune macOS shell script (policy.json)
# Idempotent: exit 0 if hash matches; otherwise write exact UTF-8 bytes.
# Generated from package/policy_delivery.py — regenerate after editing org_policy.json.

set -euo pipefail
POLICY_DIR="{MACOS_POLICY_DIR}"
POLICY_PATH="{MACOS_POLICY_PATH}"
EXPECTED_HASH="{expected_hash}"
B64="{b64}"

/bin/mkdir -p "$POLICY_DIR"

if [[ -f "$POLICY_PATH" ]]; then
  actual="$(/usr/bin/shasum -a 256 "$POLICY_PATH" | /usr/bin/awk '{{print $1}}')"
  if [[ "$actual" == "$EXPECTED_HASH" ]]; then
    echo "policy compliant: $POLICY_PATH"
    exit 0
  fi
fi

echo "$B64" | /usr/bin/base64 -D > "$POLICY_PATH"
/bin/chmod 644 "$POLICY_PATH"
echo "policy written: $POLICY_PATH"
exit 0
"""


def write_windows_policy_scripts(org_policy_path: Path, out_dir: Path) -> None:
    """Write detect.ps1 + remediate.ps1 aligned to org_policy_path bytes."""
    raw = org_policy_path.read_bytes()
    text = raw.decode("utf-8")
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "detect.ps1").write_text(
        render_detect_ps1(sha256_bytes(raw)), encoding="utf-8"
    )
    (out_dir / "remediate.ps1").write_text(
        render_remediate_ps1(text), encoding="utf-8"
    )


def write_macos_policy_script(org_policy_path: Path, out_dir: Path) -> None:
    """Write deploy-policy.sh aligned to org_policy_path bytes."""
    raw = org_policy_path.read_bytes()
    text = raw.decode("utf-8")
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "deploy-policy.sh").write_text(
        render_macos_deploy_sh(text, sha256_bytes(raw)), encoding="utf-8"
    )


def _windows_policy_dir() -> Path:
    return Path(__file__).resolve().parent / "intune" / "windows" / "policy"


def _macos_policy_dir() -> Path:
    return Path(__file__).resolve().parent / "intune" / "macos" / "policy"


if __name__ == "__main__":
    org = _windows_policy_dir() / "org_policy.json"
    write_windows_policy_scripts(org, _windows_policy_dir())
    write_macos_policy_script(org, _macos_policy_dir())
    print(f"Wrote Windows scripts under {_windows_policy_dir()}")
    print(f"Wrote macOS deploy-policy.sh under {_macos_policy_dir()}")
