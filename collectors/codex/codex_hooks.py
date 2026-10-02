"""
Codex CLI Hooks collector.

Grounded entirely in the real, empirical capture performed for this project
-- NOT vendor docs, which testing found incomplete (an oversimplified
"common fields" claim) and, in one place, silent about the single biggest
operational trap: hooks fail with zero warning when trust hasn't been
granted. Primary sources (read-only, never modified):

  Codex/RESULTS.md                          -- the full write-up this file is built from
  Codex/config/hooks.json                   -- exact working hook registration (plain-string commands)
  Codex/captured_events.jsonl               -- raw captured HTTP POST bodies, real per-event field shapes
  Codex_GapClosure/RESULTS.md               -- confirms trust mechanism: hooks.state."<key>".trusted_hash
                                                in config.toml, and the separate directory-trust gate
  Codex_GapClosure/config.toml.before_trust_attempt -- real config.toml showing directory
                                                trust_level entries with NO hooks.state section
                                                (trust was never persisted; bypass flag used instead)
  Codex_LocalHooks/RESULTS.md               -- IDE-side corroboration (shared CODEX_HOME)

This collector performs NO normalization: the real per-event JSON Codex
posts to a command-type hook's forwarder passes through untouched inside
`raw_payload`.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Iterator, List, Optional

import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parents[2]))
from collectors.codex.hook_settings import (  # noqa: E402
    forwarder_command,
    install_codex_command_hooks,
    restore_hooks_file,
)
from collectors.live_http import HookHTTPServer, LiveEventFeed  # noqa: E402
from core import Collector, RawEvent

AGENT_NAME = "codex"
COLLECTOR_ID = "codex.hooks"
WEBHOOK_PATH = "/webhook/codex/event"
_DEFAULT_HOST = "127.0.0.1"
_DEFAULT_PORT = 8000

# The 11 documented hook event names (Codex/RESULTS.md section 3). Not all
# 11 could be triggered live in testing (PermissionRequest/PreCompact/
# PostCompact were not observed live -- see capture()'s docstring), but all
# 11 are real, confirmed to exist via the CLI binary's own embedded
# draft-07 JSON Schemas.
KNOWN_HOOK_EVENTS = (
    "SessionStart", "UserPromptSubmit", "PreToolUse", "PostToolUse",
    "SubagentStart", "SubagentStop", "Stop", "SessionEnd",
    "PermissionRequest", "PreCompact", "PostCompact",
)

# Fields the vendor brief documents as "always present" on every hook --
# confirmed WRONG as a blanket claim (Codex/RESULTS.md section 4). Kept
# here only as a reference constant for callers who want to know what NOT
# to assume is always present; this collector never enforces or fabricates
# these fields.
DOCUMENTED_BUT_NOT_ALWAYS_PRESENT_FIELDS = ("model", "permission_mode", "turn_id")

# Confirmed real gap, cited to the exact event type where it was found
# (Codex/RESULTS.md section 4 table + the real captured SessionEnd payload):
#   - `model`           absent from SessionEnd
#   - `permission_mode` absent from SessionEnd, PreCompact, PostCompact
#   - `turn_id`         absent from SessionStart, SessionEnd
# Example real SessionEnd capture (Codex/captured_events.jsonl-shaped):
#   {"session_id": "...", "transcript_path": "...", "cwd": "...",
#    "hook_event_name": "SessionEnd", "reason": "other"}
# -- no model/permission_mode/turn_id keys at all.


def find_codex_home() -> Path:
    return Path.home() / ".codex"


def check_hook_trust_granted(codex_home: Path) -> bool:
    """Real evidence of hook trust being granted, per Codex_GapClosure/RESULTS.md:
    persisted trust is written as `hooks.state."<key>".trusted_hash` inside
    `~/.codex/config.toml` by the interactive `/hooks` TUI flow -- confirmed
    via the CLI binary's own strings. There is NO other way to check this
    file-system-side (no dedicated hooks-trust-granting RPC/CLI subcommand
    exists either, per the same source). We do a cheap textual scan for the
    `hooks.state` table header rather than a full TOML parse (stdlib tomllib
    is fine here -- Python 3.11+ -- but a substring check is sufficient and
    avoids taking on a fragile custom TOML section-parser for one field).
    """
    config_path = codex_home / "config.toml"
    if not config_path.is_file():
        return False
    try:
        text = config_path.read_text()
    except OSError:
        return False
    return "hooks.state." in text or "[hooks.state" in text


class CodexTrustNotGrantedError(RuntimeError):
    """Raised by activate() when no evidence of hook trust exists.

    CRITICAL, confirmed in testing (Codex/RESULTS.md section 1): Codex hooks
    require one-time trust approval (via the interactive `/hooks` TUI, which
    persists `hooks.state."<key>".trusted_hash` into ~/.codex/config.toml),
    or the CLI flag `--dangerously-bypass-hook-trust` on every invocation.
    Without either, `codex exec` runs to completion NORMALLY with ZERO hook
    output, ZERO captured events, and ZERO warning or error of any kind --
    "the run completed normally... nothing in stdout/stderr told us hooks
    were being ignored" (Codex/RESULTS.md section 1). This is worse than the
    Copilot/Antigravity silent-failure classes because it produces no
    signal whatsoever, not even a misleading one. We deliberately raise
    loudly here instead of silently returning zero events, so a caller of
    this framework gets an actionable error instead of a false "Codex sent
    no events" read.
    """


class CodexHooksCollector(Collector):
    """Hooks-based collector for the Codex CLI (OpenAI).

    Live mode (real deployment): Codex is configured via `~/.codex/hooks.json`
    (global; confirmed to work on first attempt without needing a per-project
    `.codex/hooks.json` fallback) with command-type handlers whose `command`
    field is a plain shell string (see _validate_hooks_config_shape below).
    Each handler is invoked as `$SHELL -lc "<command>"` with the event JSON
    delivered via the handler's own mechanism (a forwarder script in the real
    test, POSTing to a local HTTP capture server). A full deployment's
    activate() would start/attach to that listener; capture() would drain a
    queue it fills.

    This build supports feeding capture() from already-captured JSONL event
    dicts (Codex/captured_events.jsonl and friends) to exercise it
    deterministically against real sample data.
    """

    def __init__(
        self,
        replay_events: Optional[List[dict]] = None,
        require_trust: bool = True,
        hooks_path: Optional[Path] = None,
        listen_host: str = _DEFAULT_HOST,
        listen_port: int = _DEFAULT_PORT,
        install_hooks: Optional[bool] = None,
        codex_home: Optional[Path] = None,
    ) -> None:
        self._replay_events = replay_events
        self._activated = False
        self._require_trust = require_trust
        self._hooks_path = hooks_path
        self._listen_host = listen_host
        self._listen_port = listen_port
        self._install_hooks = install_hooks
        self._codex_home = codex_home
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
        """Real, cheap heuristics for "is Codex CLI present/configured on
        this machine", per Codex/RESULTS.md:
          1. `codex` binary on PATH.
          2. ~/.codex directory exists (CODEX_HOME; confirmed real config
             home for both the CLI and the IDE extension, which "launches
             it... against the exact same CODEX_HOME the CLI uses" per
             Codex_LocalHooks/RESULTS.md).
          3. ~/.codex/hooks.json exists with the real plain-string-command
             schema (proof hooks are actually configured, not just that the
             CLI is installed).
        """
        found_any = False

        if shutil.which("codex") is not None:
            found_any = True

        codex_home = find_codex_home()
        if codex_home.is_dir():
            found_any = True

        hooks_path = codex_home / "hooks.json"
        if hooks_path.is_file():
            try:
                cfg = json.loads(hooks_path.read_text())
                if isinstance(cfg.get("hooks"), dict) and cfg["hooks"]:
                    found_any = True
            except (json.JSONDecodeError, OSError):
                pass

        return found_any

    def activate(self) -> None:
        """CRITICAL (see CodexTrustNotGrantedError docstring): checks for
        real evidence of hook trust before activating, and raises a clear,
        actionable error if it's missing -- instead of activating
        successfully and then silently capturing nothing, which is exactly
        the trap the real CLI itself falls into (see Codex/RESULTS.md
        section 1). Set require_trust=False at construction time to opt out
        of this check (e.g. if you know --dangerously-bypass-hook-trust is
        being used out-of-band for every invocation and there is
        deliberately no persisted trust state to find).
        """
        if self._require_trust:
            home = self._codex_home or find_codex_home()
            if not check_hook_trust_granted(home):
                raise CodexTrustNotGrantedError(
                    f"{self.collector_id}: no evidence of hook trust found "
                    f"in {home / 'config.toml'} (expected a "
                    f'"hooks.state.<key>.trusted_hash" entry, written by the '
                    f"interactive `/hooks` TUI approval flow). Without this, "
                    f"real Codex hooks fail COMPLETELY SILENTLY -- zero "
                    f"events, zero warnings (confirmed in Codex/RESULTS.md "
                    f"section 1). Fix: either run the interactive `/hooks` "
                    f"approval flow once for this CODEX_HOME, or pass "
                    f"--dangerously-bypass-hook-trust to every `codex exec` "
                    f"invocation and construct this collector with "
                    f"require_trust=False to acknowledge that choice "
                    f"explicitly."
                )
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
        home = self._codex_home or find_codex_home()
        path = self._hooks_path or (home / "hooks.json")
        url = f"http://{self._listen_host}:{self._bound_port}{WEBHOOK_PATH}"
        self._hooks_path = path
        self._hooks_backup = install_codex_command_hooks(
            path, forwarder_command(url), KNOWN_HOOK_EVENTS
        )

    def capture(self) -> Iterator[RawEvent]:
        if not self._activated:
            raise RuntimeError(f"{self.collector_id}: capture() called before activate()")

        source = self._event_source()
        if source is None:
            return

        for payload in source:
            # DOCUMENTED, CONFIRMED GAPS -- not bugs to "fix" here, and not
            # papered over with fabricated defaults:
            #
            #  1. model / permission_mode / turn_id are each confirmed
            #     MISSING from at least one real event type, despite the
            #     vendor brief calling them common/always-present fields.
            #     Concretely: `model` is missing from SessionEnd;
            #     `permission_mode` is missing from SessionEnd, PreCompact,
            #     and PostCompact; `turn_id` is missing from SessionStart
            #     and SessionEnd. Confirmed by both the CLI binary's own
            #     embedded JSON Schema `required` arrays and live captures
            #     (Codex/RESULTS.md section 4). We do NOT inject these keys
            #     with a placeholder value if absent -- raw_payload must
            #     stay exactly what Codex sent.
            #
            #  2. tool_response (on PostToolUse) has NO exit-code/success
            #     field at all -- the embedded schema declares it as `true`
            #     (arbitrary JSON), and real captures confirm a failing
            #     command (`false`, exit code 1) produces tool_response ==
            #     "" (empty string), which is byte-for-byte indistinguishable
            #     from a successful command that produced no output
            #     (Codex/RESULTS.md section 5). This is the same class of
            #     data-quality issue flagged in the Copilot collector's
            #     PostToolUse handling -- we do not invent a
            #     success/failure inference here that the real payload
            #     doesn't support.
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

    def _restore_hooks(self) -> None:
        if self._replay_events is not None:
            return
        path = self._hooks_path
        if path is None:
            return
        restore_hooks_file(path, self._hooks_backup)
        self._hooks_backup = None
