"""Load upload mode and settings from the environment.

Keep cyclomatic complexity low: one concern per function.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from enum import Enum
from typing import Optional

from .supabase_sink import (
    DEFAULT_BATCH_SIZE,
    DEFAULT_SUPABASE_URL,
    DEFAULT_TENANT_ID,
    DEFAULT_TENANT_NAME,
)


class UploadMode(str, Enum):
    DIRECT = "direct"
    EDGE = "edge"


@dataclass(frozen=True)
class UploadSettings:
    mode: UploadMode
    device_id: str
    tenant_id: str
    tenant_name: str
    redaction_level: str
    batch_size: int
    supabase_url: str
    service_role_key: str
    ingest_url: str
    upload_secret: str
    tenant_domain: Optional[str] = None
    operator_email: Optional[str] = None
    hostname: Optional[str] = None
    os_name: Optional[str] = None
    package_version: Optional[str] = None


def _env(name: str, default: Optional[str] = None) -> Optional[str]:
    value = os.environ.get(name)
    if value:
        return value
    return default


def _parse_mode(raw: Optional[str]) -> UploadMode:
    text = (raw or UploadMode.DIRECT.value).strip().lower()
    if text == UploadMode.EDGE.value:
        return UploadMode.EDGE
    if text == UploadMode.DIRECT.value:
        return UploadMode.DIRECT
    raise EnvironmentError(
        f"SIGNAL_UPLOAD_MODE must be 'direct' or 'edge', got {raw!r}"
    )


def _require(name: str, value: Optional[str]) -> str:
    if value:
        return value
    raise EnvironmentError(f"{name} must be set.")


def _batch_size() -> int:
    raw = os.environ.get("SIGNAL_UPLOAD_BATCH_SIZE")
    if not raw:
        return DEFAULT_BATCH_SIZE
    return max(1, int(raw))


def _device_id(*, dry_run: bool) -> str:
    device = os.environ.get("SIGNAL_DEVICE_ID")
    if device:
        return device
    if dry_run:
        return "dry-run-device"
    raise EnvironmentError("SIGNAL_DEVICE_ID must be set.")


def _common_fields(*, dry_run: bool) -> dict:
    return {
        "device_id": _device_id(dry_run=dry_run),
        "tenant_id": _env("SIGNAL_TENANT_ID", DEFAULT_TENANT_ID),
        "tenant_name": _env("SIGNAL_TENANT_NAME", DEFAULT_TENANT_NAME),
        "redaction_level": _env("SIGNAL_REDACTION_LEVEL", "redacted"),
        "batch_size": _batch_size(),
        "supabase_url": (_env("SUPABASE_URL", DEFAULT_SUPABASE_URL) or "").rstrip("/"),
        "tenant_domain": _env("SIGNAL_TENANT_DOMAIN"),
        "operator_email": _env("SIGNAL_OPERATOR_EMAIL"),
        "hostname": _env("SIGNAL_HOSTNAME"),
        "os_name": _env("SIGNAL_OS"),
        "package_version": _env("SIGNAL_PACKAGE_VERSION"),
    }


def _service_role_key(*, dry_run: bool) -> str:
    key = _env("SUPABASE_SERVICE_ROLE_KEY") or _env("SUPABASE_KEY") or ""
    if dry_run:
        return key
    return _require("SUPABASE_SERVICE_ROLE_KEY", key or None)


def _load_direct(*, dry_run: bool) -> UploadSettings:
    fields = _common_fields(dry_run=dry_run)
    return UploadSettings(
        mode=UploadMode.DIRECT,
        service_role_key=_service_role_key(dry_run=dry_run),
        ingest_url="",
        upload_secret="",
        **fields,
    )


def _edge_url(*, dry_run: bool) -> str:
    url = _env("SIGNAL_INGEST_URL") or ""
    if dry_run:
        return url.rstrip("/")
    return _require("SIGNAL_INGEST_URL", url or None).rstrip("/")


def _edge_secret(*, dry_run: bool) -> str:
    secret = _env("SIGNAL_UPLOAD_SECRET") or ""
    if dry_run:
        return secret
    return _require("SIGNAL_UPLOAD_SECRET", secret or None)


def _load_edge(*, dry_run: bool) -> UploadSettings:
    fields = _common_fields(dry_run=dry_run)
    return UploadSettings(
        mode=UploadMode.EDGE,
        service_role_key="",
        ingest_url=_edge_url(dry_run=dry_run),
        upload_secret=_edge_secret(dry_run=dry_run),
        **fields,
    )


def load_upload_settings(*, dry_run: bool = False) -> UploadSettings:
    mode = _parse_mode(os.environ.get("SIGNAL_UPLOAD_MODE"))
    if mode is UploadMode.EDGE:
        return _load_edge(dry_run=dry_run)
    return _load_direct(dry_run=dry_run)
