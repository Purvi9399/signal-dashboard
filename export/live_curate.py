"""On-device curate of RawEvents for live/upload (Purvi stack).

Produces observables records stamped for the current ingest batch.
Does not call Supabase; callers upload via edge or direct sink.
"""

from __future__ import annotations

from typing import Any, Dict, List, Mapping, Sequence

from curate.adapter_source_events import iter_adapted_raw_events
from curate.signal_curate import curate


def _load_tuples(events: Sequence[Mapping[str, Any]]) -> list:
    loaded = []
    for event in events:
        loaded.extend(iter_adapted_raw_events(event))
    return loaded


def stamp_observables(
    records: Sequence[Dict[str, Any]],
    *,
    tenant_id: str,
    device_id: str,
    ingest_batch_id: str,
) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for row in records:
        stamped = dict(row)
        stamped.setdefault("tenant_id", tenant_id)
        stamped["device_id"] = device_id
        stamped["ingest_batch_id"] = ingest_batch_id
        out.append(stamped)
    return out


def curate_raw_event_dicts(
    events: Sequence[Mapping[str, Any]],
    *,
    tenant_id: str,
    device_id: str,
    ingest_batch_id: str,
) -> List[Dict[str, Any]]:
    """Adapt + curate RawEvent dicts → stamped observables rows."""
    records, _conflicts, _field_status = curate(_load_tuples(events))
    return stamp_observables(
        records,
        tenant_id=tenant_id,
        device_id=device_id,
        ingest_batch_id=ingest_batch_id,
    )
