"""Minimal Microsoft Graph HTTP client (stdlib only, injectable for tests)."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable, Dict, Optional

TokenFetcher = Callable[[], str]
JsonDict = Dict[str, Any]


class GraphError(RuntimeError):
    """Graph or Azure AD request failed."""


def fetch_client_credentials_token(
    *,
    tenant_id: str,
    client_id: str,
    client_secret: str,
    opener: Callable[..., Any] = urllib.request.urlopen,
) -> str:
    """OAuth2 client-credentials token for Graph."""
    url = f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token"
    body = urllib.parse.urlencode(
        {
            "client_id": client_id,
            "client_secret": client_secret,
            "scope": "https://graph.microsoft.com/.default",
            "grant_type": "client_credentials",
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        url, data=body, method="POST", headers={"Content-Type": "application/x-www-form-urlencoded"}
    )
    try:
        with opener(req) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise GraphError(f"token request failed: {exc.read().decode('utf-8', errors='replace')}") from exc
    token = data.get("access_token")
    if not token:
        raise GraphError("token response missing access_token")
    return str(token)


class GraphClient:
    """Thin Graph wrapper. Keep methods linear (CC ≤ 5)."""

    def __init__(self, token: str, *, opener: Callable[..., Any] = urllib.request.urlopen) -> None:
        self._token = token
        self._opener = opener
        self.base = "https://graph.microsoft.com/beta"

    def request(
        self,
        method: str,
        path: str,
        body: Optional[JsonDict] = None,
        *,
        raw_url: bool = False,
    ) -> Any:
        url = path if raw_url else f"{self.base}{path}"
        data = None if body is None else json.dumps(body).encode("utf-8")
        headers = {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
        }
        req = urllib.request.Request(url, data=data, method=method, headers=headers)
        try:
            with self._opener(req) as resp:
                raw = resp.read()
                if not raw:
                    return None
                return json.loads(raw.decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise GraphError(f"{method} {url} failed ({exc.code}): {detail}") from exc

    def get(self, path: str) -> Any:
        return self.request("GET", path)

    def post(self, path: str, body: Optional[JsonDict] = None) -> Any:
        return self.request("POST", path, body or {})

    def patch(self, path: str, body: JsonDict) -> Any:
        return self.request("PATCH", path, body)

    def _read_state(self, path: str, field: str) -> tuple[Any, Any]:
        last = self.get(path)
        state = last.get(field) if isinstance(last, dict) else None
        return last, state

    def poll_until(
        self,
        path: str,
        *,
        field: str,
        wanted: str,
        timeout_sec: float = 300.0,
        sleep_sec: float = 2.0,
    ) -> JsonDict:
        """Poll GET path until data[field] == wanted or timeout."""
        deadline = time.time() + timeout_sec
        last: Any = None
        while time.time() < deadline:
            last, state = self._read_state(path, field)
            if state == wanted:
                return last
            if isinstance(state, str) and state.endswith("Failed"):
                raise GraphError(f"poll {path} failed: {state}")
            time.sleep(sleep_sec)
        raise GraphError(f"poll timeout on {path}; last={last!r}")
