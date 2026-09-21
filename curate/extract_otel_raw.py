"""Thin OTel extractors for Project Signal RawEvent payloads (Phase 4).

Reshape flat Loki/OTLP-ish attribute dicts and resourceLogs batches into
(res, attrs) pairs for Purvi extract_otel. Envelope tool overrides service
mapping when the agent is already known.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Iterator, Mapping, Optional, Tuple

from curate.signal_curate import extract_otel, ts, unwrap

_KNOWN_TOOLS = frozenset(
    {"claude-code", "cursor", "codex", "copilot", "antigravity"}
)

_TOOL_SERVICE = {
    "claude-code": "claude-code",
    "codex": "codex_tui",
    "antigravity": "gemini-cli",
    "cursor": "cursor",
    "copilot": "github-copilot",
}


def _as_mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _kv_list_to_dict(items: Any) -> Dict[str, Any]:
    if not isinstance(items, list):
        return {}
    out: Dict[str, Any] = {}
    for item in items:
        if not isinstance(item, Mapping) or "key" not in item:
            continue
        out[str(item["key"])] = unwrap(item.get("value"))
    return out


def _attrs_mapping(attrs: Any) -> Dict[str, Any]:
    if isinstance(attrs, Mapping):
        return dict(attrs)
    if isinstance(attrs, list):
        return _kv_list_to_dict(attrs)
    return {}


def _resource_dict(raw: Mapping[str, Any]) -> Dict[str, Any]:
    res = raw.get("resource")
    if not isinstance(res, Mapping):
        return {}
    nested = _attrs_mapping(res.get("attributes"))
    if nested:
        return nested
    return {k: v for k, v in res.items() if k != "attributes"}


def _normalize_event_name(attrs: Dict[str, Any]) -> Dict[str, Any]:
    if attrs.get("event.name") or not attrs.get("event_name"):
        return attrs
    out = dict(attrs)
    out["event.name"] = attrs["event_name"]
    return out


def _attrs_from_nested(raw: Mapping[str, Any]) -> Dict[str, Any]:
    nested = _attrs_mapping(raw.get("attributes"))
    if nested:
        return _normalize_event_name(nested)
    return {}


def _attrs_from_event_keys(raw: Mapping[str, Any]) -> Dict[str, Any]:
    if not (raw.get("event.name") or raw.get("event_name")):
        return {}
    flat = {k: v for k, v in raw.items() if k != "resource"}
    return _normalize_event_name(flat)


def _attrs_from_flat(raw: Mapping[str, Any]) -> Dict[str, Any]:
    nested = _attrs_from_nested(raw)
    if nested:
        return nested
    return _attrs_from_event_keys(raw)


def _ensure_service(res: Dict[str, Any], tool: str) -> Dict[str, Any]:
    if res.get("service.name"):
        return res
    svc = _TOOL_SERVICE.get(tool)
    if not svc:
        return res
    out = dict(res)
    out["service.name"] = svc
    return out


def _stamp_tool(fields: Dict[str, Any], tool: str) -> Dict[str, Any]:
    if tool in _KNOWN_TOOLS:
        fields["tool"] = tool
    return fields


def _with_service_from_attrs(
    res: Dict[str, Any], attrs: Mapping[str, Any]
) -> Dict[str, Any]:
    if res.get("service.name") or not attrs.get("service.name"):
        return res
    out = dict(res)
    out["service.name"] = attrs["service.name"]
    return out


def _pair_from_raw(
    raw: Mapping[str, Any], tool: str
) -> Optional[Tuple[Dict[str, Any], Dict[str, Any]]]:
    attrs = _attrs_from_flat(raw)
    if not attrs:
        return None
    res = _with_service_from_attrs(_resource_dict(raw), attrs)
    return _ensure_service(res, tool), attrs


def _iter_scope_logs(scope_logs: Any) -> Iterator[Mapping[str, Any]]:
    for sl in scope_logs or []:
        if isinstance(sl, Mapping):
            yield sl


def _iter_log_records(scope_logs: Any) -> Iterator[Mapping[str, Any]]:
    for sl in _iter_scope_logs(scope_logs):
        for lr in sl.get("logRecords") or []:
            if isinstance(lr, Mapping):
                yield lr


def _one_resource_log(
    rl: Mapping[str, Any],
) -> Iterator[Tuple[Dict[str, Any], Dict[str, Any], Optional[datetime]]]:
    res = _kv_list_to_dict(_as_mapping(rl.get("resource")).get("attributes"))
    for lr in _iter_log_records(rl.get("scopeLogs")):
        attrs = _normalize_event_name(_kv_list_to_dict(lr.get("attributes")))
        yield res, attrs, ts(attrs.get("event.timestamp"))


def _iter_resource_logs(
    raw: Mapping[str, Any],
) -> Iterator[Tuple[Dict[str, Any], Dict[str, Any], Optional[datetime]]]:
    for rl in raw.get("resourceLogs") or []:
        if isinstance(rl, Mapping):
            yield from _one_resource_log(rl)


def iter_otel_field_dicts(
    raw: Mapping[str, Any],
    when: Optional[datetime],
    *,
    tool: str,
) -> Iterator[Dict[str, Any]]:
    """Yield curator field dicts for one OTel raw_payload (0..N)."""
    if "resourceLogs" in raw:
        for res, attrs, when2 in _iter_resource_logs(raw):
            res = _ensure_service(res, tool)
            yield _stamp_tool(extract_otel(res, attrs, when2 or when), tool)
        return
    pair = _pair_from_raw(raw, tool)
    if pair is None:
        return
    res, attrs = pair
    yield _stamp_tool(extract_otel(res, attrs, when), tool)


def extract_otel_raw(
    raw: Mapping[str, Any],
    when: Optional[datetime],
    *,
    tool: str,
) -> Optional[Dict[str, Any]]:
    """First typed OTel fields from raw, or None (single-event helper)."""
    for fields in iter_otel_field_dicts(raw, when, tool=tool):
        otype = fields.get("observable_type")
        if otype and otype != "other":
            return fields
    return None
