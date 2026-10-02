"""
Codex Local Hooks (VS Code IDE extension observer) collector.

DISTINCT MECHANISM FROM codex_hooks.py -- do not confuse the two. codex_hooks.py
captures Codex's own native `hooks.json` command-forwarder mechanism
(SessionStart/PreToolUse/etc. POSTed by the `codex` CLI binary itself, or by
the IDE's bundled `app-server` binary, when hook trust is granted). This
collector instead captures a SEPARATE signal: a third-party VS Code
*observer* extension's own editor-lifecycle events (text changes, file
watcher events, diagnostics, and a Tab-key ghost-text accept/undo heuristic)
-- entirely outside of and independent from Codex's hooks mechanism. The two
are complementary, not redundant: this collector can see IDE-only signals
(inline-completion ghost-text accept/undo) codex_hooks.py has no visibility
into at all, and vice versa (this collector cannot see tool_input/
tool_response payload content the way Hooks can). Primary sources (read-only,
never modified):

  Codex_LocalHooks/RESULTS.md              -- the full write-up this file is built from
  Codex_LocalHooks/captured_ide_events.jsonl
                                            -- real captured observer-extension events,
                                               exact per-event-type field shapes
  Codex_GapClosure/RESULTS.md              -- authoritative follow-up/correction source
                                               (task 1: proves the IDE's zero-hooks behavior
                                               is the same silent hook-trust gate found in
                                               Codex/RESULTS.md, reached via a real, binary-
                                               strings-discovered `bypass_hook_trust` JSON-RPC
                                               config override -- NOT a categorically
                                               hook-incapable code path)
  codex_hooks.py (this framework)          -- the native hooks.json mechanism this collector
                                               is explicitly NOT duplicating

WHY THIS IS A SEPARATE COLLECTOR, NOT A DUPLICATE OF codex_hooks.py:
Codex_LocalHooks/RESULTS.md section 0 item 4 confirms the observer
extension's Tab-key accept/undo heuristic fired cleanly (`probable_accept` ->
`accept_correlation` verdict `probable_real_ai_accept` -> `probable_undo`),
proving Codex's IDE extension implements classic ghost-text inline
completions -- a UX surface neither the CLI nor codex_hooks.py's own
hooks.json forwarder mechanism has any way to observe (hooks.json only fires
on SessionStart/PreToolUse/etc. turn-lifecycle events, never on
per-keystroke editor UI state). This collector's captured_ide_events.jsonl
event vocabulary (onDidChangeTextDocument, onDidSaveTextDocument,
fsWatcher_*, diagnostics, activation, environment_snapshot) has zero overlap
with codex_hooks.py's KNOWN_HOOK_EVENTS vocabulary -- confirming these are
genuinely different instrumentation points, not the same mechanism tested
twice.

This collector performs NO normalization: each captured IDE event keeps
exactly the shape Codex_LocalHooks/captured_ide_events.jsonl has, which
varies per `type` (no single common schema -- see KNOWN_IDE_EVENT_TYPES).
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Iterator, List, Optional

import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parents[2]))
from collectors.live_http import HookHTTPServer, LiveEventFeed  # noqa: E402
from core import Collector, RawEvent

AGENT_NAME = "codex"
COLLECTOR_ID = "codex.local_hooks"
WEBHOOK_PATH = "/webhook/codex/local"
_DEFAULT_HOST = "127.0.0.1"
_DEFAULT_PORT = 8000

# Real event `type` values observed in Codex_LocalHooks/captured_ide_events.jsonl
# (RESULTS.md section 2, 71 real events total). Each type has its OWN field
# shape -- there is no common envelope beyond {timestamp, type}. Kept here as
# a reference constant only; capture() never filters or requires these.
KNOWN_IDE_EVENT_TYPES = (
    "activation", "environment_snapshot",
    "onDidChangeDiagnostics", "onDidChangeTextDocument",
    "onDidSaveTextDocument", "onDidOpenTextDocument", "onDidCloseTextDocument",
    "onDidOpenTerminal", "onDidCloseTerminal",
    "fsWatcher_create", "fsWatcher_change", "fsWatcher_delete",
    "probable_accept", "accept_correlation", "probable_undo",
)

# CONFIRMED (Codex_LocalHooks/RESULTS.md section 0 item 2, root-caused by
# Codex_GapClosure/RESULTS.md task 1): the real Codex IDE extension
# (openai.chatgpt) bundles its OWN separate `codex` binary launched in
# `app-server` mode against the same CODEX_HOME as the CLI. Native
# hooks.json IS valid and present, but fires ZERO hook events in the IDE by
# default -- because the IDE-launched app-server process never sends the
# `bypass_hook_trust` config override the CLI's
# `--dangerously-bypass-hook-trust` flag provides, and (per Codex_GapClosure
# task 1) there is NO RPC method that lets a client grant/persist hook trust
# over the app-server protocol at all -- only a read-only `hooks/list`.
# This is a STRUCTURAL limitation of codex_hooks.py when pointed at the IDE
# surface, not something this observer-extension collector can work around;
# it's documented here because it's the reason this SEPARATE collector
# exists at all -- IDE-side signal has to come from somewhere else, and
# this extension-observer mechanism is that somewhere else.
IDE_HOOKS_SILENTLY_DISABLED_BY_DEFAULT = True

# CONFIRMED (Codex_LocalHooks/RESULTS.md section 0 item 3): the IDE
# extension ships a DIFFERENT, alpha-tagged Codex build
# (`codex-cli 0.146.0-alpha.3.1`) from the standalone CLI (`0.146.0`) used
# by every other Codex collector in this framework. Anyone assuming CLI and
# IDE run identical code is wrong.
IDE_BUNDLED_CODEX_BUILD_DIFFERS_FROM_CLI = True


def find_vscode_extensions_dir() -> Path:
    return Path.home() / ".vscode" / "extensions"


def check_codex_ide_extension_installed(extensions_dir: Path) -> bool:
    """Real, cheap filesystem evidence that the openai.chatgpt VS Code
    extension (Codex's IDE integration) is installed, per
    Codex_LocalHooks/RESULTS.md section 1 (`openai.chatgpt@26.721.41059`,
    installed under the standard `~/.vscode/extensions/` layout). We glob
    for the publisher.name prefix rather than requiring an exact version
    match, since version pins would make detect() brittle across upgrades.
    """
    if not extensions_dir.is_dir():
        return False
    try:
        return any(extensions_dir.glob("openai.chatgpt-*"))
    except OSError:
        return False


def check_code_cli_lists_extension() -> bool:
    """Secondary, best-effort check via the documented `code
    --list-extensions` CLI (Codex_LocalHooks/RESULTS.md section 1's own
    verification command). This is a subprocess call, so unlike the
    filesystem check above it's not free -- kept optional / best-effort and
    never required, since `code` may not be on PATH even when the extension
    is installed (e.g. Cursor/Windsurf forks, noted in RESULTS.md section 1
    as "also works in ... not separately tested here").
    """
    code_bin = shutil.which("code")
    if code_bin is None:
        return False
    try:
        result = subprocess.run(
            [code_bin, "--list-extensions"],
            capture_output=True, text=True, timeout=5,
        )
        return "openai.chatgpt" in result.stdout
    except (subprocess.SubprocessError, OSError):
        return False


class CodexLocalHooksCollector(Collector):
    """VS Code observer-extension-based collector for Codex's IDE
    integration (the `openai.chatgpt` extension), distinct from
    codex_hooks.py's native hooks.json mechanism (see module docstring).

    Live mode (real deployment): activate() would install/enable a
    dedicated observer VS Code extension (the real test used
    `signal-local-hooks`, built specifically to listen for
    onDidChangeTextDocument / onDidSaveTextDocument / fsWatcher_* /
    onDidChangeDiagnostics / terminal-open-close events, plus a Tab-key
    `editorTextFocus && inlineSuggestionVisible` context-key interception
    for ghost-text accept/undo detection), which appends every observed
    event to a local JSONL sink. capture() would then tail that sink.

    This build supports feeding capture() from already-captured JSONL
    records (Codex_LocalHooks/captured_ide_events.jsonl) to exercise it
    deterministically against real sample data, without a live VS Code
    instance or a second, purpose-built observer extension installed.
    """

    def __init__(
        self,
        replay_events: Optional[List[dict]] = None,
        listen_host: str = _DEFAULT_HOST,
        listen_port: int = _DEFAULT_PORT,
    ) -> None:
        self._replay_events = replay_events
        self._listen_host = listen_host
        self._listen_port = listen_port
        self._activated = False
        self._live_feed: Optional[LiveEventFeed] = None
        self._server: Optional[HookHTTPServer] = None
        self._bound_port = listen_port

    @property
    def listen_port(self) -> int:
        return self._bound_port

    @property
    def collector_id(self) -> str:
        return COLLECTOR_ID

    def detect(self) -> bool:
        """Real, cheap heuristics for "is Codex's VS Code IDE extension
        present on this machine", per Codex_LocalHooks/RESULTS.md:
          1. `~/.vscode/extensions/openai.chatgpt-*` directory exists
             (primary, filesystem-only, zero-subprocess signal).
          2. `code --list-extensions` (if `code` is on PATH) lists
             `openai.chatgpt` (secondary, best-effort corroboration --
             matches the exact verification command RESULTS.md section 1
             used).
        Deliberately does NOT check for the standalone `codex` CLI binary
        here (unlike every other Codex collector in this framework) --
        Codex_GapClosure/RESULTS.md confirmed the IDE bundles its own
        separate, differently-versioned `codex` binary
        (`0.146.0-alpha.3.1`) inside the extension directory itself, so
        `shutil.which("codex")` presence/absence is not evidence either way
        for this specific collector.
        """
        found_any = False

        if check_codex_ide_extension_installed(find_vscode_extensions_dir()):
            found_any = True

        if check_code_cli_lists_extension():
            found_any = True

        return found_any

    def activate(self) -> None:
        """No trust-gate precondition check here (unlike codex_hooks.py) --
        this collector's own observer-extension mechanism needs no Codex
        hook-trust state at all; it's entirely independent of Codex's
        hooks.json/config.toml. The fact that Codex's OWN native hooks
        happen to be silently disabled inside the IDE by default
        (IDE_HOOKS_SILENTLY_DISABLED_BY_DEFAULT) is exactly WHY this
        separate collector exists, not a precondition of it.
        """
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
            return
        feed = self._live_feed
        if feed is not None:
            feed.stop()


def _load_sample_ide_events(max_events: int = 8) -> List[dict]:
    """Best-effort loader for real captured IDE observer events out of
    Codex_LocalHooks/captured_ide_events.jsonl, for the __main__ demo below.
    """
    candidates = [
        Path(__file__).resolve().parents[4] / "Codex_LocalHooks" / "captured_ide_events.jsonl",
        Path("/Users/malharshinde/Desktop/SOAISEC/Project_Signal/Codex_LocalHooks/captured_ide_events.jsonl"),
    ]
    for path in candidates:
        if path.is_file():
            events = []
            try:
                with path.open() as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        events.append(json.loads(line))
                        if len(events) >= max_events:
                            break
            except (json.JSONDecodeError, OSError):
                continue
            return events
    return []


if __name__ == "__main__":
    collector = CodexLocalHooksCollector()

    print(f"[{collector.collector_id}] detect() -> {collector.detect()}")

    sample_events = _load_sample_ide_events(max_events=8)
    if sample_events:
        print(f"[{collector.collector_id}] loaded {len(sample_events)} real captured IDE "
              f"observer events from Codex_LocalHooks/captured_ide_events.jsonl")
        collector = CodexLocalHooksCollector(replay_events=sample_events)
    else:
        print(f"[{collector.collector_id}] captured_ide_events.jsonl not reachable from this "
              f"checkout -- capture() will yield nothing (no synthetic fallback).")

    collector.activate()
    print(f"[{collector.collector_id}] activate() ok")

    count = 0
    for event in collector.capture():
        count += 1
        label = "SIMULATED FROM CAPTURED DATA"
        print(f"\n--- RawEvent #{count} ({label}) ---")
        print(f"timestamp:    {event.timestamp}")
        print(f"collector_id: {event.collector_id}")
        print(f"agent:        {event.agent}")
        print(f"type:         {event.raw_payload.get('type')}")
        extra_keys = sorted(k for k in event.raw_payload.keys() if k not in ("timestamp", "type"))
        print(f"other keys:   {extra_keys}")
        if count >= 8:
            break

    collector.deactivate()
    print(f"\n[{collector.collector_id}] deactivate() ok. Total events yielded: {count}")
