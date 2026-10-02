"""
GitHub Copilot CLI Hooks collector.

Grounded entirely in the real, empirical capture performed for this project
-- NOT vendor documentation, which testing found to be wrong or silent about
the load-bearing details below. Primary sources (read-only, never modified):

  Copilot/RESULTS.md                 -- the full write-up this file is built from
  Copilot/config/hooks.json          -- exact working hook registration used
  Copilot/captured_events.jsonl      -- 40 raw captured HTTP POST bodies
  Copilot_LocalHooks/RESULTS.md      -- VS Code extension / environment_snapshot evidence
  Copilot_MCP_Proxy/RESULTS.md       -- MCP tool-call payload shapes
  Copilot_VSCode_CLI/RESULTS.md      -- env-var inheritance caveat for GUI-launched VS Code

This collector performs NO normalization: whichever of the three real raw
shapes Copilot happens to send (see below) passes through untouched inside
`raw_payload`.
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
from collectors.copilot.hook_settings import (  # noqa: E402
    apply_required_env,
    install_copilot_http_hooks,
    restore_env,
    restore_hooks_file,
)
from collectors.live_http import HookHTTPServer, LiveEventFeed  # noqa: E402
from core import Collector, RawEvent

AGENT_NAME = "copilot"
COLLECTOR_ID = "copilot.hooks"
WEBHOOK_PATH = "/webhook/copilot/event"
_DEFAULT_HOST = "127.0.0.1"
_DEFAULT_PORT = 8000

# The 14 documented event names Copilot's CLI accepts registrations for, in
# EITHER casing (per Copilot/config/hooks.json, the exact config used in the
# real capture). Do not assume only one casing needs to be registered --
# see the casing-routing note on activate() below, confirmed by
# Copilot/RESULTS.md section 4.
KNOWN_HOOK_EVENTS = (
    "SessionStart", "sessionStart",
    "SessionEnd", "sessionEnd",
    "UserPromptSubmitted", "userPromptSubmitted",
    "UserPromptTransformed", "userPromptTransformed",
    "PreToolUse", "preToolUse",
    "PermissionRequest", "permissionRequest",
    "PostToolUse", "postToolUse",
    "PostToolUseFailure", "postToolUseFailure",
    "AgentStop", "agentStop",
    "SubagentStart", "subagentStart",
    "SubagentStop", "subagentStop",
    "ErrorOccurred", "errorOccurred",
    "PreCompact", "preCompact",
    "Notification", "notification",
)

# --- Real, empirically-required env vars (Copilot/RESULTS.md section 5-6) --
#
# Confirmed by reading the CLI's bundled app.js directly -- neither of these
# is documented anywhere in `copilot help config` / `copilot help hooks`
# (the latter doesn't even exist as a help topic). Get either wrong and
# hooks fail COMPLETELY SILENTLY: no error, no warning, zero events, and the
# CLI otherwise runs and appears to succeed normally. This produced a false
# "Copilot doesn't support hooks" result on the first attempt in testing
# (see copilot_run_1.log, which used the wrong value below and captured 0
# events).
#
#   COPILOT_HOOK_ALLOW_LOCALHOST        must be the literal string "1"
#       (app.js: allowLocalhost: process.env.COPILOT_HOOK_ALLOW_LOCALHOST === "1")
#       Required for any `http`-type hook target pointing at localhost/127.0.0.1.
#
#   GITHUB_COPILOT_PROMPT_MODE_REPO_HOOKS   must be the literal string "true"
#       (app.js: process.env.GITHUB_COPILOT_PROMPT_MODE_REPO_HOOKS === "true")
#       NOTE the inconsistency with the var above: this one requires "true",
#       not "1". Setting it to "1" (an easy, reasonable-looking mistake) is
#       the single biggest trap found in testing -- it fails silently.
REQUIRED_ENV_VARS = {
    "COPILOT_HOOK_ALLOW_LOCALHOST": "1",
    "GITHUB_COPILOT_PROMPT_MODE_REPO_HOOKS": "true",
}

# VS Code injects this into the embedded CLI's environment automatically
# when Copilot Chat is running inside VS Code -- a reliable "running under
# VS Code" signal (Copilot_LocalHooks/RESULTS.md).
VSCODE_INTEGRATION_ENV_VAR = "GITHUB_COPILOT_INTEGRATION_ID"

# The VS Code extension that actually serves both chat and hook-eligible
# completions in the real environment tested (Copilot_LocalHooks/RESULTS.md
# section e: GitHub.copilot-chat was present; the separate GitHub.copilot
# completions-only extension was NOT installed there, so don't gate solely
# on GitHub.copilot).
VSCODE_EXTENSION_IDS = ("GitHub.copilot-chat", "GitHub.copilot")


def extract_shape(payload: dict) -> str:
    """Classify which of the THREE real payload shapes a given Copilot hook
    delivery uses, per Copilot/RESULTS.md section 2. Dispatch is by shape,
    not by a single assumed casing per event name -- confirmed necessary
    because behavior is per-event:
      - Session/turn lifecycle events (SessionStart/sessionStart, etc.) fire
        in BOTH casings simultaneously, as two different payload shapes.
      - PreToolUse/PostToolUse fire ONLY under PascalCase registration in
        this version (the camelCase registration is silently accepted but
        never invoked).
      - permissionRequest fires ONLY under camelCase registration, in its
        own third shape that matches neither of the other two.
    """
    if "hookName" in payload:
        # Shape C: permissionRequest's own shape (hookName, sessionId,
        # numeric timestamp, toolName/toolInput -- camelCase tool fields,
        # unlike PreToolUse's snake_case tool_name/tool_input).
        return "permission_request_shape"
    if "hook_event_name" in payload and "session_id" in payload:
        # Shape B: PascalCase "compat" shape -- hook_event_name, session_id,
        # ISO-8601 string timestamp. Fires for PascalCase-registered hooks.
        return "pascalcase_compat_shape"
    if "sessionId" in payload:
        # Shape A: camelCase "native" shape -- no hook_event_name field at
        # all; identity must be inferred from which URL/hook fired. Numeric
        # (unix ms) timestamp.
        return "camelcase_native_shape"
    return "unknown_shape"


def extract_event_identity(payload: dict) -> str:
    """Best-effort event name recovery across all three shapes. Shape B
    carries hook_event_name directly; Shape C always has the literal string
    "permissionRequest" under hookName; Shape A has NO event-name field at
    all (confirmed in RESULTS.md: "the event's identity must be inferred
    from which URL/hook fired and the field shape") -- so for Shape A this
    falls back to structural guessing and may legitimately return
    "unknown" for genuinely ambiguous cases. This is a best-effort label for
    human/log purposes only; raw_payload itself is never altered."""
    if "hook_event_name" in payload:
        return payload["hook_event_name"]
    if "hookName" in payload:
        return payload["hookName"]
    # Shape A structural guesses, in priority order.
    if "transformedPrompt" in payload:
        return "userPromptTransformed"
    if "prompt" in payload:
        return "userPromptSubmitted"
    if "initialPrompt" in payload:
        return "sessionStart"
    if "agentDisplayName" in payload or "agentName" in payload:
        return "subagentStop"
    if "stopReason" in payload:
        return "agentStop"
    if "reason" in payload:
        return "sessionEnd"
    return "unknown"


class CopilotHooksCollector(Collector):
    """Hooks-based collector for the GitHub Copilot CLI.

    Live mode (real deployment): Copilot CLI is configured (via a repo-level
    `.github/hooks/hooks.json`, confirmed the primary load path via
    `getHooksDir()` in the CLI's app.js) to POST each hook event as JSON to
    a local HTTP endpoint. As with the Claude Code / Cursor collectors, a
    full deployment's activate() would start/attach to that listener and
    capture() would drain a queue it fills.

    Live: attach `/webhook/copilot/event`, set REQUIRED_ENV_VARS, merge
    `.github/hooks/hooks.json` HTTP hooks for both casings. Replay: iterate
    a supplied list. See LIVE_HOOKS.md FR-H14.
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
        self._env_backup: Optional[dict] = None
        self._bound_port = listen_port

    @property
    def listen_port(self) -> int:
        return self._bound_port

    @property
    def collector_id(self) -> str:
        return COLLECTOR_ID

    def detect(self) -> bool:
        """Real, cheap heuristics for "is Copilot CLI/extension present on
        this machine", per Copilot/RESULTS.md and Copilot_LocalHooks/RESULTS.md:
          1. `copilot` binary on PATH.
          2. ~/.copilot directory exists (personal hooks/config live there
             per app.js's additional hook load paths).
          3. A project-local `.github/hooks/hooks.json` exists (proof hooks
             are actually wired up, matching the real load path).
          4. VS Code extension dirs for GitHub.copilot-chat / GitHub.copilot
             present under ~/.vscode/extensions (best-effort signal only --
             we do not shell out to `code --list-extensions`, which would
             not be side-effect-free/cheap).
        """
        found_any = False

        if shutil.which("copilot") is not None:
            found_any = True

        home_copilot_dir = Path.home() / ".copilot"
        if home_copilot_dir.is_dir():
            found_any = True

        cur = Path.cwd()
        for candidate in [cur, *cur.parents]:
            hooks_path = candidate / ".github" / "hooks" / "hooks.json"
            if hooks_path.is_file():
                try:
                    cfg = json.loads(hooks_path.read_text())
                    if isinstance(cfg.get("hooks"), dict) and cfg["hooks"]:
                        found_any = True
                except (json.JSONDecodeError, OSError):
                    pass
                break

        vscode_ext_dir = Path.home() / ".vscode" / "extensions"
        if vscode_ext_dir.is_dir():
            try:
                for entry in vscode_ext_dir.iterdir():
                    name = entry.name.lower()
                    if any(ext_id.lower() in name for ext_id in VSCODE_EXTENSION_IDS):
                        found_any = True
                        break
            except OSError:
                pass

        return found_any

    def activate(self) -> None:
        """Replay: warn if required env is wrong. Live: set env, listen, merge hooks."""
        self._activated = True
        if self._replay_events is not None:
            self._warn_env()
            return
        self._start_live()

    def _warn_env(self) -> None:
        for var, required_value in REQUIRED_ENV_VARS.items():
            actual = os.environ.get(var)
            if actual == required_value:
                continue
            print(
                f"[{self.collector_id}] WARNING: env var {var!r} is "
                f"{actual!r}, expected exact string {required_value!r}. "
                f"In live deployment this makes Copilot hooks fail "
                f"COMPLETELY SILENTLY (no error, zero events) -- "
                f"confirmed in Copilot/RESULTS.md section 5-6.",
                file=_sys.stderr,
            )

    def _start_live(self) -> None:
        self._env_backup = apply_required_env(REQUIRED_ENV_VARS)
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
        path = self._hooks_path or (
            Path.cwd() / ".github" / "hooks" / "hooks.json"
        )
        url = f"http://{self._listen_host}:{self._bound_port}{WEBHOOK_PATH}"
        self._hooks_path = path
        self._hooks_backup = install_copilot_http_hooks(
            path, url, KNOWN_HOOK_EVENTS
        )

    def capture(self) -> Iterator[RawEvent]:
        if not self._activated:
            raise RuntimeError(f"{self.collector_id}: capture() called before activate()")
        source = self._event_source()
        if source is None:
            return
        # PostToolUse does not reliably indicate tool failure (RESULTS.md §5).
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
        server = self._server
        self._server = None
        if server is not None:
            server.stop()
        self._restore_hooks()
        self._restore_env()

    def _restore_hooks(self) -> None:
        if self._replay_events is not None:
            return
        path = self._hooks_path
        if path is None:
            return
        restore_hooks_file(path, self._hooks_backup)
        self._hooks_backup = None

    def _restore_env(self) -> None:
        backup = self._env_backup
        self._env_backup = None
        if backup is not None:
            restore_env(backup)
