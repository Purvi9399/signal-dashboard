"""
GitHub Copilot "Local Hooks" collector.

DISTINCTNESS NOTE (per task instructions): this is a genuinely DIFFERENT
mechanism from copilot_hooks.py, not a redundant near-duplicate, so it gets
its own file. The two are as different as any two collectors in this
framework:

  copilot_hooks.py (existing) -- Copilot CLI's OWN native hook system:
      the `copilot` binary itself POSTs JSON payloads to an HTTP endpoint
      registered in a repo-level `.github/hooks/hooks.json`, for CLI
      session/tool-call lifecycle events (SessionStart, PreToolUse, etc.).
      Grounded in Copilot/RESULTS.md. This only fires for CLI `-p`/
      interactive sessions -- it has nothing to do with the editor UI.

  copilot_local_hooks.py (this file) -- a SEPARATE, custom VS Code
      extension (`signal-local-hooks`, built for this project, NOT anything
      Copilot itself ships) that listens to raw VS Code EDITOR API events
      (onDidChangeTextDocument, onDidSaveTextDocument, diagnostics, fs
      watcher, terminal open/close) plus a Tab-keybinding-override heuristic
      to detect AI inline-suggestion accepts/undos, plus a one-time
      environment_snapshot of which AI extensions are installed. This has
      NOTHING to do with Copilot's own hooks.json/HTTP mechanism -- it does
      not require any Copilot-side configuration at all, works purely off
      VS Code's own extension API, and captures IDE activity that Copilot's
      CLI hooks never see (e.g. raw keystrokes, diagnostics, terminal
      lifecycle, inline-suggestion Tab-accepts with no corresponding CLI
      tool call at all).

Grounded entirely in the real, empirical capture performed for this
project. Primary sources (read-only, never modified):

  Copilot_LocalHooks/RESULTS.md                    -- the full write-up
  Copilot_LocalHooks/captured_ide_events.jsonl      -- 121 real captured events
  Copilot_LocalHooks/signal-local-hooks/            -- the extension source
      (src/extension.ts, package.json, packaged .vsix)

REAL, NON-OBVIOUS QUIRKS THIS COLLECTOR'S DESIGN ACCOUNTS FOR (cited inline
near the relevant code below; see RESULTS.md for full detail):
  - `onDidCloseTerminal` never fired even once in real testing (0/1),
    despite `onDidOpenTerminal` firing correctly twice -- a confirmed gap,
    not a bug in this collector's listener wiring (RESULTS.md section a).
  - `onDidOpenTerminal`'s `terminal.name` field is an empty string at fire
    time in both real instances observed -- filtering/labeling by name at
    that moment is unreliable (RESULTS.md section a).
  - The accept-heuristic (Tab press while `inlineSuggestionVisible` is
    true) is 4/4 true positive, 0 false positive on the one real session
    tested -- but is STRUCTURALLY BLIND to mouse-click acceptance, which
    was never tested and is guaranteed missed by this design, not just
    an untested edge case (RESULTS.md section b).
  - `environment_snapshot.detected_ai_extensions[...].active` can be a
    false negative at startup, because the snapshot fires on
    `onStartupFinished` which can race ahead of Copilot Chat's own lazy
    activation (RESULTS.md section e).

No normalization: raw_payload is the exact JSON shape each real event type
already has in captured_ide_events.jsonl -- untouched (different event
`type` values carry different field sets; this collector does not force
them into one common shape).
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

AGENT_NAME = "copilot"
COLLECTOR_ID = "copilot.local_hooks"

# The real event `type` values observed in Copilot_LocalHooks/captured_ide_events.jsonl
# / documented in Copilot_LocalHooks/RESULTS.md section a -- listed here for
# detect()/labeling purposes only, not used to filter/drop any event.
KNOWN_EVENT_TYPES = (
    "activation",
    "environment_snapshot",
    "onDidChangeTextDocument",
    "onDidSaveTextDocument",
    "onDidOpenTextDocument",
    "onDidCloseTextDocument",
    "onDidChangeDiagnostics",
    "fsWatcher_create",
    "fsWatcher_change",
    "fsWatcher_delete",
    "onDidOpenTerminal",
    "onDidCloseTerminal",  # documented-but-never-fired, per RESULTS.md section a
    "probable_accept",
    "accept_correlation",
    "probable_undo",
)

# The VS Code extension ID for this project's own custom local-hooks
# extension (Copilot_LocalHooks/signal-local-hooks/package.json) -- distinct
# from GitHub.copilot / GitHub.copilot-chat, which are the AI extensions
# THIS extension observes, not itself.
LOCAL_HOOKS_EXTENSION_ID = "signal-local-hooks"

# AI extensions this extension's real environment_snapshot checks for
# presence/version of (Copilot_LocalHooks/RESULTS.md section e, exact list
# and exact real result for this environment: copilot-chat present,
# standalone copilot absent).
KNOWN_AI_EXTENSION_IDS = (
    "GitHub.copilot",
    "GitHub.copilot-chat",
    "Anysphere.cursor-always-local",
    "Continue.continue",
    "Codeium.codeium",
    "TabNine.tabnine-vscode",
    "AmazonWebServices.aws-toolkit-vscode",
)


def is_accept_correlation_event(payload: dict) -> bool:
    """Structural check for the Tab-driven inline-suggestion-accept
    heuristic's own event type (RESULTS.md section b) -- used only for
    human/log labeling in this file's __main__ demo, never to alter
    raw_payload.
    """
    return payload.get("type") in ("probable_accept", "accept_correlation", "probable_undo")


class CopilotLocalHooksCollector(Collector):
    """IDE-activity collector fed by the custom `signal-local-hooks` VS Code
    extension -- NOT Copilot's own hook system (see copilot_hooks.py for
    that). This collector observes VS Code's own editor API surface, which
    is a strict superset in some ways (raw keystrokes/diagnostics/terminal
    lifecycle) and a strict subset in others (no tool-call/session
    lifecycle semantics at all -- that's what copilot_hooks.py covers)
    compared to Copilot's CLI hooks.

    Live mode (real deployment): the extension itself (already built and
    packaged at Copilot_LocalHooks/signal-local-hooks/signal-local-hooks-
    0.0.1.vsix) writes to its own output channel / a local JSONL file as
    VS Code events fire; activate() would tail that file (analogous to how
    a live copilot_hooks.py deployment would attach to an HTTP listener).
    This build supports feeding capture() from already-captured JSONL
    records (Copilot_LocalHooks/captured_ide_events.jsonl, the REAL
    121-event capture from one live test session) to exercise it
    deterministically without a live VS Code session.
    """

    def __init__(self, replay_events: Optional[List[dict]] = None) -> None:
        self._replay_events = replay_events
        self._activated = False

    @property
    def collector_id(self) -> str:
        return COLLECTOR_ID

    def detect(self) -> bool:
        """Real, cheap heuristics that this project's own `signal-local-
        hooks` extension is installed on this machine, per
        Copilot_LocalHooks/RESULTS.md's setup section:
          1. The packaged .vsix exists locally (proof it was built here at
             all -- not proof it's installed into a running VS Code, which
             would require shelling out to `code --list-extensions`, ruled
             out as a detect()-time side effect per the same reasoning as
             copilot_hooks.py's detect()).
          2. A `signal-local-hooks` directory exists under
             ~/.vscode/extensions (matching how copilot_hooks.py checks for
             GitHub.copilot-chat under the same directory).
          3. A local captured_ide_events.jsonl file exists in the expected
             project location (proof this extension has actually run and
             produced real output before).
        This intentionally does NOT check for GitHub.copilot-chat itself --
        that's a precondition for what this extension *observes*, not for
        whether this extension (the collector's own data source) is
        present, which is what detect() must answer.
        """
        found_any = False

        vscode_ext_dir = Path.home() / ".vscode" / "extensions"
        if vscode_ext_dir.is_dir():
            try:
                for entry in vscode_ext_dir.iterdir():
                    if LOCAL_HOOKS_EXTENSION_ID.lower() in entry.name.lower():
                        found_any = True
                        break
            except OSError:
                pass

        cur = Path.cwd()
        for candidate in [cur, *cur.parents]:
            vsix_dir = candidate / "signal-local-hooks"
            if vsix_dir.is_dir():
                try:
                    if any(vsix_dir.glob("*.vsix")):
                        found_any = True
                except OSError:
                    pass
            captured = candidate / "captured_ide_events.jsonl"
            if captured.is_file():
                found_any = True

        return found_any

    def activate(self) -> None:
        """Documents the real known gaps/caveats (RESULTS.md sections a, b,
        e) so a live deployment's consumer isn't surprised by them later --
        mirrors copilot_hooks.py's activate() pattern of warning rather than
        silently hoping for the best.
        """
        print(
            f"[{self.collector_id}] NOTE: onDidCloseTerminal is confirmed to "
            f"never fire in real testing (0/1 in the captured session) -- do "
            f"not build logic that depends on it firing. "
            f"(Copilot_LocalHooks/RESULTS.md section a.)",
            file=_sys.stderr,
        )
        print(
            f"[{self.collector_id}] NOTE: the accept-heuristic only detects "
            f"Tab-driven inline-suggestion accepts; mouse-click acceptance is "
            f"structurally invisible to this collector by design, not a bug. "
            f"(Copilot_LocalHooks/RESULTS.md section b.)",
            file=_sys.stderr,
        )
        self._activated = True

    def capture(self) -> Iterator[RawEvent]:
        if not self._activated:
            raise RuntimeError(f"{self.collector_id}: capture() called before activate()")

        if self._replay_events is None:
            # Live mode placeholder: no live extension-output tail is wired
            # up in this standalone demo build.
            return

        for payload in self._replay_events:
            # raw_payload is the untouched real event dict exactly as the
            # signal-local-hooks extension produced it -- different `type`
            # values carry structurally different fields (e.g.
            # onDidChangeTextDocument's range/rangeLength/insertedLength vs.
            # environment_snapshot's detected_ai_extensions map vs.
            # probable_undo's originalAcceptTimestamp/msSinceAccept) and
            # this collector does not force them into one common shape.
            yield RawEvent(
                timestamp=RawEvent.now_iso(),
                collector_id=self.collector_id,
                agent=AGENT_NAME,
                raw_payload=payload,
            )

    def deactivate(self) -> None:
        self._activated = False


if __name__ == "__main__":
    print("=== CopilotLocalHooksCollector standalone test ===")

    collector = CopilotLocalHooksCollector()
    detected = collector.detect()
    print(f"detect() -> {detected}")

    real_evidence_path = (
        Path(__file__).resolve().parents[4]
        / "Copilot_LocalHooks" / "captured_ide_events.jsonl"
    )

    if real_evidence_path.is_file():
        print(f"Found real captured IDE events at {real_evidence_path}")
        events = []
        with open(real_evidence_path) as f:
            for line in f:
                line = line.strip()
                if line:
                    events.append(json.loads(line))

        replay_collector = CopilotLocalHooksCollector(replay_events=events)
        replay_collector.activate()
        print(
            f"\nSIMULATED FROM CAPTURED DATA (real {len(events)} IDE activity "
            f"events from Copilot_LocalHooks/captured_ide_events.jsonl):"
        )

        type_counts: dict = {}
        shown = 0
        for event in replay_collector.capture():
            t = event.raw_payload.get("type", "?")
            type_counts[t] = type_counts.get(t, 0) + 1
            if shown < 6:
                print(
                    f"  [{shown}] collector_id={event.collector_id!r} "
                    f"type={t!r} keys={sorted(event.raw_payload.keys())}"
                )
                shown += 1
        replay_collector.deactivate()

        print(f"\n  ... ({sum(type_counts.values())} total events captured)")
        print("Event type breakdown:")
        for t, count in sorted(type_counts.items(), key=lambda kv: -kv[1]):
            print(f"  {t}: {count}")

        if "onDidCloseTerminal" not in type_counts:
            print(
                "\nConfirmed real gap reproduced: onDidCloseTerminal did not "
                "appear anywhere in the real capture (matches RESULTS.md "
                "section a's 0/1 finding)."
            )
    else:
        print(
            f"Real evidence file not found at {real_evidence_path} -- "
            f"skipping replay demonstration."
        )
