"""Pure Graph JSON payloads for Win32 promote (low cyclomatic complexity)."""

from __future__ import annotations

import base64
from typing import Any, Dict, Mapping


APP_DISPLAY_NAME = "Project Signal Agent"
INSTALL_CMD = (
    "powershell.exe -ExecutionPolicy Bypass -NoProfile -File install.ps1"
)
UNINSTALL_CMD = (
    "powershell.exe -ExecutionPolicy Bypass -NoProfile -File uninstall.ps1"
)


def build_powershell_detection_rule(script: str) -> Dict[str, Any]:
    """Win32 PowerShell detection rule (exit 0 + STDOUT = detected).

    Do not set runAsAccount: Graph rejects it on detection script rules
    (allowed on installExperience only).
    """
    encoded = base64.b64encode(script.encode("utf-8")).decode("ascii")
    return {
        "@odata.type": "#microsoft.graph.win32LobAppPowerShellScriptRule",
        "ruleType": "detection",
        "enforceSignatureCheck": False,
        "runAs32Bit": False,
        "scriptContent": encoded,
        "operationType": "notConfigured",
        "operator": "notConfigured",
        "comparisonValue": None,
    }


def _default_return_codes() -> list:
    return [
        {"returnCode": 0, "type": "success"},
        {"returnCode": 1707, "type": "success"},
        {"returnCode": 3010, "type": "softReboot"},
        {"returnCode": 1641, "type": "hardReboot"},
        {"returnCode": 1618, "type": "retry"},
    ]


def build_win32_app_body(*, display_version: str, detect_script: str) -> Dict[str, Any]:
    """Create body for POST /deviceAppManagement/mobileApps (win32LobApp)."""
    return {
        "@odata.type": "#microsoft.graph.win32LobApp",
        "displayName": APP_DISPLAY_NAME,
        "description": "Project Signal collector agent (pilot)",
        "publisher": "Project Signal",
        "displayVersion": display_version,
        "fileName": "install.intunewin",
        "setupFilePath": "install.ps1",
        "installCommandLine": INSTALL_CMD,
        "uninstallCommandLine": UNINSTALL_CMD,
        "applicableArchitectures": "x64",
        "allowAvailableUninstall": True,
        "minimumSupportedWindowsRelease": "1607",
        "installExperience": {
            "@odata.type": "#microsoft.graph.win32LobAppInstallExperience",
            "runAsAccount": "system",
            "deviceRestartBehavior": "basedOnReturnCode",
        },
        "returnCodes": _default_return_codes(),
        "rules": [build_powershell_detection_rule(detect_script)],
    }


def build_detection_rules_patch(detect_script: str) -> Dict[str, Any]:
    """PATCH body to refresh detection rules after a content promote."""
    return {
        "@odata.type": "#microsoft.graph.win32LobApp",
        "rules": [build_powershell_detection_rule(detect_script)],
    }


def build_required_assignment(group_id: str) -> Dict[str, Any]:
    """Required assignment to an Entra group."""
    return {
        "@odata.type": "#microsoft.graph.mobileAppAssignment",
        "intent": "required",
        "target": {
            "@odata.type": "#microsoft.graph.groupAssignmentTarget",
            "groupId": group_id,
        },
    }


def build_file_encryption_commit(meta: Mapping[str, str]) -> Dict[str, Any]:
    """Commit body with encryption info from Detection.xml."""
    return {
        "fileEncryptionInfo": {
            "encryptionKey": meta["encryption_key"],
            "macKey": meta["mac_key"],
            "initializationVector": meta["initialization_vector"],
            "mac": meta["mac"],
            "profileIdentifier": meta["profile_identifier"],
            "fileDigest": meta["file_digest"],
            "fileDigestAlgorithm": meta["file_digest_algorithm"],
        }
    }


def build_content_file_placeholder(meta: Mapping[str, Any]) -> Dict[str, Any]:
    """POST body for contentVersions/{id}/files."""
    return {
        "@odata.type": "#microsoft.graph.mobileAppContentFile",
        "name": meta["file_name"],
        "size": int(meta["unencrypted_content_size"]),
        "sizeEncrypted": int(meta["size_encrypted"]),
        "manifest": None,
        "isDependency": False,
    }


def build_post_promote_issue_body(semver: str, digest: str) -> str:
    """GitHub Issue checklist after Intune promote."""
    return f"""## Post-promote smoke (Project Signal {semver})

Digest pin: `{digest}`

### Windows (`DESKTOP-HFAP22B`)
- [ ] Company Portal / Intune shows app update for **Project Signal Agent**
- [ ] `Get-FileHash` / `--json version` digest matches `{digest}`
- [ ] `--json decide --policy C:\\ProgramData\\ProjectSignal\\policy.json` still works

### macOS (manual PKG if not Graph-uploaded)
- [ ] Install/update `ProjectSignal.pkg` from the GitHub Release
- [ ] `signal-agent --json version` digest matches `{digest}`
- [ ] `--json decide --policy "/Library/Application Support/ProjectSignal/policy.json"` works

### Notes
- Policy Remediations/shell scripts were **not** changed by this promote.
- Report failures as comments on this issue.
"""
