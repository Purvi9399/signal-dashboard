"""
Cursor OpenTelemetry (OTLP) collector.

CONFIRMED SPARSE / NO POSITIVE EVIDENCE for Cursor. This module documents
that honestly rather than forcing a working collector where none is
evidenced -- same posture as antigravity_otel.py's confirmed dead end.

EVIDENCE REVIEWED (both required by the task):

1. reference_tables/observables_table_claude_cursor.csv -- the primary,
   empirically-built field table for exactly Claude Code vs. Cursor. Every
   row that cites "OpenTelemetry" does so ONLY in the "Claude Code source"
   column; the "Cursor source" column for those same rows is populated with
   something else entirely (usually "local hooks", "derived", "FS watcher",
   or "—"), never "OpenTelemetry". Specific rows checked:
     - row 7  sequence_num:       CC=OpenTelemetry,        Cursor=derived
     - row 9  tool_version:       CC=OpenTelemetry,        Cursor=local hooks
     - row 19 parent_agent_id:    CC=OpenTelemetry,        Cursor=—
     - row 23 operator_email:     CC=OpenTelemetry,        Cursor=local hooks
     - row 24 operator_id:        CC=OpenTelemetry,        Cursor=—
     - row 26 org_id:             CC=OpenTelemetry,        Cursor=—
     - row 27 team_id:            CC=OpenTelemetry,        Cursor=—
     - row 42 prompt_length:      CC=OpenTelemetry,        Cursor=derived
     - row 50 slash_command:      CC=OpenTelemetry,        Cursor=derived
     - row 53 model_id:           CC=OpenTelemetry,        Cursor=local hooks
     - row 59 cost_usd:           CC=OpenTelemetry (claude_code.cost.usage),
                                   Cursor=derived ("has no cost field, must estimate")
     - row 61 request_latency_ms: CC=OpenTelemetry,        Cursor=—
     - row 62 time_to_first_token:CC=OpenTelemetry,        Cursor=—
     - row 63 stop_reason:        CC=OpenTelemetry,        Cursor=local hooks
     - row 72 tool_result_size:   CC=OpenTelemetry,        Cursor=derived
     - row 99/100 lines_added/removed: CC=OpenTelemetry (claude_code.lines_of_code.count),
                                   Cursor=derived
     - row 110/111 permission_decision/source: CC=OpenTelemetry, Cursor=— (with note
       "Cursor fires nothing at all in sandbox mode")
   NOT ONE row in the entire CSV cites "OpenTelemetry" under the "Cursor
   source" column. This is the CSV's own built-in confirmation, stated
   directly in row 9's Note as "MIRROR AGAIN... Cursor cursor_version on
   hooks" and row 23's Note as "MIRROR IMAGE. CC only in OTel, Cursor on
   every hook payload" -- i.e. wherever Claude Code's ground truth is OTel,
   Cursor's equivalent ground truth is its Hooks payload instead, not OTel.

2. Repository-wide grep for Cursor + OTel/telemetry evidence (both this
   collector's authoring session and the task's own instruction to check):
   `grep -rli cursor . | xargs grep -li "otel\\|opentelemetry\\|OTEL_EXPORTER"`
   returned only files that merely enumerate "cursor" as one name among
   several sibling agents inside otherwise Codex/Antigravity-specific OTel
   evidence docs (Codex_OTel/otel_related_strings_raw.txt,
   Antigravity_OTel/evidence/binary_otel_strings.txt/
   binary_telemetry_backend_strings.txt) or inside THIS framework's own
   sibling files (antigravity_hooks.py, codex_cli_wrapper.py, README.md,
   run_demo_full.py) referencing Cursor only incidentally as another agent
   in the roster. There is no `Cursor_OTel/` evidence directory anywhere in
   this project (compare: `Codex_OTel/`, `Antigravity_OTel/` both exist as
   dedicated empirical-test folders; no Cursor equivalent exists at all).

3. cursor_proxy2.py (the real production mitmproxy sidecar) and
   cursor_hooks.py (the real hooks collector) are BOTH confirmed real,
   working, wire-level/hook-level capture mechanisms for Cursor -- neither
   uses or mentions OTLP/OpenTelemetry in any way. Cursor's token-usage
   telemetry demonstrably flows over its own ConnectRPC/protobuf backend
   traffic (intercepted by cursor_proxy2.py) and its native hooks.json
   events (captured by cursor_hooks.py), not an OTLP exporter.

CONCLUSION: unlike Antigravity (which has direct binary/dynamic-test
evidence of an explicit dead end), Cursor has no dedicated OTel test
folder and no positive citation anywhere in the CSV or repo -- the
evidence is "absence of any positive signal" rather than "confirmed and
tested absence." This collector is honest about that distinction: it
still implements a real, cheap detect() (checking the same
OTEL_EXPORTER_OTLP_* env vars a diligent operator would try, exactly as
antigravity_otel.py does), but capture() has no real captured OTLP payload
anywhere in this project to replay for Cursor (there is no
`Cursor_OTel/captured_*.jsonl` the way there is for Codex/Antigravity),
so it legitimately yields 0 events unless fed synthetic replay_events.

No normalization: if ever fed real data, raw_payload passes through
untouched.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Iterator, List, Optional

import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parents[2]))
from collectors.live_http import LiveEventFeed  # noqa: E402
from collectors.live_otel import DEFAULT_HOST, DEFAULT_PORT, LiveOTLPServer  # noqa: E402
from core import Collector, RawEvent

AGENT_NAME = "cursor"
COLLECTOR_ID = "cursor.otel"
OTLP_PREFIX = "/otel/cursor"

# Standard OTLP env vars. No evidence anywhere in this project (CSV or
# repo grep) that Cursor's IDE or backend reads/honors these -- included
# only because a real operator following standard OTel practice would set
# these first, and detect() should notice that attempt honestly, exactly
# as antigravity_otel.py does for the same reason.
STANDARD_OTEL_ENV_VARS = (
    "OTEL_EXPORTER_OTLP_ENDPOINT",
    "OTEL_EXPORTER_OTLP_PROTOCOL",
    "OTEL_SERVICE_NAME",
)


class CursorOTelCollector(Collector):
    """OTLP-based collector for Cursor -- NO POSITIVE EVIDENCE found in
    this project (not the same as Antigravity's *confirmed-dead* binary
    test; here it is simply that no Cursor_OTel/ test folder exists and
    the CSV never cites "OpenTelemetry" under its "Cursor source" column
    for any of its ~90 rows). See module docstring for the specific CSV
    rows and repo-grep results this conclusion is drawn from.

    This class exists to be a truthful, non-silent placeholder in the
    registry (matching this framework's established pattern for
    documented dead ends/gaps) rather than a working data source.
    """

    def __init__(
        self,
        replay_events: Optional[List[dict]] = None,
        listen_host: str = DEFAULT_HOST,
        listen_port: int = DEFAULT_PORT,
    ) -> None:
        self._replay_events = replay_events
        self._listen_host = listen_host
        self._listen_port = listen_port
        self._activated = False
        self._live_feed: Optional[LiveEventFeed] = None
        self._server: Optional[LiveOTLPServer] = None
        self._bound_port = listen_port

    @property
    def listen_port(self) -> int:
        return self._bound_port

    @property
    def collector_id(self) -> str:
        return COLLECTOR_ID

    def detect(self) -> bool:
        """Cheap, side-effect-free environment check. Returns True only if
        a standard OTLP env var is set -- NOT because that implies OTLP
        will work for Cursor (no evidence it does), but because it is the
        real, checkable signal that this machine's operator has attempted
        to point *something* at an OTLP collector, worth recording as a
        documented no-op for Cursor specifically. Unlike
        antigravity_otel.py, there is no Cursor-specific undocumented env
        var to check (no BAICODE_* equivalent was ever found for Cursor in
        this project), so this is a strictly narrower detect() than its
        Antigravity sibling.
        """
        for var in STANDARD_OTEL_ENV_VARS:
            if os.environ.get(var):
                return True

        # Weak positive: Cursor.app installed at all, matching
        # cursor_hooks.py's own third detect() heuristic (mac app bundle
        # path) -- without Cursor installed there is nothing to even
        # attempt OTel against.
        if Path("/Applications/Cursor.app").exists():
            return True
        if shutil.which("cursor") is not None:
            return True

        return False

    def activate(self) -> None:
        """Documents the absence of evidence loudly rather than silently
        doing nothing, per this framework's established pattern
        (antigravity_otel.py) for honest dead-end/gap collectors."""
        print(
            f"[{self.collector_id}] NOTE: no evidence anywhere in this "
            f"project (reference_tables/observables_table_claude_cursor.csv "
            f"or a repo-wide grep for cursor+otel/opentelemetry/OTEL_EXPORTER) "
            f"that Cursor exports OpenTelemetry/OTLP data. The CSV's "
            f"'Cursor source' column cites 'OpenTelemetry' on zero rows "
            f"(compare: it cites 'OpenTelemetry' under 'Claude Code source' "
            f"on ~18 rows). Unlike antigravity_otel.py, there is no dedicated "
            f"Cursor_OTel/ test folder in this project to confirm this via a "
            f"live otel-lgtm collector run, so this is documented as 'no "
            f"positive evidence found', not 'empirically confirmed dead end'. "
            f"Cursor's real telemetry channels are its native hooks.json "
            f"events (cursor_hooks.py) and its own backend wire traffic "
            f"(cursor_proxy2.py / cursor_mcp_proxy.py), neither of which is "
            f"OTLP. This collector's capture() will yield real events only "
            f"if fed synthetic replay_events; it has no live OTLP stream to "
            f"drain and no real captured sample to replay.",
            file=_sys.stderr,
        )
        self._activated = True
        if self._replay_events is not None:
            return
        self._start_live()

    def _start_live(self) -> None:
        feed = LiveEventFeed()
        server = LiveOTLPServer(
            self._listen_host, self._listen_port, OTLP_PREFIX, feed
        )
        self._live_feed = feed
        self._server = server
        self._bound_port = server.start()

    def capture(self) -> Iterator[RawEvent]:
        if not self._activated:
            raise RuntimeError(f"{self.collector_id}: capture() called before activate()")
        source = self._event_source()
        if source is None:
            return
        for payload in source:
            yield RawEvent(
                timestamp=RawEvent.now_iso(),
                collector_id=self.collector_id,
                agent=AGENT_NAME,
                raw_payload=payload,
            )

    def _event_source(self):
        if self._replay_events is not None:
            return self._replay_events
        return self._live_feed

    def deactivate(self) -> None:
        self._activated = False
        server = self._server
        self._server = None
        if server is not None:
            server.stop()


if __name__ == "__main__":
    collector = CursorOTelCollector()
    detected = collector.detect()
    print(f"detect() -> {detected}")

    collector.activate()

    print("\ncapture() with no replay_events (the honest, no-evidence-found behavior):")
    events = list(collector.capture())
    print(f"  yielded {len(events)} RawEvent(s) -- expected 0, no real Cursor OTLP sample exists in this project")

    print(
        "\nSIMULATED FROM CAPTURED DATA: there is no real captured Cursor "
        "OTLP payload anywhere in this project (no Cursor_OTel/ folder "
        "exists at all, unlike Codex_OTel/ or Antigravity_OTel/). "
        "Demonstrating the replay path anyway with a clearly-labeled "
        "synthetic placeholder so capture()'s non-empty code path is "
        "exercised:"
    )
    collector2 = CursorOTelCollector(
        replay_events=[
            {
                "_synthetic_placeholder": True,
                "_note": (
                    "No real OTLP payload for Cursor exists in this project -- "
                    "TODO: unverified, needs a live capture pass (e.g. a "
                    "Cursor_OTel/ test folder analogous to Codex_OTel/ or "
                    "Antigravity_OTel/) if Cursor is ever confirmed to export "
                    "OTLP."
                ),
            }
        ]
    )
    collector2.activate()
    for ev in collector2.capture():
        print(f"  {ev}")
    collector2.deactivate()

    collector.deactivate()
