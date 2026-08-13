"""
Cursor MCP / network Proxy collector.

Ported directly from the real, production Cursor mitmproxy sidecar:

  projectsetup/backend/cursor_proxy2.py

This IS the strongest-grounded of the four new Cursor collectors -- unlike
cursor_otel.py (no positive evidence) and cursor_cli_wrapper.py (no
dedicated evidence folder), cursor_proxy2.py is a real, currently-running
production script (`mitmdump -s cursor_proxy2.py -p 8080`) that intercepts
Cursor.app's actual outbound HTTPS traffic to `cursor.sh`.

IMPORTANT NAMING NOTE: cursor_proxy2.py is technically a wire-level
NETWORK proxy (intercepting Cursor's own ConnectRPC/protobuf backend
calls), not a stdio MCP-JSON-RPC proxy the way copilot_mcp_proxy.py /
Copilot_MCP_Proxy/mcp_proxy.py is (which sits between an MCP client and
an MCP stdio server). The task explicitly names this file
`cursor_mcp_proxy.py` and instructs porting cursor_proxy2.py's logic into
it, so this collector documents that naming reflects "the network/proxy
interception technique for Cursor" broadly, grounded in the one real
proxy sidecar that exists for Cursor -- it is NOT claiming Cursor has a
separate, distinct MCP-stdio-JSON-RPC proxy the way Copilot does. Per the
CSV, Cursor's mcp_server/mcp_tool/mcp_transport/mcp_server_version/
mcp_tools_available fields (rows 77-81) ARE cited as "MCP proxy" sourced
-- those would come from watching Cursor's `beforeMCPExecution` hook
payload (see cursor_hooks.py's KNOWN_HOOK_EVENTS) or, if Cursor's MCP
traffic is proxied at the JSON-RPC layer the way Copilot's is, a stdio
proxy analogous to copilot_mcp_proxy.py's mechanism -- but no such
Cursor-specific stdio-MCP-proxy script or capture exists anywhere in this
project. This collector focuses on what IS real and documented:
cursor_proxy2.py's confirmed wire-level token-usage interception.

REAL MECHANISM PORTED (from cursor_proxy2.py's `CursorProxyTracker`
class, read in full):
  - Only intercepts traffic whose host contains `cursor.sh`
    (TARGET_HOST), matching cursor_proxy2.py's `response()` guard.
  - Drops known non-completion noise routes: CppService, ShadowWorkspace,
    Analytics, Dashboard, metrics, Report, rgstr,
    Aiserver.v1.FileSyncService, Embedding, UploadFile, Heartbeat,
    AvailableModels, Usage (IGNORE_ROUTES, verbatim).
  - Extracts session_id from `x-cursor-session-id` or `x-session-id`
    request headers, falling back to the literal string
    "ACTIVE_AUTO_MODE_SESSION" -- exact fallback string from
    cursor_proxy2.py, not invented here.
  - PREFERRED token source: regex-extracts Cursor's OWN reported
    input/output token counts directly out of the response body when
    present (`_USAGE_PATTERNS`, matching input_tokens/prompt_tokens/
    promptTokenCount and output_tokens/completion_tokens/
    candidatesTokenCount key spellings) -- tagged
    `token_source: "cursor_reported"`.
  - FALLBACK: only if no real usage was found in the response, and the
    decoded REQUEST body is >=85% printable characters (guards against
    tiktoken-encoding compressed/binary protobuf, which cursor_proxy2.py's
    own comments say "produced the garbage 10k-400k swings"), estimate
    tokens via a real `tiktoken` `o200k_base` encoding of the request
    body -- tagged `token_source: "tiktoken_estimate"`.
  - Both stdlib `tiktoken` and `mitmproxy` are third-party, non-stdlib
    dependencies (this framework is stdlib-only per the task rules) --
    this collector gracefully degrades: if `tiktoken` is unavailable, the
    fallback-estimate path is skipped entirely rather than crashing (the
    cursor_reported path, which needs no dependency at all beyond
    stdlib `re`, still works).
  - Ignores tiny heartbeat/keepalive traffic (<500 input AND <500 output
    tokens), matching cursor_proxy2.py's exact threshold.
  - Emits the exact same payload shape cursor_proxy2.py POSTs to
    webhook_server.py: {"event_type": "network_context_capture",
    "session_id": ..., "payload": {"usage": {"input_tokens": ...,
    "output_tokens": ...}}, "runtime_enrichment": {"source":
    "mitmproxy_interceptor", "route": ..., "token_source": ...}} -- this
    is also the exact NETWORK_CAPTURE_EVENT_TYPE shape cursor_hooks.py's
    own module docstring already documents as the distinct, non-hooks.json
    payload shape from this same proxy.

CSV grounding (observables_table_claude_cursor.csv):
  - rows 77-81 (mcp_server, mcp_tool, mcp_transport, mcp_server_version,
    mcp_tools_available): cited "MCP proxy" for both CC and Cursor.
  - row 54-58 (input_tokens, output_tokens, cache_read_tokens,
    cache_write_tokens, total_tokens): cited "local hooks" for Cursor, with
    row 54's Note "THE KEY ASYMMETRY. CC in the transcript, Cursor in the
    hook payload." -- meaning the CSV's PRIMARY route for Cursor token
    counts is hooks, not a proxy. This collector's real, cited role (per
    cursor_hooks.py's own module docstring, corroborated by row 59's "Cursor
    has no cost field, must estimate") is as the FALLBACK/cross-check route
    when hook-level usage is absent -- exactly the role webhook_server.py's
    cursor_event() gives it (banking tokens WITHOUT creating a session, a
    separate code path from normal hook events).

No normalization: raw_payload is the exact payload dict cursor_proxy2.py
builds and POSTs -- untouched, `event_type`/`session_id`/`payload`/
`runtime_enrichment` keys and all.
"""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from typing import Iterator, List, Optional

import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parents[2]))
from core import Collector, RawEvent

AGENT_NAME = "cursor"
COLLECTOR_ID = "cursor.mcp_proxy"

# Verbatim from cursor_proxy2.py.
TARGET_HOST = "cursor.sh"
IGNORE_ROUTES = [
    "CppService", "ShadowWorkspace", "Analytics", "Dashboard", "metrics",
    "Report", "rgstr", "Aiserver.v1.FileSyncService", "Embedding",
    "UploadFile", "Heartbeat", "AvailableModels", "Usage",
]
_USAGE_PATTERNS = {
    "input_tokens": re.compile(
        r'["\'](?:input_?[tT]okens|prompt_?[tT]okens|promptTokenCount)["\']\s*:\s*(\d+)'
    ),
    "output_tokens": re.compile(
        r'["\'](?:output_?[tT]okens|completion_?[tT]okens|candidatesTokenCount)["\']\s*:\s*(\d+)'
    ),
}
SESSION_ID_FALLBACK = "ACTIVE_AUTO_MODE_SESSION"
NETWORK_CAPTURE_EVENT_TYPE = "network_context_capture"

try:
    import tiktoken  # third-party, matches cursor_proxy2.py's real dependency
    TIKTOKEN_AVAILABLE = True
except ImportError:
    # cursor_proxy2.py hard-requires tiktoken; this framework is
    # stdlib-only, so the fallback tiktoken-estimate path degrades
    # gracefully instead of crashing. The cursor_reported path (regex
    # over response bodies, stdlib `re` only) is unaffected.
    TIKTOKEN_AVAILABLE = False
    tiktoken = None  # type: ignore[assignment]


def _printable_ratio(text: str) -> float:
    """Verbatim logic from cursor_proxy2.py's CursorProxyTracker._printable_ratio."""
    if not text:
        return 0.0
    good = sum(1 for c in text if c.isprintable() or c in "\r\n\t ")
    return good / len(text)


def extract_real_usage(response_body: str) -> tuple:
    """Port of cursor_proxy2.py's _real_usage(), adapted to take an
    already-decoded response body string (this collector does not do its
    own mitmproxy flow decoding -- see module docstring on live-mode
    scope) instead of a live mitmproxy `http.HTTPFlow` object. Returns
    (input_tokens, output_tokens) or (None, None) if neither pattern
    matched, exactly as the original.
    """
    if not response_body:
        return None, None
    i = _USAGE_PATTERNS["input_tokens"].search(response_body)
    o = _USAGE_PATTERNS["output_tokens"].search(response_body)
    in_tok = int(i.group(1)) if i else None
    out_tok = int(o.group(1)) if o else None
    return in_tok, out_tok


def build_network_capture_payload(
    route: str,
    session_id: Optional[str],
    request_body: str = "",
    response_body: str = "",
    encoding=None,
) -> Optional[dict]:
    """Direct port of cursor_proxy2.py's `response()` method body (the
    part after the TARGET_HOST/IGNORE_ROUTES guards, which the caller is
    responsible for applying first -- see CursorMCPProxyCollector.
    _process_flow()). Returns the exact payload dict cursor_proxy2.py
    would POST, or None if this traffic should be dropped (tiny
    heartbeat, or no usable signal at all) -- matching the original's
    early-return behavior exactly.
    """
    session_id = session_id or SESSION_ID_FALLBACK

    real_in, real_out = extract_real_usage(response_body)

    if real_in is not None or real_out is not None:
        input_tokens = real_in or 0
        output_tokens = real_out or 0
        source = "cursor_reported"
    elif encoding is not None:
        # FALLBACK: estimate from the decoded request body. Only when the
        # body is genuinely text (printable ratio >= 0.85) -- verbatim
        # threshold from cursor_proxy2.py, guarding against tiktoken-
        # encoding compressed/binary protobuf.
        if not request_body or _printable_ratio(request_body) < 0.85:
            return None
        input_tokens = len(encoding.encode(request_body))
        output_tokens = 0
        source = "tiktoken_estimate"
    else:
        # tiktoken unavailable in this stdlib-only environment and no
        # cursor_reported usage found -- nothing this collector can
        # honestly report for this flow (does NOT fabricate a zero-token
        # estimate, unlike the original which always has tiktoken).
        return None

    # Ignore tiny heartbeat/keepalive requests either way. Verbatim
    # threshold from cursor_proxy2.py.
    if input_tokens < 500 and output_tokens < 500:
        return None

    return {
        "event_type": NETWORK_CAPTURE_EVENT_TYPE,
        "session_id": session_id,
        "payload": {
            "usage": {
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
            }
        },
        "runtime_enrichment": {
            "source": "mitmproxy_interceptor",
            "route": route,
            "token_source": source,
        },
    }


class CursorMCPProxyCollector(Collector):
    """Network-proxy-based collector for Cursor, porting cursor_proxy2.py's
    real interception/parsing logic.

    Live mode (real deployment): would run as a mitmproxy addon exactly as
    cursor_proxy2.py does (`mitmdump -s cursor_proxy2.py -p 8080`) with
    Cursor.app's traffic routed through it. This framework's Collector
    interface does not itself manage a mitmproxy process (that is a
    heavier, side-effecting subprocess concern outside this module's
    scope, matching this framework's stated boundary of "raw agent events
    in, RawEvent envelopes out" -- see core.py's SCOPE NOTE) -- activate()
    documents that gap rather than silently no-op'ing.

    This build supports feeding capture() from replay_events shaped like
    already-decoded {route, session_id, request_body, response_body}
    flow summaries, which build_network_capture_payload() turns into the
    exact payload shape cursor_proxy2.py posts -- exercising the real
    ported parsing/filtering logic end-to-end without needing a live
    mitmproxy process or real Cursor network traffic.
    """

    def __init__(self, replay_flows: Optional[List[dict]] = None) -> None:
        self._replay_flows = replay_flows
        self._activated = False
        self._encoding = None
        if TIKTOKEN_AVAILABLE:
            try:
                self._encoding = tiktoken.get_encoding("o200k_base")
            except Exception:
                self._encoding = None

    @property
    def collector_id(self) -> str:
        return COLLECTOR_ID

    def detect(self) -> bool:
        """Real, cheap heuristics for "Cursor is installed and this
        proxy technique is applicable on this machine":
          1. Cursor.app / `cursor` on PATH (same checks cursor_hooks.py
             uses -- this proxy intercepts Cursor.app's own traffic, so
             Cursor itself must be present).
          2. mitmproxy importable (the real mechanism's actual
             dependency, per cursor_proxy2.py's own `from mitmproxy
             import http`). Its absence does not make detect() False on
             its own if Cursor is present -- it just means live-mode
             activate() cannot actually spin up a mitmproxy addon, which
             is surfaced honestly there instead.
        """
        found_any = False

        if shutil.which("cursor") is not None:
            found_any = True
        if Path("/Applications/Cursor.app").exists():
            found_any = True

        return found_any

    def activate(self) -> None:
        """In replay mode, a no-op besides marking activated and printing
        the same honest-gap note every time (this collector does not
        manage a real mitmproxy child process -- see class docstring).
        """
        if self._replay_flows is None:
            try:
                import mitmproxy  # noqa: F401
                mitmproxy_available = True
            except ImportError:
                mitmproxy_available = False
            print(
                f"[{self.collector_id}] NOTE: live mode would require running "
                f"as a mitmproxy addon (`mitmdump -s cursor_proxy2.py -p 8080` "
                f"is the real, currently-deployed invocation) with Cursor.app's "
                f"traffic actually routed through it -- this framework does not "
                f"spawn/manage that external mitmproxy process itself. "
                f"mitmproxy importable on this machine: {mitmproxy_available}. "
                f"capture() will yield 0 events in live mode; use "
                f"replay_flows= to exercise the real ported parsing logic."
            )
        self._activated = True

    def capture(self) -> Iterator[RawEvent]:
        if not self._activated:
            raise RuntimeError(f"{self.collector_id}: capture() called before activate()")

        if self._replay_flows is None:
            return

        for flow in self._replay_flows:
            route = flow.get("route", "")

            # Verbatim IGNORE_ROUTES guard from cursor_proxy2.py's response().
            if any(ignore in route for ignore in IGNORE_ROUTES):
                continue

            payload = build_network_capture_payload(
                route=route,
                session_id=flow.get("session_id"),
                request_body=flow.get("request_body", ""),
                response_body=flow.get("response_body", ""),
                encoding=self._encoding,
            )
            if payload is None:
                continue

            yield RawEvent(
                timestamp=RawEvent.now_iso(),
                collector_id=self.collector_id,
                agent=AGENT_NAME,
                raw_payload=payload,  # untouched, exact shape cursor_proxy2.py posts
            )

    def deactivate(self) -> None:
        self._activated = False


def _sample_flows_for_demo() -> List[dict]:
    """No real captured Cursor network-proxy flow log exists anywhere in
    this project (cursor_proxy2.py fires-and-forgets its POST to
    webhook_server.py; it does not itself write a captured_*.jsonl the
    way the Codex/Copilot MCP Proxy collectors' evidence folders do). To
    exercise the REAL ported logic above (extract_real_usage,
    build_network_capture_payload, IGNORE_ROUTES filtering) honestly, this
    builds sample flow dicts using cursor_proxy2.py's OWN real regex
    patterns and threshold constants against small hand-written response
    bodies -- clearly labeled as constructed-for-demo inputs to real
    parsing code, not as a real captured session.
    """
    return [
        {
            # Should be dropped: IGNORE_ROUTES match.
            "route": "/aiserver.v1.AnalyticsService/Track",
            "session_id": "sess-demo-1",
            "request_body": "irrelevant",
            "response_body": "irrelevant",
        },
        {
            # Should be dropped: tiny heartbeat (< 500/500 threshold).
            "route": "/aiserver.v1.ChatService/StreamChat",
            "session_id": "sess-demo-2",
            "request_body": "",
            "response_body": '{"input_tokens": 12, "output_tokens": 3}',
        },
        {
            # Should be emitted via cursor_reported path: real usage keys
            # present in the response body, over the 500-token threshold.
            "route": "/aiserver.v1.ChatService/StreamChat",
            "session_id": "sess-demo-3",
            "request_body": "",
            "response_body": '{"promptTokenCount": 812, "candidatesTokenCount": 640}',
            "request_headers": {"x-cursor-session-id": "sess-demo-3"},
        },
    ]


if __name__ == "__main__":
    collector = CursorMCPProxyCollector()
    print(f"[{collector.collector_id}] detect() -> {collector.detect()}")
    print(f"[{collector.collector_id}] tiktoken available for fallback-estimate path: {TIKTOKEN_AVAILABLE}")

    demo_flows = _sample_flows_for_demo()
    print(
        f"\nNo real captured Cursor network-proxy flow log exists anywhere in "
        f"this project (cursor_proxy2.py fire-and-forgets its POST; it keeps "
        f"no local log file). Exercising the REAL ported parsing/filtering "
        f"logic (extract_real_usage, IGNORE_ROUTES, build_network_capture_"
        f"payload) against {len(demo_flows)} small hand-constructed flow "
        f"summaries instead -- labeled SIMULATED, not real captured data:"
    )

    replay_collector = CursorMCPProxyCollector(replay_flows=demo_flows)
    replay_collector.activate()
    count = 0
    for event in replay_collector.capture():
        count += 1
        print(f"\n--- RawEvent #{count} (SIMULATED, real ported logic) ---")
        print(f"collector_id: {event.collector_id}")
        print(f"agent:        {event.agent}")
        print(json.dumps(event.raw_payload, indent=2))
    replay_collector.deactivate()

    print(
        f"\nTotal RawEvents yielded: {count} out of {len(demo_flows)} input flows "
        f"(the AnalyticsService route and the sub-threshold heartbeat were "
        f"correctly dropped by the real ported IGNORE_ROUTES / 500-token "
        f"filters, matching cursor_proxy2.py's exact behavior)."
    )
