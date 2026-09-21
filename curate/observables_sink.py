"""Observables-only Supabase upsert for curated records.

Does not write source_events (avoids source_ref collisions with already
uploaded batches). Uses stdlib HTTP like export.supabase_sink.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional, Sequence

DEFAULT_SUPABASE_URL = "https://cdftgybwybuzcyurlccf.supabase.co"
DEFAULT_BATCH_SIZE = 100


def _first_env(*names: str, default: Optional[str] = None) -> Optional[str]:
    for name in names:
        value = os.environ.get(name)
        if value:
            return value
    return default


def service_role_key() -> str:
    key = _first_env("SUPABASE_SERVICE_ROLE_KEY", "SUPABASE_KEY")
    if not key:
        raise EnvironmentError(
            "SUPABASE_SERVICE_ROLE_KEY or SUPABASE_KEY must be set "
            "(operator curate only; never bake into the exe)."
        )
    return key


def supabase_url() -> str:
    value = _first_env("SUPABASE_URL", default=DEFAULT_SUPABASE_URL) or DEFAULT_SUPABASE_URL
    return value.rstrip("/")


def observables_table() -> str:
    return _first_env("SUPABASE_TABLE", default="observables") or "observables"


def _headers(key: str, *, prefer: Optional[str] = None) -> Dict[str, str]:
    headers = {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    if prefer:
        headers["Prefer"] = prefer
    return headers


def _request(
    method: str,
    path: str,
    *,
    key: str,
    body: Any = None,
    prefer: Optional[str] = None,
    query: Optional[Dict[str, str]] = None,
    extra_headers: Optional[Dict[str, str]] = None,
) -> Any:
    qs = f"?{urllib.parse.urlencode(query)}" if query else ""
    url = f"{supabase_url()}/rest/v1/{path.lstrip('/')}{qs}"
    data = None if body is None else json.dumps(body).encode("utf-8")
    headers = _headers(key, prefer=prefer)
    if extra_headers:
        headers.update(extra_headers)
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
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


def _chunk(rows: Sequence[Dict[str, Any]], size: int) -> List[List[Dict[str, Any]]]:
    n = max(1, size)
    return [list(rows[i : i + n]) for i in range(0, len(rows), n)]


def upsert_observables(
    records: Sequence[Dict[str, Any]],
    *,
    key: Optional[str] = None,
    batch_size: int = DEFAULT_BATCH_SIZE,
) -> int:
    """Upsert curated records into public.observables only. Returns count sent."""
    if not records:
        return 0
    from export.observable_rows import align_observable_keys

    auth = key or service_role_key()
    table = observables_table()
    sent = 0
    aligned = align_observable_keys(list(records))
    for batch in _chunk(aligned, batch_size):
        _request(
            "POST",
            table,
            key=auth,
            body=batch,
            prefer="resolution=merge-duplicates,return=minimal",
            query={"on_conflict": "dedup_key"},
        )
        sent += len(batch)
    return sent


def _fetch_page(
    auth: str, ingest_batch_id: str, start: int, page_size: int
) -> List[Dict[str, Any]]:
    end = start + page_size - 1
    batch = _request(
        "GET",
        "source_events",
        key=auth,
        query={
            "ingest_batch_id": f"eq.{ingest_batch_id}",
            "select": "*",
            "order": "received_at.asc",
        },
        extra_headers={"Range": f"{start}-{end}"},
    )
    if batch is None:
        return []
    if not isinstance(batch, list):
        raise RuntimeError("unexpected source_events response shape")
    return batch


def fetch_source_events_by_batch(
    ingest_batch_id: str,
    *,
    key: Optional[str] = None,
    page_size: int = 1000,
) -> List[Dict[str, Any]]:
    """Load source_events rows for one ingest_batch_id (operator only)."""
    auth = key or service_role_key()
    rows: List[Dict[str, Any]] = []
    start = 0
    size = max(1, page_size)
    while True:
        batch = _fetch_page(auth, ingest_batch_id, start, size)
        if not batch:
            break
        rows.extend(batch)
        if len(batch) < size:
            break
        start += size
    return rows
