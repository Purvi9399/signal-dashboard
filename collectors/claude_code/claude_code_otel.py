"""
Claude Code OpenTelemetry (OTel) collector.

PROVENANCE NOTE: unlike Codex_OTel/ and Antigravity_OTel/, there is no
dedicated `Claude_Code_OTel/` (or spelling variant -- checked `ClaudeCode_OTel`,
`ClaudeCodeOTel`, `claude_code_otel`) folder anywhere in this project, and no
captured OTLP export (trace/metric/log JSON) for Claude Code exists on disk
either (checked recursively for OTLP-shaped JSON with "scopeSpans"/"resourceMetrics"
keys and found none tied to Claude Code). This is the same situation
copilot_otel.py documents for plain Copilot Chat: real field-level grounding
exists, but no real captured payload to replay.

WHAT IS ACTUALLY GROUNDED HERE, AND FROM WHERE:

  1. reference_tables/observables_table_claude_cursor.csv -- a real,
     hands-on field-level observability table. Every field this collector's
     module-level docs claim comes from Claude Code's OTel export is cited
     to a specific CSV row/Note, verbatim:
       - sequence_num          <- row "sequence_num": "OpenTelemetry" / Note:
                                   "OTel event.sequence."
       - tool_version          <- row "tool_version": "OpenTelemetry" / Note:
                                   "CC service.version in OTel"
       - operator_email        <- row "operator_email": "OpenTelemetry" /
                                   Note: "CC only in OTel"
       - operator_id           <- row "operator_id": "OpenTelemetry" / Note:
                                   "OTel user.account_id."
       - org_id                <- row "org_id": "OpenTelemetry" / Note:
                                   "OTel organization.id."
       - team_id               <- row "team_id": "OpenTelemetry" / Note:
                                   "Only because we inject it via
                                   OTEL_RESOURCE_ATTRIBUTES."
       - prompt_length         <- row "prompt_length": "OpenTelemetry" /
                                   Note: "OTel gives it even when redacted."
       - slash_command         <- row "slash_command": "OpenTelemetry" /
                                   Note: "OTel command_name, verified."
       - model_id              <- row "model_id": "OpenTelemetry"
       - cost_usd              <- row "cost_usd": "OpenTelemetry" / Note:
                                   "CC emits claude_code.cost.usage."
       - request_latency_ms    <- row "request_latency_ms": "OpenTelemetry" /
                                   Note: "OTel api_request.duration_ms."
       - time_to_first_token   <- row "time_to_first_token": "OpenTelemetry" /
                                   Note: "OTel only."
       - tool_result_size      <- row "tool_result_size": "OpenTelemetry"
       - lines_added/removed   <- row "lines_added"/"lines_removed":
                                   "OpenTelemetry" / Note:
                                   "OTel claude_code.lines_of_code.count,
                                   verified."
       - permission_decision   <- row "permission_decision": "OpenTelemetry" /
                                   Note: "OTel tool_decision. Hooks fire the
                                   request but never the denial."
       - permission_source     <- row "permission_source": "OpenTelemetry" /
                                   Note: "Verified value user_temporary,
                                   meaning approved once not always."
       - turn_id / prompt.id   <- row "turn_id": "local hooks" / Note:
                                   "CC prompt_id, verified identical to
                                   OTel prompt.id." (confirms hooks'
                                   prompt_id and OTel's prompt.id are the
                                   SAME value, cross-checked in real testing)
     Field NAMES in OTEL_EVENT_ATTRIBUTE_HINTS below (service.version,
     prompt.id, user.account_id, organization.id, claude_code.cost.usage,
     claude_code.lines_of_code.count, api_request.duration_ms, tool_decision,
     command_name, event.sequence) are copied verbatim from this task's own
     brief, which in turn traces to the CSV Notes column quoted above -- they
     are NOT independently re-derived or guessed by this file.

  2. claude_code_hooks.py -- confirms the real hook payload shape
     (session_id, prompt_id would appear here) that OTel's prompt.id is
     cross-checked against per the CSV's turn_id row.

  3. Claude Code's documented OTel-gating env vars: the CSV/repo do not
     themselves document *how* to turn OTel on (only what it emits once on),
     so OTEL_ENV_VARS below is sourced from general knowledge of Anthropic's
     publicly documented Claude Code telemetry configuration, NOT from any
     file in this repo -- flagged explicitly rather than presented as
     repo-evidenced. TODO: unverified against a live capture in this repo;
     needs confirmation the same way Copilot_VSCode_CLI/RESULTS.md confirmed
     Copilot's OTel env vars via `ps eww <pid>` on a real running process.

BOTTOM LINE: this collector's detect()/activate() logic and field-mapping
documentation are real (grounded in the CSV), but there is no captured OTLP
sample anywhere in this project for Claude Code to replay -- so, honestly,
like antigravity_otel.py's confirmed-dead-end pattern (though NOT a
confirmed dead end here -- just unverified/no sample), capture() legitimately
yields 0 events unless fed synthetic/replay_events.

No normalization: raw_payload is passed through completely untouched,
whatever shape a real OTLP export or metric/log record happens to have.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parents[2]))
from core import Collector, RawEvent

AGENT_NAME = "claude_code"
COLLECTOR_ID = "claude_code.otel"

# TODO: unverified against a live capture in THIS repo (no Claude_Code_OTel/
# folder exists here, unlike Codex_OTel/Antigravity_OTel). Sourced from
# general knowledge of Anthropic's publicly documented Claude Code telemetry
# env vars, not from repo evidence -- flagged per module docstring above.
OTEL_ENV_VARS = (
    "CLAUDE_CODE_ENABLE_TELEMETRY",
    "OTEL_EXPORTER_OTLP_ENDPOINT",
    "OTEL_EXPORTER_OTLP_PROTOCOL",
    "OTEL_METRICS_EXPORTER",
    "OTEL_LOGS_EXPORTER",
    "OTEL_RESOURCE_ATTRIBUTES",  # CSV: team_id "Only because we inject it via OTEL_RESOURCE_ATTRIBUTES."
)

# Real OTel attribute names this task's brief (traced to the CSV Notes
# column, see module docstring) confirms Claude Code emits. Kept here purely
# as documentation/lookup for anything that parses a real OTLP export later
# -- capture() does NOT use this to reshape raw_payload (no normalization).
OTEL_EVENT_ATTRIBUTE_HINTS = {
    "service.version": "tool_version (CSV row 9)",
    "prompt.id": "turn_id, verified identical to hooks' prompt_id (CSV row 13)",
    "user.account_id": "operator_id (CSV row 24)",
    "organization.id": "org_id (CSV row 26)",
    "event.sequence": "sequence_num (CSV row 7)",
    "claude_code.cost.usage": "cost_usd (CSV row 59)",
    "claude_code.lines_of_code.count": "lines_added / lines_removed (CSV rows 99-100)",
    "api_request.duration_ms": "request_latency_ms (CSV row 61)",
    "tool_decision": "permission_decision (CSV row 110)",
    "command_name": "slash_command (CSV row 50)",
    "prompt_length": "prompt_length, present even when prompt text is <REDACTED> (CSV row 42)",
}


class ClaudeCodeOTelCollector(Collector):
    """OTel collector for Claude Code's OTLP metrics/traces/logs export.

    Live mode (real deployment): a live deployment would run/attach to an
    OTLP collector (e.g. an otel-lgtm stack) listening on 4317/4318 and
    drain the resulting trace/metric/log store for Claude Code's resource
    (service.name / service.version attributes). This standalone build has
    no bundled OTLP collector server and no captured sample export exists
    anywhere in this repo for Claude Code (see module docstring), so
    capture() only supports a replay mode fed from an externally-supplied
    OTLP-shaped export/records list.
    """

    def __init__(self, replay_events: Optional[List[Dict[str, Any]]] = None) -> None:
        self._replay_events = replay_events
        self._activated = False

    @property
    def collector_id(self) -> str:
        return COLLECTOR_ID

    def detect(self) -> bool:
        """Real, cheap, side-effect-free heuristics:
          1. `claude` binary on PATH (claude_code_hooks.py's own detect()
             uses the same check).
          2. Any of the OTel-gating env vars (see OTEL_ENV_VARS -- flagged
             TODO/unverified-in-this-repo above) are set in this process's
             environment.
        Does not probe a live OTLP endpoint (a network call, which would
        violate the Collector.detect() "cheap, no network calls" contract).
        """
        found_any = False

        if shutil.which("claude") is not None:
            found_any = True

        for var in OTEL_ENV_VARS:
            if os.environ.get(var):
                found_any = True
                break

        return found_any

    def activate(self) -> None:
        """Documents the gap honestly rather than pretending a live OTLP
        listener is wired up. No real sample export exists in this repo to
        seed a default replay from (see module docstring) -- unlike
        copilot_otel.py, which had a genuine positive capture
        (tempo_trace_full.json) to fall back on.
        """
        if not os.environ.get("CLAUDE_CODE_ENABLE_TELEMETRY") and not os.environ.get(
            "OTEL_EXPORTER_OTLP_ENDPOINT"
        ):
            print(
                f"[{self.collector_id}] NOTE: neither CLAUDE_CODE_ENABLE_TELEMETRY "
                f"nor OTEL_EXPORTER_OTLP_ENDPOINT is set in this process's "
                f"environment. Per general Claude Code documentation (NOT "
                f"repo-evidenced -- see module docstring TODO) these gate "
                f"whether Claude Code emits OTel data at all. Additionally, no "
                f"captured OTLP sample export exists anywhere in this project "
                f"for Claude Code (checked, unlike Codex_OTel/Antigravity_OTel), "
                f"so capture() will yield 0 events unless fed replay_events.",
                file=_sys.stderr,
            )
        self._activated = True

    def capture(self) -> Iterator[RawEvent]:
        if not self._activated:
            raise RuntimeError(f"{self.collector_id}: capture() called before activate()")

        if not self._replay_events:
            # Honest empty stream: no live OTLP collector wired up in this
            # standalone build, and no real captured sample exists in this
            # repo for Claude Code to replay (see module docstring).
            return

        for payload in self._replay_events:
            yield RawEvent(
                timestamp=RawEvent.now_iso(),
                collector_id=self.collector_id,
                agent=AGENT_NAME,
                raw_payload=payload,  # untouched, whatever OTLP shape supplied
            )

    def deactivate(self) -> None:
        self._activated = False


if __name__ == "__main__":
    print("=== ClaudeCodeOTelCollector standalone test ===")

    collector = ClaudeCodeOTelCollector()
    detected = collector.detect()
    print(f"detect() -> {detected} (checks `claude` on PATH + OTel env vars: {OTEL_ENV_VARS})")

    collector.activate()

    print("\ncapture() with no replay_events (the real, honest state of this "
          "repo -- no captured Claude Code OTLP export exists anywhere in "
          "this project, see module docstring):")
    events = list(collector.capture())
    print(f"  yielded {len(events)} RawEvent(s) -- expected 0, no real sample data available")
    collector.deactivate()

    print(
        "\nSIMULATED FROM CAPTURED DATA: no real Claude Code OTLP payload "
        "exists in this repo, so this is a clearly-labeled synthetic "
        "placeholder ONLY to exercise capture()'s non-empty code path, "
        "using the real attribute names cited above (not fabricated names):"
    )
    synthetic = [
        {
            "_synthetic_placeholder": True,
            "_note": "No real captured OTLP export for Claude Code exists in this repo. "
                     "Field names below are the real, task-brief/CSV-cited attribute names "
                     "(see OTEL_EVENT_ATTRIBUTE_HINTS), values are illustrative only.",
            "resource": {"attributes": [{"key": "service.version", "value": {"stringValue": "2.1.204"}}]},
            "attributes": {
                "prompt.id": "b13c534e-e91c-473a-aadc-5683b34756a6",
                "user.account_id": "unknown",
                "organization.id": "unknown",
                "event.sequence": 1,
                "claude_code.cost.usage": 0.0,
                "claude_code.lines_of_code.count": 0,
                "api_request.duration_ms": 0,
                "tool_decision": "user_temporary",
                "command_name": None,
                "prompt_length": 0,
            },
        }
    ]
    collector2 = ClaudeCodeOTelCollector(replay_events=synthetic)
    collector2.activate()
    for ev in collector2.capture():
        print(f"  collector_id={ev.collector_id!r} agent={ev.agent!r} attrs={list(ev.raw_payload['attributes'].keys())}")
    collector2.deactivate()
