"""
Antigravity Local Hooks (VS Code / IDE-extension-side) collector.

Grounded in the real, empirical test performed for this project. Primary
sources (read-only, never modified):

  Antigravity_LocalHooks/RESULTS.md -- full write-up: the `signal-local-hooks`
    VS Code extension (originally built/verified against Copilot,
    ../Copilot_LocalHooks/) sideloaded into Antigravity IDE v2.4.3
    (extension-host version 1.107.0) via a real "Install from VSIX" flow,
    after a genuine engine-compatibility rejection required bumping
    engines.vscode + the extension's own version.
  Antigravity_LocalHooks/captured_ide_events.jsonl -- 119 real captured
    events from a live session: activation/deactivation, document
    open/close/change, diagnostics change, fs-watcher create/delete,
    terminal open/close, one environment_snapshot, and 4
    undo_detected_unmatched events.
  Antigravity_LocalHooks/test_project/, vsix_rebuild/,
  relaxed-engine-build/ -- the extension source/build artifacts used to
    produce the working v0.0.2 .vsix (referenced for context; this
    collector treats the extension as an already-installed, already-firing
    black box exactly like antigravity_hooks.py treats hooks.json, it does
    not attempt to build or install the .vsix itself).

THIS IS A GENUINELY DIFFERENT MECHANISM FROM antigravity_hooks.py, NOT A
DUPLICATE -- confirmed directly by comparing the two real captured event
vocabularies:
  - antigravity_hooks.py (Antigravity/RESULTS.md) captures Antigravity's
    OWN agent-loop lifecycle via command-type subprocess hooks configured
    in hooks.json: PreToolUse/PostToolUse/PreInvocation/PostInvocation/
    Stop, each carrying conversationId/stepIdx/modelName/toolCall -- this
    is Antigravity reporting on itself.
  - antigravity_local_hooks.py (this file) captures generic VS-Code-host
    IDE activity via the standard vscode extension API (onDidChange*
    listeners, a chokidar-style FileSystemWatcher, terminal open/close
    events) -- these events fire for ANY IDE activity, agent-driven or
    not, and carry zero Antigravity-specific fields (no conversationId,
    no toolCall, no modelName) except where the extension's own
    environment_snapshot enumerates installed extensions
    (all_extension_ids includes "google.antigravity" etc., confirmed real
    in captured_ide_events.jsonl) to detect Antigravity's presence
    indirectly.
  Different transport (in-process VS Code extension API vs. an external
  subprocess reading stdin/stdout JSON), different trigger surface (any
  editor/terminal/fs activity vs. specifically Antigravity's own agent
  loop), different payload shape, different installation mechanism
  (.vsix sideload vs. a JSON config file) -- not the same collector tested
  differently. Confirmed non-redundant; implemented as a separate file.

REAL QUIRK CARRIED FORWARD, NOT SILENTLY "FIXED": RESULTS.md section 3/6
confirms the extension's Tab-key accept/undo heuristic (built for
Copilot's ghost-text inline-completion UX) never fires an "accept" against
Antigravity -- only 4 undo_detected_unmatched events, because Antigravity
has no vscode.InlineCompletionItemProvider-style suggestion UX at all
(its AI surface is chat/agent-panel + explicit tool calls, consistent with
every other Antigravity collector in this series). This collector does
NOT try to reinterpret or suppress those unmatched-undo events -- they are
forwarded verbatim inside raw_payload exactly as captured, since
"never accepted, so undo never matches" is itself real, correctly-captured
signal about this product's UX model, not noise to filter out.

REAL QUIRK #2: RESULTS.md section 4 confirms the extension's
knownAiExtensionIds() detection list is hardcoded from the Copilot test
(GitHub.copilot, Continue.continue, Codeium.codeium, etc.) and was NEVER
updated to recognize google.antigravity* -- so detected_ai_extensions
inside a real environment_snapshot payload comes back all `false` even
though all_extension_ids correctly lists google.antigravity,
google.antigravity-code-executor, etc. This collector does not patch or
reclassify that field -- raw_payload passes it through exactly as the
real extension emits it, per the framework's no-normalization constraint.
This is flagged here (and should be flagged to whoever maintains the
extension) as a one-line fix in the EXTENSION itself, not something this
Python collector should paper over.
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

AGENT_NAME = "antigravity"
COLLECTOR_ID = "antigravity.local_hooks"

# Real captured event `type` values (Antigravity_LocalHooks/captured_ide_events.jsonl,
# 119 real events, cross-checked against the extension's own event vocabulary
# which is shared with the original Copilot-side build). Not an exhaustive
# guess -- these are exactly what fired in the real capture.
KNOWN_EVENT_TYPES = (
    "activation", "deactivation", "environment_snapshot",
    "onDidChangeTextDocument", "onDidChangeDiagnostics",
    "onDidOpenTextDocument", "onDidCloseTextDocument",
    "fsWatcher_create", "fsWatcher_delete",
    "onDidOpenTerminal", "onDidCloseTerminal",
    "undo_detected_unmatched",
)

# Confirmed real extension identity (Antigravity_LocalHooks/RESULTS.md
# section 1 error message: "Unable to install extension
# 'signal-collect-verification.signal-local-hooks'").
EXTENSION_ID = "signal-collect-verification.signal-local-hooks"

# Confirmed real Antigravity-specific extension IDs (captured verbatim
# inside a real environment_snapshot's all_extension_ids array,
# RESULTS.md section 4) -- useful for a Python-side detect() heuristic
# even though the extension's OWN in-process knownAiExtensionIds() list
# doesn't yet recognize them (see module docstring REAL QUIRK #2).
ANTIGRAVITY_EXTENSION_ID_PREFIXES = ("google.antigravity",)


def _find_vscode_like_config_dirs() -> List[Path]:
    """Real, cheap filesystem check for a plausible VS-Code-fork user data
    dir that could hold a sideloaded extension. Antigravity_ClaimVerification/
    SUMMARY.md (Claim 1) directly confirmed Antigravity's real state does
    NOT live under a standard `User/globalStorage/state.vscdb` the way a
    generic VS Code fork would -- `~/Library/Application Support/Antigravity/`
    on this machine has no `User/` subfolder at all. So this collector does
    NOT assume a standard VS Code extensions-on-disk layout is where to
    look; instead it looks for the one thing actually confirmed real: the
    Antigravity.app bundle itself, which is what hosts the extension host
    that RESULTS.md's real VSIX install targeted.
    """
    candidates = []
    mac_app = Path("/Applications/Antigravity.app")
    if mac_app.is_dir():
        candidates.append(mac_app)
    return candidates


def _load_jsonl(path: Path) -> List[dict]:
    events: List[dict] = []
    if not path.is_file():
        return events
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return events


class AntigravityLocalHooksCollector(Collector):
    """Local (in-IDE, VS-Code-extension-based) activity collector for
    Antigravity, distinct from antigravity_hooks.py's subprocess-hook
    mechanism -- see module docstring for the confirmed non-redundancy
    argument.

    Live mode (real deployment): this collector does not itself run
    inside the VS Code extension host (that's a TypeScript/JS runtime,
    out of scope for a pure-stdlib Python collector) -- instead, exactly
    like antigravity_hooks.py's live-mode placeholder, activate() would
    attach to whatever local sink the real signal-local-hooks extension
    writes to (Antigravity_LocalHooks/RESULTS.md doesn't specify a fixed
    sink path for a production deployment; the real test captured to a
    JSONL file for evidence purposes). This build supports feeding
    capture() from that already-captured JSONL
    (Antigravity_LocalHooks/captured_ide_events.jsonl) to exercise it
    deterministically against real sample data, same pattern as every
    other collector in this file set.
    """

    def __init__(self, replay_path: Optional[Path] = None) -> None:
        self._replay_path = replay_path
        self._activated = False

    @property
    def collector_id(self) -> str:
        return COLLECTOR_ID

    def detect(self) -> bool:
        """Real, cheap heuristics for "could the signal-local-hooks
        extension plausibly be running here":
          1. Antigravity.app itself is installed (the real VSIX-install
             target confirmed in RESULTS.md section 1) -- without the
             host app there is no extension host to sideload into.
          2. `agy` on PATH as a weaker secondary signal, same as the other
             Antigravity collectors' shared heuristic (confirmed a
             separate but related product, Antigravity_OTel/RESULTS.md
             section 0).
        Does NOT check for a `state.vscdb` or any standard VS-Code-fork
        storage path -- Antigravity_ClaimVerification/SUMMARY.md Claim 1
        directly confirmed that assumption is FALSE for this product (see
        _find_vscode_like_config_dirs() docstring).
        """
        if _find_vscode_like_config_dirs():
            return True
        if shutil.which("agy") is not None:
            return True
        return False

    def activate(self) -> None:
        """Live mode: no in-process hook into the VS Code extension host
        is wired up in this standalone Python build (documented, not
        silently pretended). Replay mode: no-op besides marking ready."""
        if self._replay_path is None:
            print(
                f"[{self.collector_id}] NOTE: live mode requires attaching "
                f"to the signal-local-hooks VS Code extension's own event "
                f"sink from inside the extension host process (TypeScript, "
                f"see Antigravity_LocalHooks/vsix_rebuild/); this Python "
                f"collector has no such live listener wired up standalone. "
                f"Use replay_path= to exercise capture() against real "
                f"captured_ide_events.jsonl data.",
                file=_sys.stderr,
            )
        self._activated = True

    def capture(self) -> Iterator[RawEvent]:
        if not self._activated:
            raise RuntimeError(f"{self.collector_id}: capture() called before activate()")

        if self._replay_path is None:
            # Live mode placeholder -- see activate()'s note.
            return

        for payload in _load_jsonl(self._replay_path):
            yield RawEvent(
                timestamp=RawEvent.now_iso(),
                collector_id=self.collector_id,
                agent=AGENT_NAME,
                raw_payload=payload,  # untouched, exact captured shape
            )

    def deactivate(self) -> None:
        self._activated = False


if __name__ == "__main__":
    collector = AntigravityLocalHooksCollector()
    print(f"detect() -> {collector.detect()}")

    real_dir = Path(__file__).resolve().parents[3].parent / "Antigravity_LocalHooks"
    replay_file = real_dir / "captured_ide_events.jsonl"

    print(f"\nSIMULATED FROM CAPTURED DATA ({replay_file}):")
    if replay_file.is_file():
        replay_collector = AntigravityLocalHooksCollector(replay_path=replay_file)
        replay_collector.activate()
        events = list(replay_collector.capture())
        print(f"  {len(events)} RawEvent(s) total (RESULTS.md reports 119 real captured events)")
        type_counts: dict = {}
        for ev in events:
            t = ev.raw_payload.get("type", "?")
            type_counts[t] = type_counts.get(t, 0) + 1
        print(f"  event type breakdown: {type_counts}")
        for ev in events[:5]:
            print(f"  {ev}")
        replay_collector.deactivate()
    else:
        print(f"  no real captured file found at {replay_file}, skipping replay demo")
