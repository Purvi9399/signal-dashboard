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
from collectors.cursor.hook_settings import (  # noqa: E402
    forwarder_command,
    install_cursor_command_hooks,
    restore_hooks_file,
)
from collectors.live_http import HookHTTPServer, LiveEventFeed  # noqa: E402
from core import Collector, RawEvent

AGENT_NAME = "cursor"
COLLECTOR_ID = "cursor.hooks"
WEBHOOK_PATH = "/webhook/cursor/event"
_DEFAULT_HOST = "127.0.0.1"
_DEFAULT_PORT = 8000

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

    Live: attach `/webhook/cursor/event` on the shared listener and merge
    user `~/.cursor/hooks.json` command hooks (`forward_hook.py` POSTs
    stdin JSON). Replay: iterate a supplied payload list. Raw JSON is
    unmodified. See LIVE_HOOKS.md FR-H13.
    """

    def __init__(
        self,
        replay_events: Optional[List[dict]] = None,
        hooks_path: Optional[Path] = None,
        listen_host: str = _DEFAULT_HOST,
        listen_port: int = _DEFAULT_PORT,
        install_hooks: Optional[bool] = None,
    ) -> None:
        self._replay_events = replay_events
        self._hooks_path = hooks_path
        self._listen_host = listen_host
        self._listen_port = listen_port
        self._install_hooks = install_hooks
        self._activated = False
        self._live_feed: Optional[LiveEventFeed] = None
        self._server: Optional[HookHTTPServer] = None
        self._hooks_backup: Optional[bytes] = None
        self._bound_port = listen_port

    @property
    def listen_port(self) -> int:
        return self._bound_port

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
        """Live: shared HTTP listener + merge ~/.cursor/hooks.json. Replay: flag only."""
        self._activated = True
        if self._replay_events is not None:
            return
        self._start_live()

    def _start_live(self) -> None:
        feed = LiveEventFeed()
        server = HookHTTPServer(
            self._listen_host, self._listen_port, WEBHOOK_PATH, feed
        )
        self._live_feed = feed
        self._server = server
        self._bound_port = server.start()
        self._maybe_install_hooks()

    def _maybe_install_hooks(self) -> None:
        if self._install_hooks is False:
            return
        path = self._hooks_path or (Path.home() / ".cursor" / "hooks.json")
        url = f"http://{self._listen_host}:{self._bound_port}{WEBHOOK_PATH}"
        self._hooks_path = path
        self._hooks_backup = install_cursor_command_hooks(
            path, forwarder_command(url), KNOWN_HOOK_EVENTS
        )

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
                raw_payload=payload,  # untouched, exact shape from source
            )

    def _event_source(self):
        if self._replay_events is not None:
            return self._replay_events
        return self._live_feed

    def deactivate(self) -> None:
        self._activated = False
        self._stop_server()
        self._restore_hooks()

    def _stop_server(self) -> None:
        server = self._server
        self._server = None
        if server is not None:
            server.stop()

    def _restore_hooks(self) -> None:
        if self._replay_events is not None:
            return
        path = self._hooks_path
        if path is None:
            return
        restore_hooks_file(path, self._hooks_backup)
        self._hooks_backup = None
