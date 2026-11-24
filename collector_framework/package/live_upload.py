"""Flush captured live events to Supabase as each session completes."""

from __future__ import annotations

from typing import Any, Dict, List, Mapping, Sequence

from export.upload_events import raw_events_to_dicts, upload_event_dicts
from export.validate_ingest import MAX_EVENTS


def flush_session(events: Sequence[Any], *, origin: str = "live-capture") -> List[Dict[str, Any]]:
    dicts = raw_events_to_dicts(events)
    if not dicts:
        return []
    results: List[Dict[str, Any]] = []
    for start in range(0, len(dicts), MAX_EVENTS):
        chunk = dicts[start : start + MAX_EVENTS]
        results.append(upload_event_dicts(chunk, origin=origin))
    return results


def try_flush_session(
    events: Sequence[Any],
    batches: List[Dict[str, Any]],
    *,
    origin: str = "live-capture",
) -> None:
    if not events:
        return
    try:
        batches.extend(flush_session(events, origin=origin))
    except Exception as exc:
        batches.append({"error": str(exc), "events_read": len(events), "inserted": 0})


def summarize_uploads(batches: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    rows = list(batches)
    errors = [row.get("error") for row in rows if row.get("error")]
    ids = [row.get("ingest_batch_id") for row in rows if row.get("ingest_batch_id")]
    mode = None
    for row in reversed(rows):
        if row.get("mode"):
            mode = row.get("mode")
            break
    return {
        "mode": mode,
        "sessions_uploaded": len(rows),
        "inserted": sum(int(row.get("inserted") or 0) for row in rows),
        "observables_curated": sum(int(row.get("observables_curated") or 0) for row in rows),
        "observables_upserted": sum(int(row.get("observables_upserted") or 0) for row in rows),
        "events_read": sum(int(row.get("events_read") or 0) for row in rows),
        "ingest_batch_ids": ids,
        "ingest_batch_id": ids[-1] if ids else None,
        "errors": errors,
    }
