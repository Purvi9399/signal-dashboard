"""HTTP client for the signal-ingest Edge Function.

The client sends mapped rows and an upload secret.
The client does not use the service role key.
Optional curated observables ride in the same POST (fleet-safe).
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any, Dict, Mapping, Optional, Sequence


def build_ingest_body(
    *,
    ingest_batch_id: str,
    events: Sequence[Mapping[str, Any]],
    tenant: Mapping[str, Any],
    device: Mapping[str, Any],
    observables: Optional[Sequence[Mapping[str, Any]]] = None,
) -> Dict[str, Any]:
    body: Dict[str, Any] = {
        "ingest_batch_id": ingest_batch_id,
        "tenant": dict(tenant),
        "device": dict(device),
        "events": [dict(row) for row in events],
    }
    if observables:
        body["observables"] = [dict(row) for row in observables]
    return body


def urlopen_json(
    url: str,
    *,
    data: bytes,
    headers: Mapping[str, str],
    timeout: float = 60.0,
) -> bytes:
    req = urllib.request.Request(url, data=data, headers=dict(headers), method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"ingest POST failed: HTTP {exc.code}: {detail}") from exc


class EdgeIngestClient:
    def __init__(self, *, ingest_url: str, upload_secret: str) -> None:
        self.ingest_url = ingest_url.rstrip("/")
        self.upload_secret = upload_secret

    def request_headers(self) -> Dict[str, str]:
        return {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "x-signal-upload-secret": self.upload_secret,
        }

    def post_events(self, body: Mapping[str, Any]) -> Dict[str, Any]:
        raw = urlopen_json(
            self.ingest_url,
            data=json.dumps(body, default=str).encode("utf-8"),
            headers=self.request_headers(),
        )
        if not raw:
            raise RuntimeError("ingest POST returned empty body")
        parsed = json.loads(raw.decode("utf-8"))
        if not isinstance(parsed, dict):
            raise RuntimeError("ingest POST returned non-object JSON")
        return parsed
