"""Shared helpers for JSONL and live-capture upload/export.

Keep cyclomatic complexity low. See package/LIVE_EXPORT.md.
On --upload, also curate RawEvents (Purvi stack) and send observables.
"""

from __future__ import annotations

import json
import platform
import socket
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

from .edge_client import EdgeIngestClient, build_ingest_body
from .live_curate import curate_raw_event_dicts
from .mapping import map_raw_event_dict
from .supabase_sink import SinkConfig, SupabaseSink, new_ingest_batch_id
from .upload_config import UploadMode, UploadSettings, load_upload_settings
from .validate_ingest import (
    device_dict,
    tenant_dict,
    validate_event_rows,
    validate_observables,
)

RawEventLike = Any


def check_upload_requires_live(*, live: bool, upload: bool) -> None:
    if upload and not live:
        raise ValueError("--upload requires --live")


def raw_event_to_dict(event: RawEventLike) -> Dict[str, Any]:
    if isinstance(event, Mapping):
        return {
            "timestamp": event.get("timestamp"),
            "collector_id": event.get("collector_id"),
            "agent": event.get("agent"),
            "raw_payload": event.get("raw_payload") or {},
        }
    return {
        "timestamp": event.timestamp,
        "collector_id": event.collector_id,
        "agent": event.agent,
        "raw_payload": event.raw_payload or {},
    }


def raw_events_to_dicts(events: Iterable[RawEventLike]) -> List[Dict[str, Any]]:
    return [raw_event_to_dict(ev) for ev in events]


def write_jsonl(path: Path, dicts: Sequence[Mapping[str, Any]]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for row in dicts:
            fh.write(json.dumps(row, default=str))
            fh.write("\n")
    return len(dicts)


def with_host_defaults(settings: UploadSettings) -> UploadSettings:
    if settings.hostname and settings.os_name:
        return settings
    return UploadSettings(
        mode=settings.mode,
        device_id=settings.device_id,
        tenant_id=settings.tenant_id,
        tenant_name=settings.tenant_name,
        redaction_level=settings.redaction_level,
        batch_size=settings.batch_size,
        supabase_url=settings.supabase_url,
        service_role_key=settings.service_role_key,
        ingest_url=settings.ingest_url,
        upload_secret=settings.upload_secret,
        tenant_domain=settings.tenant_domain,
        operator_email=settings.operator_email,
        hostname=settings.hostname or socket.gethostname(),
        os_name=settings.os_name or platform.platform(),
        package_version=settings.package_version,
    )


def base_upload_result(
    *,
    origin: str,
    rows: List[Dict[str, Any]],
    ingest_batch_id: str,
    settings: UploadSettings,
    dry_run: bool,
) -> Dict[str, Any]:
    return {
        "file": origin,
        "events_read": len(rows),
        "ingest_batch_id": ingest_batch_id,
        "tenant_id": settings.tenant_id,
        "device_id": settings.device_id,
        "mode": settings.mode.value,
        "dry_run": dry_run,
        "inserted": 0,
        "observables_upserted": 0,
        "observables_curated": 0,
    }


def upload_direct(
    settings: UploadSettings,
    rows: List[Dict[str, Any]],
    observables: Sequence[Dict[str, Any]],
) -> Dict[str, int]:
    from curate.observables_sink import upsert_observables

    cfg = SinkConfig(
        url=settings.supabase_url,
        service_role_key=settings.service_role_key,
        tenant_id=settings.tenant_id,
        device_id=settings.device_id,
        tenant_name=settings.tenant_name,
        tenant_domain=settings.tenant_domain,
        operator_email=settings.operator_email,
        hostname=settings.hostname,
        os_name=settings.os_name,
        package_version=settings.package_version,
        redaction_level=settings.redaction_level,
        batch_size=settings.batch_size,
    )
    sink = SupabaseSink(cfg)
    sink.ensure_tenant()
    sink.ensure_device()
    inserted = sink.insert_source_events(rows)
    obs_n = upsert_observables(list(observables), key=settings.service_role_key)
    return {"inserted": inserted, "observables_upserted": obs_n}


def upload_edge(
    settings: UploadSettings,
    rows: List[Dict[str, Any]],
    ingest_batch_id: str,
    observables: Sequence[Dict[str, Any]],
) -> Dict[str, Any]:
    body = build_ingest_body(
        ingest_batch_id=ingest_batch_id,
        events=rows,
        tenant=tenant_dict(settings),
        device=device_dict(settings),
        observables=list(observables) if observables else None,
    )
    client = EdgeIngestClient(
        ingest_url=settings.ingest_url,
        upload_secret=settings.upload_secret,
    )
    return client.post_events(body)


def finish_upload(
    settings: UploadSettings,
    rows: List[Dict[str, Any]],
    ingest_batch_id: str,
    result: Dict[str, Any],
    observables: Sequence[Dict[str, Any]],
) -> Dict[str, Any]:
    if settings.mode is UploadMode.EDGE:
        remote = upload_edge(settings, rows, ingest_batch_id, observables)
        result["inserted"] = int(remote.get("inserted") or 0)
        result["observables_upserted"] = int(remote.get("observables_upserted") or 0)
        result["ingest_batch_id"] = remote.get("ingest_batch_id") or ingest_batch_id
        return result
    counts = upload_direct(settings, rows, observables)
    result["inserted"] = counts["inserted"]
    result["observables_upserted"] = counts["observables_upserted"]
    return result


def map_event_dicts(
    events: Sequence[Mapping[str, Any]],
    settings: UploadSettings,
    ingest_batch_id: str,
    origin: str,
    *,
    ref_prefix: str = "idx",
) -> List[Dict[str, Any]]:
    rows = [
        map_raw_event_dict(
            event,
            tenant_id=settings.tenant_id,
            device_id=settings.device_id,
            ingest_batch_id=ingest_batch_id,
            redaction_level=settings.redaction_level,
            file_origin=origin,
            source_ref=f"{ingest_batch_id}:{ref_prefix}:{index}",
        )
        for index, event in enumerate(events, start=1)
    ]
    validate_event_rows(rows)
    return rows


def build_observables(
    events: Sequence[Mapping[str, Any]],
    settings: UploadSettings,
    ingest_batch_id: str,
) -> List[Dict[str, Any]]:
    from .observable_rows import align_observable_keys

    records = curate_raw_event_dicts(
        events,
        tenant_id=settings.tenant_id,
        device_id=settings.device_id,
        ingest_batch_id=ingest_batch_id,
    )
    aligned = align_observable_keys(records)
    validate_observables(aligned)
    return aligned


def upload_event_dicts(
    events: Sequence[Mapping[str, Any]],
    *,
    settings: Optional[UploadSettings] = None,
    dry_run: bool = False,
    origin: str = "live-capture",
    ref_prefix: str = "idx",
) -> Dict[str, Any]:
    cfg = with_host_defaults(settings or load_upload_settings(dry_run=dry_run))
    ingest_batch_id = new_ingest_batch_id()
    rows = map_event_dicts(
        events, cfg, ingest_batch_id, origin, ref_prefix=ref_prefix
    )
    observables = build_observables(events, cfg, ingest_batch_id)
    result = base_upload_result(
        origin=origin,
        rows=rows,
        ingest_batch_id=ingest_batch_id,
        settings=cfg,
        dry_run=dry_run,
    )
    result["observables_curated"] = len(observables)
    if dry_run:
        result["sample_row"] = rows[0] if rows else None
        result["sample_observable"] = observables[0] if observables else None
        return result
    return finish_upload(cfg, rows, ingest_batch_id, result, observables)
