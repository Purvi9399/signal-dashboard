"""
Cursor Hooks collector.

Adapted from the real event-handling logic in
projectsetup/backend/webhook_server.py (the `cursor_event()` handler,
around line ~430 onward -- "5. CURSOR WEBHOOK HANDLER") and the real
forwarding config in projectsetup/.cursor/hooks.json, which wires Cursor's
native hook names to a Node forwarder (forwarder.js) that POSTs JSON to
http://localhost:8000/webhook/cursor/event:

    beforeSubmitPrompt, afterAgentThought, preToolUse, postToolUse,
    postToolUseFailure, subagentStart, subagentStop, beforeShellExecution,
    beforeMCPExecution, afterShellExecution, afterFileEdit, beforeReadFile,
    stop, sessionEnd

Also draws on projectsetup/backend/cursor_proxy2.py, the mitmproxy sidecar
that separately intercepts Cursor's network traffic to recover token usage
(`event_type == "network_context_capture"`) when Cursor's own hooks don't
carry token counts -- webhook_server.py treats that payload shape as
distinct from a normal hook event (see cursor_event()'s early branch for
`network_context_capture`, which banks tokens without creating a session).

We do NOT import webhook_server.py or cursor_proxy2.py directly (heavy
side effects: FastAPI app startup / mitmproxy addon registration + a
`tiktoken` dependency this framework intentionally avoids). The small
field-extraction helpers below are copied out and adapted, stdlib-only.

This collector performs NO normalization: raw Cursor hook JSON passes
through as `raw_payload` completely unmodified, `event_type`/`payload`
keys and all.

KNOWN BEHAVIOR (documented, not bugs -- carried over from prior
gap-assessment research on this project, and confirmed by comments in
webhook_server.py itself):
  - Permission/gate events may not fire at all when Cursor is running in
    sandbox mode. Do not treat a missing PreToolUse-equivalent event as a
    collector malfunction under sandbox mode -- it is expected.
  - Token usage IS present via the afterAgentThought/composerSync and
    stop/sessionEnd hooks (see webhook_server.py's handling of a `usage`
    dict under `payload.get("usage")`, tagged `token_tracker.provenance =
    "sqlite_scraper"` there), and/or via cursor_proxy2.py's separate
    network-level capture. So "no PreToolUse" does not mean "no token
    telemetry" -- tokens ride on different, later events than the ones
    permission-style telemetry would use.
  - MCP-related hooks (beforeMCPExecution) only fire at all if the user
    actually has an MCP server configured in Cursor. On a machine with no
    MCP servers configured, this collector will never see an MCP event --
    that is expected absence, not a detection failure.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Iterator, List, Optional

import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parents[2]))
from core import Collector, RawEvent

AGENT_NAME = "cursor"
COLLECTOR_ID = "cursor.hooks"

# Real hook event names, taken verbatim from projectsetup/.cursor/hooks.json.
KNOWN_HOOK_EVENTS = (
    "beforeSubmitPrompt", "afterAgentThought", "preToolUse", "postToolUse",
    "postToolUseFailure", "subagentStart", "subagentStop",
    "beforeShellExecution", "beforeMCPExecution", "afterShellExecution",
    "afterFileEdit", "beforeReadFile", "stop", "sessionEnd",
)

# The distinct payload shape cursor_proxy2.py's mitmproxy sidecar posts --
# NOT a hooks.json event_type, a separate network-capture channel that
# webhook_server.py special-cases (see cursor_event()'s first branch).
NETWORK_CAPTURE_EVENT_TYPE = "network_context_capture"


def extract_event_type(payload: dict) -> str:
    """Adapted from webhook_server.py's cursor_event(): Cursor forwards
    `event_type` at the top level of the POSTed JSON (unlike Claude Code,
    which uses `hook_event_name`)."""
    return payload.get("event_type", "unknown")


def extract_session_id(payload: dict) -> str:
    return payload.get("session_id", "unknown_cursor_session")


class CursorHooksCollector(Collector):
    """Hooks-based collector for Cursor (the AI-native code editor).

    Live mode (real deployment): Cursor's .cursor/hooks.json is configured
    to invoke forwarder.js on each hook event, which POSTs JSON to a local
    HTTP endpoint. As with the Claude Code collector, a full deployment's
    activate() would start/attach to that listener and capture() would
    drain a queue it fills.

    This build supports feeding capture() from already-captured JSONL
    event dicts to exercise it deterministically against real sample data.
    """

    def __init__(self, replay_events: Optional[List[dict]] = None) -> None:
        self._replay_events = replay_events
        self._activated = False

    @property
    def collector_id(self) -> str:
        return COLLECTOR_ID

    def detect(self) -> bool:
        """Multiple real, cheap heuristics for "is Cursor installed/
        configured on this machine":
          1. `cursor` CLI on PATH.
          2. A project-local .cursor/hooks.json exists (proof hooks are
             actually wired up, mirroring projectsetup/.cursor/hooks.json).
          3. macOS app bundle present at the conventional install path.
        """
        found_any = False

        if shutil.which("cursor") is not None:
            found_any = True

        cur = Path.cwd()
        for candidate in [cur, *cur.parents]:
            hooks_path = candidate / ".cursor" / "hooks.json"
            if hooks_path.is_file():
                try:
                    cfg = json.loads(hooks_path.read_text())
                    if isinstance(cfg.get("hooks"), dict) and cfg["hooks"]:
                        found_any = True
                except (json.JSONDecodeError, OSError):
                    pass
                break

        mac_app = Path("/Applications/Cursor.app")
        if mac_app.exists():
            found_any = True

        return found_any

    def activate(self) -> None:
        """In a live deployment this would start/attach to the local HTTP
        listener the Node forwarder POSTs to. Here we just flip a flag; the
        event source (replay list) is supplied at construction time.
        """
        self._activated = True

    def capture(self) -> Iterator[RawEvent]:
        if not self._activated:
            raise RuntimeError(f"{self.collector_id}: capture() called before activate()")

        if self._replay_events is None:
            # Live mode placeholder -- no live HTTP listener wired up in
            # this standalone demo build.
            return

        for payload in self._replay_events:
            yield RawEvent(
                timestamp=RawEvent.now_iso(),
                collector_id=self.collector_id,
                agent=AGENT_NAME,
                raw_payload=payload,  # untouched, exact shape from source
            )

    def deactivate(self) -> None:
        self._activated = False
