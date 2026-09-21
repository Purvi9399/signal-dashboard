"""Minimal Supabase REST sink using the service role (local / server only).

Never ship SUPABASE_SERVICE_ROLE_KEY in the frozen agent, Intune scripts,
or policy.json. See package/UPLOAD.md.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Sequence
from uuid import uuid4

DEFAULT_SUPABASE_URL = "https://cdftgybwybuzcyurlccf.supabase.co"
DEFAULT_TENANT_ID = "00000000-0000-0000-0000-000000000001"
DEFAULT_TENANT_NAME = "Pilot"
DEFAULT_BATCH_SIZE = 100


def _first_env(*names: str, default: Optional[str] = None) -> Optional[str]:
    for name in names:
        value = os.environ.get(name)
        if value:
            return value
    return default


def _require_service_key() -> str:
    key = _first_env("SUPABASE_SERVICE_ROLE_KEY", "SUPABASE_KEY")
    if not key:
        raise EnvironmentError(
            "SUPABASE_SERVICE_ROLE_KEY must be set in the environment "
            "(local/server only; never commit or bake into the binary)."
        )
    return key


def _require_device_id() -> str:
    device_id = _first_env("SIGNAL_DEVICE_ID")
    if not device_id:
        raise EnvironmentError(
            "SIGNAL_DEVICE_ID must be set (stable text id for public.devices)."
        )
    return device_id


def _supabase_url() -> str:
    value = _first_env("SUPABASE_URL", default=DEFAULT_SUPABASE_URL)
    if value is None:
        return DEFAULT_SUPABASE_URL
    return value.rstrip("/")


def _batch_size_from_env() -> int:
    raw = _first_env("SIGNAL_UPLOAD_BATCH_SIZE", default=str(DEFAULT_BATCH_SIZE))
    if raw is None:
        return DEFAULT_BATCH_SIZE
    return max(1, int(raw))


def _sink_fields(key: str, device_id: str) -> Dict[str, Any]:
    return {
        "url": _supabase_url(),
        "service_role_key": key,
        "tenant_id": _first_env("SIGNAL_TENANT_ID", default=DEFAULT_TENANT_ID),
        "device_id": device_id,
        "tenant_name": _first_env("SIGNAL_TENANT_NAME", default=DEFAULT_TENANT_NAME),
        "tenant_domain": _first_env("SIGNAL_TENANT_DOMAIN"),
        "operator_email": _first_env("SIGNAL_OPERATOR_EMAIL"),
        "hostname": _first_env("SIGNAL_HOSTNAME"),
        "os_name": _first_env("SIGNAL_OS"),
        "package_version": _first_env("SIGNAL_PACKAGE_VERSION"),
        "redaction_level": _first_env("SIGNAL_REDACTION_LEVEL", default="redacted"),
        "batch_size": _batch_size_from_env(),
    }


@dataclass(frozen=True)
class SinkConfig:
    url: str
    service_role_key: str
    tenant_id: str
    device_id: str
    tenant_name: str = DEFAULT_TENANT_NAME
    tenant_domain: Optional[str] = None
    operator_email: Optional[str] = None
    hostname: Optional[str] = None
    os_name: Optional[str] = None
    package_version: Optional[str] = None
    redaction_level: str = "redacted"
    batch_size: int = DEFAULT_BATCH_SIZE

    @classmethod
    def from_env(cls) -> "SinkConfig":
        return cls(**_sink_fields(_require_service_key(), _require_device_id()))


class SupabaseSink:
    def __init__(self, config: SinkConfig) -> None:
        self.config = config

    def _headers(self, *, prefer: Optional[str] = None) -> Dict[str, str]:
        headers = {
            "apikey": self.config.service_role_key,
            "Authorization": f"Bearer {self.config.service_role_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        if prefer:
            headers["Prefer"] = prefer
        return headers

    def _request(
        self,
        method: str,
        path: str,
        *,
        body: Any = None,
        prefer: Optional[str] = None,
        query: Optional[Dict[str, str]] = None,
    ) -> Any:
        qs = f"?{urllib.parse.urlencode(query)}" if query else ""
        url = f"{self.config.url}/rest/v1/{path.lstrip('/')}{qs}"
        data = None if body is None else json.dumps(body).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            headers=self._headers(prefer=prefer),
            method=method,
        )
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                raw = resp.read()
                if not raw:
                    return None
                return json.loads(raw.decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(
                f"Supabase {method} {path} failed: HTTP {exc.code}: {detail}"
            ) from exc

    def ensure_tenant(self) -> None:
        row: Dict[str, Any] = {
            "tenant_id": self.config.tenant_id,
            "name": self.config.tenant_name,
            "redaction_policy": self.config.redaction_level,
            "active": True,
        }
        if self.config.tenant_domain:
            row["domain"] = self.config.tenant_domain
        self._request(
            "POST",
            "tenants",
            body=row,
            prefer="resolution=merge-duplicates,return=minimal",
            query={"on_conflict": "tenant_id"},
        )

    def ensure_device(self) -> None:
        now = datetime.now(timezone.utc).isoformat()
        row: Dict[str, Any] = {
            "device_id": self.config.device_id,
            "tenant_id": self.config.tenant_id,
            "last_seen": now,
            "active": True,
        }
        if self.config.hostname:
            row["hostname"] = self.config.hostname
        if self.config.os_name:
            row["os"] = self.config.os_name
        if self.config.operator_email:
            row["operator_email"] = self.config.operator_email
        if self.config.package_version:
            row["package_version"] = self.config.package_version
        self._request(
            "POST",
            "devices",
            body=row,
            prefer="resolution=merge-duplicates,return=minimal",
            query={"on_conflict": "device_id"},
        )

    def insert_source_events(self, rows: Sequence[Dict[str, Any]]) -> int:
        if not rows:
            return 0
        inserted = 0
        batch = self.config.batch_size
        for i in range(0, len(rows), batch):
            chunk = list(rows[i : i + batch])
            self._request(
                "POST",
                "source_events",
                body=chunk,
                prefer="return=minimal",
            )
            inserted += len(chunk)
        return inserted


def new_ingest_batch_id() -> str:
    return str(uuid4())
