"""Extractor for Harbor container-registry events.

Maps Harbor audit-log entries and webhook payloads to Signal observable
flat fields, matching the shape that signal_curate.curate() expects.

Harbor event types mapped:
  PUSH_ARTIFACT      -> container_push
  PULL_ARTIFACT      -> container_pull
  DELETE_ARTIFACT     -> container_delete
  SCANNING_COMPLETED -> scan_complete
  SCANNING_FAILED    -> scan_failed
  REPLICATION        -> container_replicate
  QUOTA_EXCEED       -> quota_exceeded
  QUOTA_WARNING      -> quota_warning
  TAG_RETENTION      -> tag_retention
  CREATE_PROJECT     -> project_create
  DELETE_PROJECT     -> project_delete
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Mapping, Optional

# Harbor audit-log operation → Signal observable_type
_AUDIT_OP_TYPE = {
    "push": "container_push",
    "pull": "container_pull",
    "delete": "container_delete",
    "create": "container_push",
    "stop": "scan_complete",
    "scanAll": "scan_complete",
}

# Harbor webhook type → Signal observable_type
_WEBHOOK_TYPE = {
    "PUSH_ARTIFACT": "container_push",
    "PULL_ARTIFACT": "container_pull",
    "DELETE_ARTIFACT": "container_delete",
    "SCANNING_COMPLETED": "scan_complete",
    "SCANNING_FAILED": "scan_failed",
    "SCANNING_STOPPED": "scan_complete",
    "REPLICATION": "container_replicate",
    "QUOTA_EXCEED": "quota_exceeded",
    "QUOTA_WARNING": "quota_warning",
    "TAG_RETENTION": "tag_retention",
    "CREATE_PROJECT": "project_create",
    "DELETE_PROJECT": "project_delete",
}


def _parse_when(timestamp: Any) -> Optional[datetime]:
    if isinstance(timestamp, datetime):
        return timestamp
    if not timestamp:
        return None
    try:
        return datetime.fromisoformat(str(timestamp).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def _resource_fields(resources: list) -> Dict[str, Any]:
    """Extract fields from Harbor event_data.resources array."""
    if not resources:
        return {}
    res = resources[0] if isinstance(resources[0], dict) else {}
    fields: Dict[str, Any] = {}
    digest = res.get("digest")
    if digest:
        fields["content_hash"] = digest
    tag = res.get("tag")
    if tag:
        fields["container_tag"] = tag
    scan = res.get("scan_overview")
    if isinstance(scan, dict):
        for _mime, report in scan.items():
            if isinstance(report, dict):
                fields["scan_severity"] = report.get("severity")
                fields["scan_status"] = report.get("scan_status") or report.get("complete_percent")
                summary = report.get("summary")
                if isinstance(summary, dict):
                    fields["vulnerability_count"] = sum(
                        v for v in summary.values() if isinstance(v, int)
                    )
                break
    return fields


def extract_harbor_webhook(
    raw: Mapping[str, Any],
    when: Optional[datetime] = None,
) -> Optional[Dict[str, Any]]:
    """Map a Harbor webhook payload to Signal flat fields."""
    event_type = raw.get("type")
    if not event_type:
        return None
    observable_type = _WEBHOOK_TYPE.get(event_type)
    if not observable_type:
        return None

    operator = raw.get("operator")
    event_data = raw.get("event_data") or {}
    repository = event_data.get("repository") or {}
    resources = event_data.get("resources") or []

    repo_name = repository.get("repo_full_name") or repository.get("name")
    namespace = repository.get("namespace")

    occur_at = raw.get("occur_at")
    if isinstance(occur_at, (int, float)):
        from datetime import timezone
        ts = datetime.fromtimestamp(occur_at, tz=timezone.utc)
    else:
        ts = when

    fields: Dict[str, Any] = {
        "observable_type": observable_type,
        "tool": "harbor",
        "occurred_at": ts,
        "operator_username": operator,
        "repo_name": repo_name,
        "workspace": namespace,
        "session_id": f"harbor-{namespace or 'default'}" if namespace else None,
    }
    fields.update(_resource_fields(resources))
    return fields


def extract_harbor_audit(
    raw: Mapping[str, Any],
    when: Optional[datetime] = None,
) -> Optional[Dict[str, Any]]:
    """Map a Harbor audit-log entry to Signal flat fields.

    Audit log entries have shape:
      id, username, resource, resource_type, operation, op_time, ...
    """
    operation = (raw.get("operation") or "").lower()
    resource_type = (raw.get("resource_type") or "").lower()

    observable_type = _AUDIT_OP_TYPE.get(operation)
    if not observable_type:
        if "scan" in operation:
            observable_type = "scan_complete"
        elif "project" in resource_type and "create" in operation:
            observable_type = "project_create"
        elif "project" in resource_type and "delete" in operation:
            observable_type = "project_delete"
        else:
            observable_type = "container_event"

    resource = raw.get("resource") or ""
    op_time = _parse_when(raw.get("op_time")) or when

    fields: Dict[str, Any] = {
        "observable_type": observable_type,
        "tool": "harbor",
        "occurred_at": op_time,
        "operator_username": raw.get("username"),
        "repo_name": resource if "/" in str(resource) else None,
        "file_path": resource,
    }
    return fields


def extract_harbor(
    raw: Mapping[str, Any],
    when: Optional[datetime] = None,
    **kwargs: Any,
) -> Optional[Dict[str, Any]]:
    """Unified entry point: detects webhook vs audit-log format."""
    if "type" in raw and "event_data" in raw:
        return extract_harbor_webhook(raw, when)
    if "operation" in raw and "resource_type" in raw:
        return extract_harbor_audit(raw, when)
    return None
