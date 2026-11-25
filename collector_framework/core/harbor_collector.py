"""Harbor container-registry collector.

Captures events from a Harbor v2 instance via its audit-log API or
webhook receiver.  Two modes:

  poll   – periodically GET /api/v2.0/audit-logs (default, no setup)
  webhook – lightweight HTTP server that receives Harbor webhook POSTs

Both produce RawEvent envelopes with collector_id "harbor.webhook"
and agent "harbor".
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Any, Dict, Iterator, Optional

from .interfaces import Collector, RawEvent

_DEFAULT_BASE = "http://harbor.local:8880"
_POLL_INTERVAL_S = 10
_PAGE_SIZE = 50


class HarborCollector(Collector):
    """Collector that reads Harbor audit-log entries as RawEvents."""

    def __init__(
        self,
        base_url: Optional[str] = None,
        username: Optional[str] = None,
        password: Optional[str] = None,
        poll_interval_s: float = _POLL_INTERVAL_S,
    ) -> None:
        self._base = (base_url or os.environ.get("HARBOR_URL", _DEFAULT_BASE)).rstrip("/")
        self._user = username or os.environ.get("HARBOR_USERNAME", "admin")
        self._pass = password or os.environ.get("HARBOR_PASSWORD", "Harbor12345")
        self._poll_s = poll_interval_s
        self._last_id: int = 0
        self._active = False

    @property
    def collector_id(self) -> str:
        return "harbor.webhook"

    def detect(self) -> bool:
        try:
            req = urllib.request.Request(
                f"{self._base}/api/v2.0/health",
                headers={"Accept": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                body = json.load(resp)
                return body.get("status") == "healthy"
        except Exception:
            return False

    def activate(self) -> None:
        self._last_id = self._fetch_latest_id()
        self._active = True

    def capture(self) -> Iterator[RawEvent]:
        while self._active:
            for entry in self._poll_new_entries():
                yield self._to_raw_event(entry)
            time.sleep(self._poll_s)

    def deactivate(self) -> None:
        self._active = False

    def capture_once(self) -> list[RawEvent]:
        """Non-blocking: fetch new entries since last poll and return them."""
        return [self._to_raw_event(e) for e in self._poll_new_entries()]

    def _auth_header(self) -> str:
        import base64
        creds = base64.b64encode(f"{self._user}:{self._pass}".encode()).decode()
        return f"Basic {creds}"

    def _api_get(self, path: str, params: str = "") -> Any:
        url = f"{self._base}{path}"
        if params:
            url = f"{url}?{params}"
        req = urllib.request.Request(
            url,
            headers={
                "Accept": "application/json",
                "Authorization": self._auth_header(),
            },
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.load(resp)

    def _fetch_latest_id(self) -> int:
        try:
            entries = self._api_get(
                "/api/v2.0/audit-logs",
                f"page_size=1&sort=-id",
            )
            if entries and isinstance(entries, list):
                return entries[0].get("id", 0)
        except Exception:
            pass
        return 0

    def _poll_new_entries(self) -> list[dict]:
        try:
            entries = self._api_get(
                "/api/v2.0/audit-logs",
                f"page_size={_PAGE_SIZE}&sort=id&q=id%3E{self._last_id}",
            )
            if not isinstance(entries, list):
                return []
            if entries:
                self._last_id = max(e.get("id", 0) for e in entries)
            return entries
        except Exception:
            return []

    def _to_raw_event(self, entry: dict) -> RawEvent:
        ts = entry.get("op_time") or datetime.now(timezone.utc).isoformat()
        return RawEvent(
            timestamp=ts,
            collector_id=self.collector_id,
            agent="harbor",
            raw_payload=entry,
        )


def from_webhook_payload(payload: Dict[str, Any]) -> RawEvent:
    """Convert a Harbor webhook POST body into a RawEvent.

    Harbor webhooks send JSON with:
      type: "PUSH_ARTIFACT" | "PULL_ARTIFACT" | "SCANNING_COMPLETED" | ...
      occur_at: unix timestamp
      operator: username
      event_data: { repository, resources, ... }
    """
    ts_unix = payload.get("occur_at")
    if ts_unix:
        ts = datetime.fromtimestamp(ts_unix, tz=timezone.utc).isoformat()
    else:
        ts = RawEvent.now_iso()
    return RawEvent(
        timestamp=ts,
        collector_id="harbor.webhook",
        agent="harbor",
        raw_payload=payload,
    )
