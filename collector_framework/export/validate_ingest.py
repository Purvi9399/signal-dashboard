"""Validate mapped ingest payloads before network send."""

from __future__ import annotations

from typing import Any, Dict, Mapping, Optional, Sequence

MAX_EVENTS = 500
MAX_OBSERVABLES = 500


def validate_event_rows(rows: Sequence[Mapping[str, Any]]) -> None:
    if len(rows) > MAX_EVENTS:
        raise ValueError(f"events exceed max {MAX_EVENTS}")
    for index, row in enumerate(rows):
        _validate_one_row(index, row)


def _validate_one_row(index: int, row: Mapping[str, Any]) -> None:
    if "payload" not in row:
        raise ValueError(f"events[{index}] missing payload")


def validate_observables(rows: Optional[Sequence[Mapping[str, Any]]]) -> None:
    if rows is None:
        return
    if len(rows) > MAX_OBSERVABLES:
        raise ValueError(f"observables exceed max {MAX_OBSERVABLES}")
    for index, row in enumerate(rows):
        _validate_one_observable(index, row)


def _validate_one_observable(index: int, row: Mapping[str, Any]) -> None:
    for key in ("dedup_key", "observable_type", "tool"):
        if not row.get(key):
            raise ValueError(f"observables[{index}] missing {key}")


def validate_ingest_body(body: Mapping[str, Any]) -> None:
    events = body.get("events")
    if not isinstance(events, list):
        raise ValueError("body.events must be a list")
    validate_event_rows(events)
    obs = body.get("observables")
    if obs is None:
        return
    if not isinstance(obs, list):
        raise ValueError("body.observables must be a list")
    validate_observables(obs)


def tenant_dict(settings: Any) -> Dict[str, Any]:
    out: Dict[str, Any] = {
        "tenant_id": settings.tenant_id,
        "name": settings.tenant_name,
        "redaction_policy": settings.redaction_level,
    }
    if settings.tenant_domain:
        out["domain"] = settings.tenant_domain
    return out


def device_dict(settings: Any) -> Dict[str, Any]:
    out: Dict[str, Any] = {
        "device_id": settings.device_id,
        "tenant_id": settings.tenant_id,
    }
    if settings.hostname:
        out["hostname"] = settings.hostname
    if settings.os_name:
        out["os"] = settings.os_name
    if settings.operator_email:
        out["operator_email"] = settings.operator_email
    if settings.package_version:
        out["package_version"] = settings.package_version
    return out
