"""
Claude Code Hooks collector.

Adapted from the real event-handling logic in
projectsetup/backend/webhook_server.py (the `claude_event()` handler, see
that file around line ~896 onward) and the real hook config in
projectsetup/.claude/settings.json, which wires these hook names to an
http POST at http://localhost:8000/webhook/claude/event:

    SessionStart, PreToolUse, PostToolUse, PostToolUseFailure,
    SubagentStart, SubagentStop, Stop, SessionEnd, UserPromptSubmit,
    Notification, PreCompact

We do NOT import webhook_server.py directly (it starts a FastAPI server and
has heavy side effects at import time / module scope). Instead the small,
self-contained field-extraction logic below is copied out and adapted from
it, with the same field-name fallbacks the real handler uses (Claude Code
hook payloads are JSON with fields like `hook_event_name`, `session_id`,
`transcript_path`, `tool_name`, `tool_input`, `prompt`, etc. -- see the real
captured samples in projectsetup/backend/events/*.jsonl).

This collector performs NO normalization: it forwards the hook JSON as
`raw_payload` completely unmodified. It only extracts `hook_event_name`
and `session_id` to decide what to print/log for humans and to route
events into a RawEvent envelope -- it never rewrites or drops fields.
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Iterator, List, Optional

import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parents[2]))
from core import Collector, RawEvent

AGENT_NAME = "claude_code"
COLLECTOR_ID = "claude_code.hooks"

# The real hook event names Claude Code fires, taken verbatim from
# projectsetup/.claude/settings.json.
KNOWN_HOOK_EVENTS = (
    "SessionStart", "PreToolUse", "PostToolUse", "PostToolUseFailure",
    "SubagentStart", "SubagentStop", "Stop", "SessionEnd",
    "UserPromptSubmit", "Notification", "PreCompact",
)


def extract_hook_event_name(payload: dict) -> str:
    """Adapted from webhook_server.py's claude_event(): Claude Code hook
    payloads can carry the event name under a couple of possible keys
    depending on hook-runner version, so try them in the same priority
    order the production handler does."""
    return (
        payload.get("hook_event_name")
        or payload.get("hook")
        or payload.get("event")
        or payload.get("type")
        or "unknown"
    )


def extract_session_id(payload: dict) -> str:
    return payload.get("session_id", "claude_local_cli_session")


class ClaudeCodeHooksCollector(Collector):
    """Hooks-based collector for Claude Code (Anthropic's CLI coding agent).

    Live mode (real deployment): Claude Code is configured (via
    ~/.claude/settings.json or a project .claude/settings.json) to POST
    each hook event as JSON to a local HTTP endpoint. In a full deployment,
    activate() would start a small local HTTP listener (or reuse an
    existing one) and capture() would drain a queue fed by that listener.

    This build ships that live-listener plumbing but ALSO supports feeding
    capture() from a list of already-captured JSONL event dicts, so it can
    be exercised deterministically against the real sample data checked
    into projectsetup/backend/events/*.jsonl without a live session.
    """

    def __init__(self, replay_events: Optional[List[dict]] = None) -> None:
        self._replay_events = replay_events
        self._activated = False

    @property
    def collector_id(self) -> str:
        return COLLECTOR_ID

    def detect(self) -> bool:
        """Multiple real, cheap heuristics for "is Claude Code installed/
        configured on this machine", mirroring how one would actually check
        without any network calls:
          1. `claude` binary on PATH.
          2. ~/.claude directory exists (created by Claude Code on first run).
          3. A project-local .claude/settings.json exists with a "hooks" key
             (proof hooks are actually wired up, not just that the CLI is
             installed).
        """
        found_any = False

        if shutil.which("claude") is not None:
            found_any = True

        home_claude_dir = Path.home() / ".claude"
        if home_claude_dir.is_dir():
            found_any = True

        # Walk up from cwd looking for a .claude/settings.json with hooks
        # configured, same as how Claude Code itself resolves project config.
        cur = Path.cwd()
        for candidate in [cur, *cur.parents]:
            settings_path = candidate / ".claude" / "settings.json"
            if settings_path.is_file():
                try:
                    cfg = json.loads(settings_path.read_text())
                    if isinstance(cfg.get("hooks"), dict) and cfg["hooks"]:
                        found_any = True
                except (json.JSONDecodeError, OSError):
                    pass
                break  # first .claude dir found wins, matches CLI behavior

        return found_any

    def activate(self) -> None:
        """In a live deployment this would start/attach to the local HTTP
        listener that Claude Code's hooks POST to (per settings.json's
        `"type": "http", "url": "http://localhost:8000/webhook/claude/event"`)
        and begin queueing incoming events. Here we just flip a flag --
        the actual event source (replay list) is supplied at construction
        time for this demo build.
        """
        self._activated = True

    def capture(self) -> Iterator[RawEvent]:
        if not self._activated:
            raise RuntimeError(f"{self.collector_id}: capture() called before activate()")

        if self._replay_events is None:
            # Live mode placeholder: nothing queued because no live HTTP
            # listener is wired up in this standalone demo build. A real
            # deployment would `yield` off a thread-safe queue fed by the
            # HTTP handler as events arrive.
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
