"""
Antigravity OpenTelemetry (OTLP) collector.

Grounded entirely in the real, empirical test performed for this project.
Primary source (read-only, never modified):

  Antigravity_OTel/RESULTS.md -- full write-up of a live test against a
  real grafana/otel-lgtm OTLP collector, run against both the `agy` CLI
  and the Antigravity.app IDE (v2.4.3 IDE / 1.1.8 CLI), with the standard
  OTEL_EXPORTER_OTLP_ENDPOINT / OTEL_EXPORTER_OTLP_PROTOCOL /
  OTEL_SERVICE_NAME env vars set on both surfaces.
  Antigravity_OTel/evidence/binary_otel_strings.txt -- static string dump
  of the `language_server` Go binary (shared by IDE + CLI) confirming zero
  occurrences of any OTEL_EXPORTER_OTLP_* env var name anywhere in it.
  Antigravity_OTel/evidence/binary_telemetry_backend_strings.txt --
  confirms the real (non-OTLP) telemetry channels: Clearcut and a
  Google-proprietary `businessaicode.v1*.TelemetryService/SendTelemetry`
  gRPC service, gated by undocumented BAICODE_*_ENDPOINT_URL env vars.
  Antigravity_OTel/evidence/prometheus_increase_spans_20m.json,
  prometheus_increase_logs_20m.json -- both `0`: the collector's own
  ingestion counters proved zero spans/logs arrived during either test
  session, cross-checked against Prometheus's own accepted-count metrics,
  not just the UI.

HEADLINE FINDING (RESULTS.md section 5, "Verdict"): Antigravity -- both
the CLI and IDE, which share one binary -- DOES NOT export OTLP data under
any combination of the standard OTEL_EXPORTER_OTLP_* env vars, on this
version. This is not a config gap: it is architecturally absent (the OTel
Go SDK is linked in and genuinely used to wrap outbound Google Cloud API
calls, but registered against a no-op TracerProvider with zero exporter
wired to it -- see RESULTS.md section 2). Real session telemetry instead
flows over two Google-proprietary channels (Clearcut, businessaicode
SendTelemetry RPC), neither of which is OTLP or interceptable by a
self-hosted collector.

Consequently this collector is built to HONESTLY reflect a dead end
rather than pretend to work: detect() looks for real, currently-existing
signals of "OTel-adjacent config was touched" (the undocumented
BAICODE_*_ENDPOINT_URL / ANTIGRAVITY_SENTRY_SAMPLE_RATE env vars, and a
standard OTEL_EXPORTER_OTLP_ENDPOINT env var if a user sets one, since
that's the standard mechanism any operator would try first) purely so
downstream tooling can record "an operator attempted to enable OTel here"
-- but capture() is honest that, per the empirical evidence, no OTLP
event stream actually exists to drain. This collector supports feeding
it synthetic/replay events ONLY for the hypothetical future where
Antigravity ships a real OTLP exporter; there is no real captured OTLP
payload anywhere in Antigravity_OTel/ to replay because none was ever
emitted (the whole point of the test).
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Iterator, List, Optional

import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parents[2]))
from core import Collector, RawEvent

AGENT_NAME = "antigravity"
COLLECTOR_ID = "antigravity.otel"

# Confirmed real (RESULTS.md section 3) undocumented env vars that exist in
# the language_server binary's string table. These are NOT OTLP endpoints
# -- they configure Google's own proprietary businessaicode telemetry RPC
# -- but their presence is the closest real, checkable signal that
# somebody has gone looking at Antigravity's telemetry configuration at
# all, which is useful operational context even though it says nothing
# about OTLP specifically.
BAICODE_ENV_VARS = (
    "BAICODE_TELEMETRY_ENDPOINT_URL",
    "BAICODE_PREDICTION_ENDPOINT_URL",
    "BAICODE_MANAGEMENT_ENDPOINT_URL",
)
SENTRY_ENV_VAR = "ANTIGRAVITY_SENTRY_SAMPLE_RATE"

# Standard OTLP env vars. Confirmed (RESULTS.md section 1, static+dynamic)
# that Antigravity's binary contains ZERO references to any of these --
# setting them has no effect. Included in detect() only because a real
# operator following standard OTel practice would set these first, and we
# want detect() to notice that attempt even though it's confirmed inert.
STANDARD_OTEL_ENV_VARS = (
    "OTEL_EXPORTER_OTLP_ENDPOINT",
    "OTEL_EXPORTER_OTLP_PROTOCOL",
    "OTEL_SERVICE_NAME",
)


class AntigravityOTelCollector(Collector):
    """OTLP-based collector for Antigravity -- CONFIRMED ARCHITECTURALLY
    DEAD by direct empirical testing (Antigravity_OTel/RESULTS.md).

    This class exists to be a truthful, non-silent placeholder in the
    registry rather than a working data source. Registering it and letting
    detect()/activate() run is useful precisely because it will honestly
    report "nothing here" instead of a downstream consumer having to
    rediscover this dead end themselves.

    detect() looks for real, on-disk/environment evidence that OTel-related
    config was touched (standard OTEL_EXPORTER_OTLP_* env vars, or the
    Antigravity-specific BAICODE_*/Sentry env vars) -- but per RESULTS.md,
    even a full positive detect() (someone diligently set every OTLP env
    var) will yield zero OTLP events, confirmed via a live otel-lgtm
    collector's own ingestion counters staying at baseline for the entire
    test window.
    """

    def __init__(self, replay_events: Optional[List[dict]] = None) -> None:
        # No real captured OTLP payload exists anywhere in
        # Antigravity_OTel/ (the test's entire point was that none arrived)
        # so replay_events is only for a hypothetical future exporter,
        # never populated from real data by this build.
        self._replay_events = replay_events
        self._activated = False

    @property
    def collector_id(self) -> str:
        return COLLECTOR_ID

    def detect(self) -> bool:
        """Cheap, side-effect-free environment check for OTel-adjacent
        config. Returns True if any standard OTLP env var OR any of the
        confirmed-real Antigravity-specific telemetry env vars are set --
        NOT because that implies OTLP will work (it won't, per RESULTS.md),
        but because it's the real, checkable signal that this machine's
        operator has attempted (or could attempt) to point Antigravity's
        telemetry somewhere, which is worth recording even as a documented
        no-op.
        """
        for var in STANDARD_OTEL_ENV_VARS:
            if os.environ.get(var):
                return True
        for var in BAICODE_ENV_VARS:
            if os.environ.get(var):
                return True
        if os.environ.get(SENTRY_ENV_VAR):
            return True

        # Also treat "agy CLI is installed at all" as a weak positive --
        # confirmed in RESULTS.md section 0 that `agy` is a separate
        # Homebrew-cask-installed binary (`antigravity-cli`), symlinked as
        # `agy` on PATH -- the same binary whose OTLP-inertness this
        # collector documents. Without at least the target installed there
        # is nothing whatsoever to even attempt OTel against.
        if shutil.which("agy") is not None:
            return True

        return False

    def activate(self) -> None:
        """Documents the confirmed dead end loudly rather than silently
        doing nothing. Does not start any real OTLP receiver/listener --
        there would be nothing to listen for (RESULTS.md section 1: zero
        spans/logs/metrics above baseline across two full sessions,
        verified against the collector's own accepted-span/accepted-log
        counters, not just a UI)."""
        print(
            f"[{self.collector_id}] NOTE: Antigravity does not export "
            f"OpenTelemetry/OTLP data on this product as currently shipped "
            f"(confirmed both statically -- zero OTEL_EXPORTER_OTLP_* "
            f"strings in the language_server binary -- and dynamically -- "
            f"zero accepted spans/logs/metrics against a real otel-lgtm "
            f"collector across two full CLI+IDE sessions). Real user "
            f"telemetry instead flows over Google-proprietary Clearcut / "
            f"businessaicode.TelemetryService channels, neither of which "
            f"is reachable by a self-hosted collector. See "
            f"Antigravity_OTel/RESULTS.md. This collector's capture() will "
            f"yield real events only if fed synthetic replay_events; it "
            f"has no live OTLP stream to drain."
        )
        self._activated = True

    def capture(self) -> Iterator[RawEvent]:
        if not self._activated:
            raise RuntimeError(f"{self.collector_id}: capture() called before activate()")

        if not self._replay_events:
            # Honest empty stream: this is not a bug, it's the confirmed
            # real behavior of the target (RESULTS.md section 5).
            return

        for payload in self._replay_events:
            yield RawEvent(
                timestamp=RawEvent.now_iso(),
                collector_id=self.collector_id,
                agent=AGENT_NAME,
                raw_payload=payload,
            )

    def deactivate(self) -> None:
        self._activated = False


if __name__ == "__main__":
    collector = AntigravityOTelCollector()
    detected = collector.detect()
    print(f"detect() -> {detected}")

    collector.activate()

    print("capture() with no replay_events (the real, confirmed empirical behavior):")
    events = list(collector.capture())
    print(f"  yielded {len(events)} RawEvent(s) -- expected 0, matches RESULTS.md's confirmed dead end")

    print(
        "\nSIMULATED FROM CAPTURED DATA: there is no real captured OTLP "
        "payload to replay (Antigravity_OTel/RESULTS.md's entire finding is "
        "that none was ever emitted). Demonstrating the replay path anyway "
        "with a clearly-labeled synthetic placeholder so capture()'s "
        "non-empty code path is exercised:"
    )
    collector2 = AntigravityOTelCollector(
        replay_events=[
            {
                "_synthetic_placeholder": True,
                "_note": (
                    "No real OTLP payload exists in Antigravity_OTel/ -- "
                    "TODO: unverified, needs live capture if a future "
                    "Antigravity version ever ships a working OTLP exporter."
                ),
            }
        ]
    )
    collector2.activate()
    for ev in collector2.capture():
        print(f"  {ev}")
    collector2.deactivate()

    collector.deactivate()
