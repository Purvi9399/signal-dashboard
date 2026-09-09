"""Orchestrate in-place Win32 content promote via Microsoft Graph."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

from package.intune_promote.blob_upload import upload_blocks
from package.intune_promote.graph import GraphClient
from package.intune_promote.intunewin_meta import parse_intunewin_metadata
from package.intune_promote.payloads import (
    APP_DISPLAY_NAME,
    build_content_file_placeholder,
    build_detection_rules_patch,
    build_file_encryption_commit,
    build_post_promote_issue_body,
    build_required_assignment,
    build_win32_app_body,
)


def find_app_id_by_name(client: GraphClient, display_name: str) -> Optional[str]:
    """Return mobileApp id for displayName, or None."""
    # $filter on mobileApps is limited; list and match locally for pilot scale.
    data = client.get("/deviceAppManagement/mobileApps?$select=id,displayName")
    for item in data.get("value", []):
        if item.get("displayName") == display_name:
            return str(item["id"])
    return None


def ensure_win32_app(
    client: GraphClient, *, semver: str, detect_script: str
) -> str:
    """Find or create Project Signal Agent Win32 app; return app id."""
    existing = find_app_id_by_name(client, APP_DISPLAY_NAME)
    if existing:
        return existing
    created = client.post(
        "/deviceAppManagement/mobileApps",
        build_win32_app_body(display_version=semver, detect_script=detect_script),
    )
    return str(created["id"])


def upload_intunewin_content(
    client: GraphClient, *, app_id: str, intunewin_path: Path
) -> str:
    """Upload .intunewin encrypted payload; return content version id."""
    meta = parse_intunewin_metadata(intunewin_path)
    version = client.post(
        f"/deviceAppManagement/mobileApps/{app_id}/microsoft.graph.win32LobApp/contentVersions",
        {},
    )
    version_id = str(version["id"])
    files_path = (
        f"/deviceAppManagement/mobileApps/{app_id}/"
        f"microsoft.graph.win32LobApp/contentVersions/{version_id}/files"
    )
    placeholder = client.post(files_path, build_content_file_placeholder(meta))
    file_id = str(placeholder["id"])
    file_path = f"{files_path}/{file_id}"
    status = client.poll_until(
        file_path, field="uploadState", wanted="azureStorageUriRequestSuccess"
    )
    upload_blocks(str(status["azureStorageUri"]), meta["encrypted_content"])
    client.post(f"{file_path}/commit", build_file_encryption_commit(meta))
    client.poll_until(file_path, field="uploadState", wanted="commitFileSuccess")
    client.patch(
        f"/deviceAppManagement/mobileApps/{app_id}",
        {
            "@odata.type": "#microsoft.graph.win32LobApp",
            "committedContentVersion": version_id,
        },
    )
    return version_id


def refresh_detection(client: GraphClient, *, app_id: str, detect_script: str) -> None:
    """PATCH detection rules to the release-pinned detect.ps1."""
    client.patch(
        f"/deviceAppManagement/mobileApps/{app_id}",
        build_detection_rules_patch(detect_script),
    )


def ensure_group_assignment(
    client: GraphClient, *, app_id: str, group_id: str
) -> None:
    """Create required assignment if the group is not already targeted."""
    existing = client.get(f"/deviceAppManagement/mobileApps/{app_id}/assignments")
    for row in existing.get("value", []):
        target = row.get("target") or {}
        if target.get("groupId") == group_id:
            return
    client.post(
        f"/deviceAppManagement/mobileApps/{app_id}/assignments",
        build_required_assignment(group_id),
    )


def resolve_group_id(client: GraphClient, group_display_name: str) -> str:
    """Resolve Entra group id by displayName."""
    from urllib.parse import quote

    filt = quote(f"displayName eq '{group_display_name}'")
    data = client.get(f"/groups?$filter={filt}&$select=id,displayName")
    rows = data.get("value") or []
    if not rows:
        raise RuntimeError(f"group not found: {group_display_name}")
    return str(rows[0]["id"])


def promote_win32(
    client: Optional[GraphClient],
    *,
    intunewin_path: Path,
    detect_ps1_path: Path,
    semver: str,
    group_name: str = "signal-pilot-windows",
    dry_run: bool = False,
) -> Dict[str, Any]:
    """In-place promote: ensure app, upload content, refresh detection, assign."""
    detect_script = detect_ps1_path.read_text(encoding="utf-8")
    plan: Dict[str, Any] = {
        "app_display_name": APP_DISPLAY_NAME,
        "semver": semver,
        "group": group_name,
        "intunewin": str(intunewin_path),
        "dry_run": dry_run,
    }
    if dry_run:
        return plan
    if client is None:
        raise ValueError("Graph client is required unless dry_run=True")
    app_id = ensure_win32_app(client, semver=semver, detect_script=detect_script)
    version_id = upload_intunewin_content(
        client, app_id=app_id, intunewin_path=intunewin_path
    )
    client.patch(
        f"/deviceAppManagement/mobileApps/{app_id}",
        {
            "@odata.type": "#microsoft.graph.win32LobApp",
            "displayVersion": semver,
        },
    )
    refresh_detection(client, app_id=app_id, detect_script=detect_script)
    group_id = resolve_group_id(client, group_name)
    ensure_group_assignment(client, app_id=app_id, group_id=group_id)
    plan.update({"app_id": app_id, "content_version_id": version_id, "group_id": group_id})
    return plan
