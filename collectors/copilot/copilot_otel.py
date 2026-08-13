"""
GitHub Copilot OTel (OpenTelemetry) collector.

IMPORTANT PROVENANCE NOTE: there is no dedicated `Copilot_OTel/` folder in
this project (unlike `Codex_OTel/` and `Antigravity_OTel/`, which do exist as
siblings -- checked, including spelling variants like `Copilot_Otel`,
`CopilotOTel`, `copilot_otel` -- none exist). This collector is grounded
instead in real OTel evidence captured incidentally across TWO other test
folders while investigating other Copilot surfaces:

  Copilot/copilot_test/RESULTS.md
      -- a DEDICATED, focused test of "does plain GitHub Copilot Chat in
         VS Code export OTel data via github.copilot.chat.otel.* settings".
         Verdict: **No.** Zero traces/metrics/logs reached a real
         grafana/otel-lgtm collector across Tempo/Prometheus/Loki, despite
         the settings being applied and the window reloaded. See
         Copilot/copilot_test/evidence/{01_collector_startup.log,
         02_tempo_search_result.json, 03_prometheus_metric_names.json,
         03b_prometheus_genai_copilot_query.json, 04_loki_logs.txt,
         05_negative_results.txt, 06_copilot_chat_extension.log}.
         One relevant sub-finding from that same log: the extension's
         *embedded CLI subsystem* (used for its internal "Agent Sessions"
         feature) does self-report
         `[CopilotCLI] OpenTelemetry enabled: exporter=file, source=github.copilot`
         -- i.e. it reads the same COPILOT_OTEL_*/OTEL_EXPORTER_OTLP_* env
         vars the standalone CLI uses, NOT the github.copilot.chat.otel.*
         workspace settings -- but no output file for it was ever found on
         disk in that test.

  Copilot_VSCode_CLI/RESULTS.md (section d) + its evidence/ files
      -- a genuinely POSITIVE OTel capture, but for a different Copilot
         surface: the CLI-session feature (invoked from inside VS Code),
         not plain chat. All 3 "New Copilot CLI Session" attempts (which
         failed on a model-routing error before any tool call) still
         produced real OTLP traces/metrics reaching the collector, confirmed
         via Tempo/Prometheus queries. Raw evidence:
           evidence/tempo_trace_full.json   -- full raw OTLP JSON trace export
           evidence/tempo_search_result.json
           evidence/prometheus_metric_names.json
         `tempo_trace_full.json`'s shape (`{"batches": [{"resource": {...},
         "scopeSpans": [{"spans": [...]}]}]}`) is the real OTLP/JSON export
         format this collector's replay mode is built to consume verbatim --
         see `extract_batches()` below.

BOTTOM LINE ON WHAT THIS COLLECTOR ACTUALLY COVERS:
This is **not** a general "Copilot Chat exports OTel" collector -- real
testing shows that surface emits nothing. It is specifically a collector for
the CLI-session-under-VS-Code / standalone-CLI OTel export path, which is
the only Copilot surface with real, positive, captured OTLP evidence in this
project. Anything about plain Copilot Chat OTel export is asserted here as
a documented NEGATIVE result, not fabricated as a working feature.

No normalization: whatever real OTLP JSON shape a batch/span/metric arrives
in is passed through untouched inside `raw_payload`.
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parents[2]))
from core import Collector, RawEvent

AGENT_NAME = "copilot"
COLLECTOR_ID = "copilot.otel"

# Real, empirically-confirmed env vars that gate OTel export for the CLI /
# CLI-session path (Copilot_VSCode_CLI/RESULTS.md section a -- read directly
# off the spawned embedded CLI process's environment via `ps eww <pid>`).
# NOTE: these are the CLI's own OTel vars -- github.copilot.chat.otel.* VS
# Code *settings* were tested and confirmed to have NO effect at all
# (Copilot/copilot_test/RESULTS.md) -- do not treat that settings namespace
# as load-bearing here.
OTEL_ENV_VARS = (
    "OTEL_EXPORTER_OTLP_ENDPOINT",
    "OTEL_EXPORTER_OTLP_PROTOCOL",
    "COPILOT_OTEL_ENABLED",
)

# VS Code injects this automatically into the embedded CLI's env regardless
# of user config (Copilot_VSCode_CLI/RESULTS.md section a) -- it also forces
# content-capture off for the CLI-level resource specifically (see
# extract_resource_service_name's docstring re: the copilot-chat resource
# NOT honoring this).
VSCODE_FORCED_ENV_VARS = {
    "COPILOT_CLI_RUN_AS_NODE": "1",
    "OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT": "false",
}


def extract_batches(otlp_export: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Flatten a real OTLP/JSON trace export (the exact shape of
    Copilot_VSCode_CLI/evidence/tempo_trace_full.json: top-level "batches"
    key, each with "resource" + "scopeSpans") into one dict per batch. This
    is a *read-only* structural walk for iteration purposes only -- the
    dicts yielded are the untouched original batch objects, not a
    re-shaped/renamed copy.
    """
    return list(otlp_export.get("batches", []))


def resource_service_name(batch: Dict[str, Any]) -> Optional[str]:
    """Best-effort read of resource.attributes[].service.name for
    human/log labeling only (never mutates raw_payload). Two real service
    names were observed for Copilot's CLI-session OTel path
    (Copilot_VSCode_CLI/RESULTS.md section d):
      "github-copilot" -- the embedded CLI's own instrumentation scope.
      "copilot-chat"   -- a SEPARATE, VS-Code-only instrumentation layer
                          wrapping the CLI session, with its own metric
                          namespace (copilot_chat_*) and, notably, its own
                          more permissive content-capture behavior that is
                          NOT governed by OTEL_INSTRUMENTATION_GENAI_CAPTURE_
                          MESSAGE_CONTENT (that env var only restrains the
                          "github-copilot" resource -- confirmed in RESULTS.md
                          finding g.2, where copilot_chat.user_request still
                          contained the full verbatim prompt).
    """
    for attr in batch.get("resource", {}).get("attributes", []):
        if attr.get("key") == "service.name":
            return attr.get("value", {}).get("stringValue")
    return None


class CopilotOTelCollector(Collector):
    """OTel collector for GitHub Copilot's CLI / CLI-session export path.

    Live mode (real deployment): a live deployment would run/attach to an
    OTLP collector (e.g. `grafana/otel-lgtm`, as used in both real test
    sessions this file is grounded in) listening on 4317/4318, and drain its
    own export queryable store (Tempo for traces, Prometheus for metrics).
    This standalone build has no bundled OTLP collector server, so capture()
    supports a replay mode fed from an already-captured OTLP/JSON export
    (e.g. Copilot_VSCode_CLI/evidence/tempo_trace_full.json) to exercise the
    collector deterministically against real sample data.
    """

    def __init__(self, replay_otlp_export: Optional[Dict[str, Any]] = None) -> None:
        self._replay_otlp_export = replay_otlp_export
        self._activated = False

    @property
    def collector_id(self) -> str:
        return COLLECTOR_ID

    def detect(self) -> bool:
        """Real, cheap, side-effect-free heuristics that Copilot's OTel
        export path *could* be active on this machine:
          1. `copilot` binary on PATH (the CLI is the only Copilot surface
             with confirmed-working OTel export -- plain Chat does not
             export, per Copilot/copilot_test/RESULTS.md).
          2. Any of the real OTEL_* / COPILOT_OTEL_* env vars are set in
             this process's environment (the exact vars confirmed to gate
             export in Copilot_VSCode_CLI/RESULTS.md section a).
          3. A local ~/.copilot directory exists (same signal used by
             copilot_hooks.py's detect() -- CLI is configured/has run here).
        This intentionally does NOT try to probe a live OTLP endpoint (that
        would be a network call, violating the "cheap, no network calls"
        contract in core.Collector.detect()).
        """
        found_any = False

        if shutil.which("copilot") is not None:
            found_any = True

        for var in OTEL_ENV_VARS:
            if os.environ.get(var):
                found_any = True
                break

        if (Path.home() / ".copilot").is_dir():
            found_any = True

        return found_any

    def activate(self) -> None:
        """Documents (does not silently assume) the real env-var gating
        found in testing. Warns if the OTLP endpoint var is unset, since a
        live deployment with it unset produces the same behavior as the
        Copilot/copilot_test/RESULTS.md negative result: nothing at all
        reaches the collector, no error surfaced anywhere.
        """
        if not os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT"):
            print(
                f"[{self.collector_id}] WARNING: OTEL_EXPORTER_OTLP_ENDPOINT is "
                f"unset. Per Copilot_VSCode_CLI/RESULTS.md this is required for "
                f"the CLI/CLI-session OTel path to export anything -- and per "
                f"Copilot/copilot_test/RESULTS.md, plain Copilot Chat's own "
                f"github.copilot.chat.otel.* settings do NOT substitute for it "
                f"(confirmed to have zero effect in real testing)."
            )
        self._activated = True

    def capture(self) -> Iterator[RawEvent]:
        if not self._activated:
            raise RuntimeError(f"{self.collector_id}: capture() called before activate()")

        if self._replay_otlp_export is None:
            # Live mode placeholder: no bundled OTLP collector server is
            # wired up in this standalone demo build.
            return

        for batch in extract_batches(self._replay_otlp_export):
            # raw_payload is the untouched OTLP batch object exactly as it
            # appeared in the real captured export (tempo_trace_full.json
            # shape) -- resource_service_name() above is used only to decide
            # nothing here; it exists purely as an optional human-readable
            # helper callers may use, kept separate from raw_payload itself.
            yield RawEvent(
                timestamp=RawEvent.now_iso(),
                collector_id=self.collector_id,
                agent=AGENT_NAME,
                raw_payload=batch,
            )

    def deactivate(self) -> None:
        self._activated = False


if __name__ == "__main__":
    print("=== CopilotOTelCollector standalone test ===")

    collector = CopilotOTelCollector()
    detected = collector.detect()
    print(f"detect() -> {detected}")

    # Load the real captured OTLP export from the VS Code -> CLI test, if
    # present, and re-run the collector in replay mode against it -- this is
    # the only real, positive OTel capture available anywhere in this
    # project for any Copilot surface.
    real_evidence_path = (
        Path(__file__).resolve().parents[4]
        / "Copilot_VSCode_CLI" / "evidence" / "tempo_trace_full.json"
    )

    if real_evidence_path.is_file():
        print(f"Found real captured OTLP export at {real_evidence_path}")
        with open(real_evidence_path) as f:
            real_export = json.load(f)

        replay_collector = CopilotOTelCollector(replay_otlp_export=real_export)
        replay_collector.activate()
        print("\nSIMULATED FROM CAPTURED DATA (real OTLP batches from "
              "Copilot_VSCode_CLI/evidence/tempo_trace_full.json):")
        count = 0
        for event in replay_collector.capture():
            count += 1
            svc = resource_service_name(event.raw_payload)
            n_spans = sum(
                len(scope_span.get("spans", []))
                for scope_span in event.raw_payload.get("scopeSpans", [])
            )
            print(
                f"  [{count}] collector_id={event.collector_id!r} agent={event.agent!r} "
                f"service.name={svc!r} spans_in_batch={n_spans}"
            )
        replay_collector.deactivate()
        print(f"\nTotal batches replayed: {count}")
    else:
        print(
            f"Real evidence file not found at {real_evidence_path} -- "
            f"skipping replay demonstration. (This would only happen if "
            f"Copilot_VSCode_CLI/evidence/ was moved/deleted.)"
        )

    print("\ndetect() with no live env vars/binary present would return False "
          "on a machine with no Copilot CLI installed -- this machine's "
          "actual result is shown above.")
