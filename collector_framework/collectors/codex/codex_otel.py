"""
Codex CLI OpenTelemetry (OTel) collector.

Grounded entirely in the real, empirical capture performed for this project.
Primary sources (read-only, never modified):

  Codex_OTel/RESULTS.md                     -- the full write-up this file is built from
  Codex_OTel/config/config.toml.working_otel_section
                                             -- exact working [otel] config.toml section
                                                (3 separately-pathed otlp-http endpoints;
                                                 see _WORKING_OTEL_CONFIG_EXAMPLE below)
  Codex_OTel/sample_loki_logs.json          -- real captured OTLP log records (Loki query
                                                result shape) -- real per-event field names
  Codex_OTel/sample_loki_mcpserver.json     -- same, captured from `codex mcp-server` mode
  Codex_OTel/sample_trace_thread_start.json -- one full real Tempo trace (span tree)
  Codex_OTel/otel_related_strings_raw.txt   -- raw `strings -a` hits confirming the full
                                                real event-name/config-schema vocabulary

This collector performs NO normalization: whatever shape a real OTLP/HTTP-JSON
payload (or, for replay convenience, a single Loki "stream" attribute-dict as
captured in sample_loki_logs.json) has is passed straight through inside
raw_payload, untouched.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Iterator, List, Optional

import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parents[2]))
from collectors.live_http import LiveEventFeed  # noqa: E402
from collectors.live_otel import (  # noqa: E402
    DEFAULT_HOST,
    DEFAULT_PORT,
    LiveOTLPServer,
    install_codex_otel,
    otlp_base,
    restore_codex_otel,
)
from core import Collector, RawEvent

AGENT_NAME = "codex"
COLLECTOR_ID = "codex.otel"
OTLP_PREFIX = "/otel/codex"

# Real working [otel] config.toml section (Codex_OTel/config/config.toml.working_otel_section).
# Codex requires THREE separately-pathed otlp-http endpoints -- it does NOT
# auto-suffix a single base endpoint with /v1/logs, /v1/traces, /v1/metrics
# the way most OTel SDKs do (Codex_OTel/RESULTS.md section 1, "undocumented
# footgun"). A naive single-endpoint config silently 404s at default log
# verbosity (RUST_LOG hardcodes opentelemetry_sdk/opentelemetry_otlp to
# "off"). This constant documents the real shape a deploying user needs;
# this collector does not write config.toml itself.
_WORKING_OTEL_CONFIG_EXAMPLE = """
[otel]
environment = "test"
log_user_prompt = true

[otel.exporter.otlp-http]
endpoint = "http://localhost:4318/v1/logs"
protocol = "json"

[otel.trace_exporter.otlp-http]
endpoint = "http://localhost:4318/v1/traces"
protocol = "json"

[otel.metrics_exporter.otlp-http]
endpoint = "http://localhost:4318/v1/metrics"
protocol = "json"
""".strip()

# Real codex.* log event_name values actually observed on the wire
# (Codex_OTel/RESULTS.md section 3), matching and extending the binary's
# static string list. Reference only -- capture() never filters on this list,
# since the vendor's own "common fields" claim was already found incomplete
# for Hooks and there is no reason to trust a curated list here either.
KNOWN_LOG_EVENT_NAMES = (
    "codex.conversation_starts", "codex.startup_phase", "codex.user_prompt",
    "codex.api_request", "codex.websocket_connect", "codex.websocket_request",
    "codex.sse_event", "codex.tool_decision", "codex.tool_result", "codex.turn_ttft",
)

# CONFIRMED PRIVACY GAP (Codex_OTel/RESULTS.md section 4): every single
# codex.* log record -- not just codex.user_prompt -- carries the real
# ChatGPT account `user_email` and `user_account_id` as top-level attributes,
# regardless of the `log_user_prompt` toggle (that flag only controls whether
# the raw prompt TEXT is included). This collector does NOT redact/strip
# these fields -- raw_payload must stay exactly what Codex emitted -- but
# callers building a policy/redaction stage on top of this collector should
# be aware every record needs scrubbing, not just codex.user_prompt ones.
PII_BEARING_ATTRS = ("user_email", "user_account_id")

# CONFIRMED BUG, same class found in codex_hooks.py's PostToolUse
# (Codex_OTel/RESULTS.md section 5): `codex.tool_result`'s `success`
# attribute is hardcoded the string "true" even for a confirmed exit-code-1
# command; the free-text `output` attribute is the only place the real
# exit code is visible, and only if you parse it yourself. This collector
# does not attempt to re-derive a corrected success/failure verdict --
# that would be normalization, out of scope for a raw collector.


def find_codex_home() -> Path:
    return Path.home() / ".codex"


def check_otel_configured(codex_home: Path) -> bool:
    """Real, cheap evidence that OTel export is actually configured, per
    Codex_OTel/RESULTS.md section 1: telemetry is 100% config.toml-driven
    (no CLI flags at all -- confirmed via `codex --help`/`codex exec --help`
    static inspection), and it is OFF by default until an `[otel]` table
    with an explicit exporter is present. We do a cheap textual scan for the
    `[otel` table header rather than a full TOML parse, mirroring the same
    tradeoff codex_hooks.py makes for its `hooks.state.` trust-scan.
    """
    config_path = codex_home / "config.toml"
    if not config_path.is_file():
        return False
    try:
        text = config_path.read_text()
    except OSError:
        return False
    return "[otel]" in text or "[otel." in text


class CodexOTelCollector(Collector):
    """OpenTelemetry-based collector for the Codex CLI (OpenAI).

    Live mode (real deployment): Codex exports OTLP/HTTP-JSON logs, traces,
    and metrics to whatever three (per-signal) endpoints are configured
    under `[otel]` in `~/.codex/config.toml` (Codex_OTel/RESULTS.md section
    1). A full deployment's activate() would stand up (or attach to) an
    OTLP-compatible receiver (e.g. grafana/otel-lgtm, as used in the real
    test) at those endpoints; capture() would then read back accepted
    records from that collector's own query API (Loki/Tempo/Prometheus, as
    the real test did) or ingest raw OTLP payloads directly.

    This build supports feeding capture() from already-captured JSONL/JSON
    records shaped like the real Loki "stream" attribute dicts in
    Codex_OTel/sample_loki_logs.json to exercise it deterministically
    against real sample data, without standing up a live otel-lgtm stack.
    """

    def __init__(
        self,
        replay_events: Optional[List[dict]] = None,
        require_configured: bool = True,
        listen_host: str = DEFAULT_HOST,
        listen_port: int = DEFAULT_PORT,
        codex_home: Optional[Path] = None,
        install_config: Optional[bool] = None,
    ) -> None:
        self._replay_events = replay_events
        self._activated = False
        self._require_configured = require_configured
        self._listen_host = listen_host
        self._listen_port = listen_port
        self._codex_home = codex_home
        self._install_config = install_config
        self._live_feed: Optional[LiveEventFeed] = None
        self._server: Optional[LiveOTLPServer] = None
        self._config_backup: Optional[bytes] = None
        self._config_path: Optional[Path] = None
        self._bound_port = listen_port

    @property
    def listen_port(self) -> int:
        return self._bound_port

    @property
    def collector_id(self) -> str:
        return COLLECTOR_ID

    def detect(self) -> bool:
        """Real, cheap heuristics for "is Codex CLI present and possibly
        OTel-capable on this machine", per Codex_OTel/RESULTS.md:
          1. `codex` binary on PATH (same check as codex_hooks.py).
          2. ~/.codex directory exists (CODEX_HOME).
          3. ~/.codex/config.toml contains an [otel] table (proof telemetry
             export is actually configured, not just that the CLI exists --
             mirrors codex_hooks.py's hooks.json-presence check).
        Unlike hooks.json (which is a strong "hooks configured" signal on
        its own), OTel has NO dedicated marker file -- it's a single table
        inside config.toml alongside everything else -- so this check is
        necessarily a substring scan of that shared file.
        """
        found_any = False

        if shutil.which("codex") is not None:
            found_any = True

        codex_home = find_codex_home()
        if codex_home.is_dir():
            found_any = True

        if check_otel_configured(codex_home):
            found_any = True

        return found_any

    def activate(self) -> None:
        """Unlike codex_hooks.py's trust gate (which raises loudly on a
        missing precondition), a missing [otel] config is not an error
        condition here -- it is a silent, confirmed default (Codex_OTel/
        RESULTS.md section 1: "with no [otel] section... unchanged (64 -> 64
        log records accepted)... No network activity toward our collector at
        all"). We surface this via a best-effort flag on the instance
        (self._configured) rather than raising, since "OTel not configured"
        is Codex's documented, intentional off-by-default state, not a trap
        the way the silent hook-trust gate is. Set require_configured=False
        to skip even the informational check (e.g. for pure-replay testing).
        """
        self._configured = True
        if self._require_configured:
            home = self._codex_home or find_codex_home()
            self._configured = check_otel_configured(home)
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
        self._maybe_install_config()

    def _maybe_install_config(self) -> None:
        if self._install_config is False:
            return
        home = self._codex_home or find_codex_home()
        path = home / "config.toml"
        base = otlp_base(self._listen_host, self._bound_port, "codex")
        self._config_path = path
        self._config_backup = install_codex_otel(path, base)

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
        self._restore_config()

    def _restore_config(self) -> None:
        if self._replay_events is not None:
            return
        path = self._config_path
        if path is None:
            return
        restore_codex_otel(path, self._config_backup)
        self._config_backup = None


def _load_sample_loki_streams(max_events: int = 5) -> List[dict]:
    """Best-effort loader for a few real captured log records out of
    Codex_OTel/sample_loki_logs.json, for use by the __main__ demo below.
    Returns [] if the sample file isn't reachable from this checkout (this
    collector file must stay independently importable/runnable even when
    the raw evidence tree isn't alongside it).
    """
    candidates = [
        Path(__file__).resolve().parents[4] / "Codex_OTel" / "sample_loki_logs.json",
        Path("/Users/malharshinde/Desktop/SOAISEC/Project_Signal/Codex_OTel/sample_loki_logs.json"),
    ]
    for path in candidates:
        if path.is_file():
            try:
                data = json.loads(path.read_text())
                streams = data.get("data", {}).get("result", [])
                return [s["stream"] for s in streams[:max_events] if "stream" in s]
            except (json.JSONDecodeError, OSError, KeyError):
                continue
    return []


if __name__ == "__main__":
    collector = CodexOTelCollector(require_configured=False)

    print(f"[{collector.collector_id}] detect() -> {collector.detect()}")

    sample_events = _load_sample_loki_streams(max_events=5)
    if sample_events:
        print(f"[{collector.collector_id}] loaded {len(sample_events)} real captured "
              f"log records from Codex_OTel/sample_loki_logs.json")
        collector = CodexOTelCollector(replay_events=sample_events, require_configured=False)
    else:
        print(f"[{collector.collector_id}] sample_loki_logs.json not reachable from this "
              f"checkout -- capture() will yield nothing (no synthetic fallback data).")

    collector.activate()
    print(f"[{collector.collector_id}] activate() ok, configured={getattr(collector, '_configured', None)}")

    count = 0
    for event in collector.capture():
        count += 1
        label = "SIMULATED FROM CAPTURED DATA"
        print(f"\n--- RawEvent #{count} ({label}) ---")
        print(f"timestamp:    {event.timestamp}")
        print(f"collector_id: {event.collector_id}")
        print(f"agent:        {event.agent}")
        print(f"event_name:   {event.raw_payload.get('event_name')}")
        print(f"raw_payload keys: {sorted(event.raw_payload.keys())}")
        if count >= 5:
            break

    collector.deactivate()
    print(f"\n[{collector.collector_id}] deactivate() ok. Total events yielded: {count}")
