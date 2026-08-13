"""
Antigravity (Google) Hooks collector.

Grounded entirely in the real, empirical capture performed for this project
-- NOT vendor docs, which testing found structurally wrong in the one place
that matters most (the hooks.json schema itself). Primary sources
(read-only, never modified):

  Antigravity/RESULTS.md                    -- the full write-up this file is built from
  Antigravity/config/hooks.json              -- exact working named-hook-block config
  Antigravity/.agents/hooks.json             -- workspace copy of the same config
  Antigravity/captured_events.jsonl          -- raw captured stdin/stdout hook invocations
  Antigravity_LocalHooks/RESULTS.md          -- IDE-side corroboration
  Antigravity_OTel/RESULTS.md                -- confirms `agy` is a separate CLI binary
  Antigravity_FSWatcher/captured_fs_events.jsonl -- confirms ~/.gemini/antigravity(-cli)/brain/<id> paths

This collector performs NO normalization: the real stdin JSON Antigravity
sends to a command-type hook passes through untouched inside `raw_payload`.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from typing import Iterator, List, Optional, Set

import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parents[2]))
from core import Collector, RawEvent

AGENT_NAME = "antigravity"
COLLECTOR_ID = "antigravity.hooks"

# Real event types confirmed to fire in testing (Antigravity/RESULTS.md
# section 2) -- all 5 documented event types fired at least once.
KNOWN_HOOK_EVENTS = (
    "PreToolUse", "PostToolUse", "PreInvocation", "PostInvocation", "Stop",
)


def load_hooks_config(path: Path) -> Optional[dict]:
    """Parse a real Antigravity hooks.json using the REAL schema discovered
    in testing -- named hook blocks, NOT the flat `{"hooks": {...}}` shape
    vendor docs describe and that claude_code_hooks.py / cursor_hooks.py use
    for their own (differently-shaped) configs. Confirmed schema
    (Antigravity/RESULTS.md section 1, cross-checked against the vendor's
    own embedded hooks.md spec string found inside the Antigravity.app Go
    binary itself):

        {
          "<arbitrary-hook-name>": {
            "enabled": true,
            "PreToolUse":  [ { "matcher": "*", "hooks": [ {handler}, ... ] } ],
            "PostToolUse": [ { "matcher": "*", "hooks": [ {handler}, ... ] } ],
            "PreInvocation":  [ {handler}, ... ],   # flat form, no matcher
            "PostInvocation": [ {handler}, ... ],   # flat form, no matcher
            "Stop":            [ {handler}, ... ]   # flat form, no matcher
          }
        }

    Top-level keys are hook names YOU choose (e.g. "verify-hooks" in the
    real captured config), not event names. PreToolUse/PostToolUse use the
    grouped {matcher, hooks:[...]} form; PreInvocation/PostInvocation/Stop
    use the flat list-of-handlers form with no matcher wrapper.
    """
    if not path.is_file():
        return None
    try:
        cfg = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return None
    if not isinstance(cfg, dict):
        return None
    # Sanity-check it actually looks like a named-hook-block config (has at
    # least one top-level value that is a dict with an "enabled" key or one
    # of the known event names) rather than some unrelated JSON file.
    for block in cfg.values():
        if isinstance(block, dict) and (
            "enabled" in block or any(evt in block for evt in KNOWN_HOOK_EVENTS)
        ):
            return cfg
    return None


def event_dedup_key(payload: dict) -> str:
    """Best-effort dedup key for the confirmed global+workspace duplication
    (see class docstring / KNOWN LIMITATION below). We do not have a real
    per-delivery event id in the captured data -- Antigravity's stdin
    payload carries no unique event/delivery id field at all, only
    conversationId + stepIdx/invocationNum/executionNum (scoped per-event-
    type counters, not globally unique, and IDENTICAL across the global and
    workspace duplicate deliveries of the same logical event, which is
    exactly what makes them look like duplicates in the first place).

    Best-effort approach: hash the full payload JSON. Two independently
    fired hook processes (global config copy + workspace config copy) for
    the SAME underlying event send byte-identical stdin JSON (confirmed in
    Antigravity/RESULTS.md section 6a: the only thing that differs between
    the two deliveries is the *forwarder subprocess's own cwd*, which is
    not part of the payload itself) -- so a content hash collapses true
    duplicates. This is NOT bulletproof: two genuinely different events
    that happen to produce byte-identical payloads (e.g. two PreInvocation
    calls with the same invocationNum/initialNumSteps very early in a
    session, before any tool call differentiates them) would also collide
    and be incorrectly dropped. See KNOWN LIMITATION below.
    """
    try:
        canonical = json.dumps(payload, sort_keys=True)
    except TypeError:
        canonical = repr(payload)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class AntigravityHooksCollector(Collector):
    """Hooks-based collector for Antigravity (Google's agentic IDE/CLI).

    Live mode (real deployment): Antigravity is configured via a workspace
    `.agents/hooks.json` and/or a global `~/.gemini/config/hooks.json`, each
    using the real named-hook-block schema above. Each configured hook is a
    `command`-type handler that Antigravity invokes as a subprocess, passing
    the event payload as JSON on stdin and expecting a JSON object back on
    stdout (`{}` minimum for non-gating events). A full deployment's
    activate() would start/attach to the local listener that such a command
    handler forwards to, and capture() would drain a queue it fills.

    This build supports feeding capture() from already-captured JSONL event
    dicts (Antigravity/captured_events.jsonl and friends) to exercise it
    deterministically against real sample data.

    KNOWN LIMITATION -- global+workspace duplication dedup is best-effort,
    NOT solved: Antigravity fires BOTH a workspace-scoped hooks.json and a
    global (~/.gemini/config/hooks.json) hooks.json independently for every
    single event when both are configured with the same hook (confirmed:
    every event count in Antigravity/RESULTS.md section 2 is exactly double
    what a single scope would produce). The real captured payloads carry no
    delivery-unique id to key a dedup off of, so this collector's dedup
    (see event_dedup_key()) is a content-hash best effort with a known false
    -collision edge case for legitimately identical back-to-back payloads.
    This is flagged here and in README.md's Known Limitations section
    rather than silently presented as solved -- do not treat capture()'s
    de-duplicated output as guaranteed-exactly-once delivery.
    """

    def __init__(
        self,
        replay_events: Optional[List[dict]] = None,
        dedup: bool = True,
    ) -> None:
        self._replay_events = replay_events
        self._activated = False
        self._dedup = dedup
        self._seen_keys: Set[str] = set()

    @property
    def collector_id(self) -> str:
        return COLLECTOR_ID

    def detect(self) -> bool:
        """Real, cheap heuristics for "is Antigravity installed/configured
        on this machine", per Antigravity/RESULTS.md and Antigravity_OTel/RESULTS.md:
          1. `agy` binary on PATH -- confirmed a SEPARATE binary/product
             from the Antigravity.app IDE itself (installed via the
             `antigravity-cli` Homebrew cask, distinct install path).
          2. ~/.gemini/config directory exists (global config home).
          3. A workspace-local .agents/hooks.json exists with the real
             named-hook-block schema (proof hooks are actually wired up,
             not just that the CLI/IDE is installed).
        """
        found_any = False

        if shutil.which("agy") is not None:
            found_any = True

        gemini_config_dir = Path.home() / ".gemini" / "config"
        if gemini_config_dir.is_dir():
            found_any = True

        cur = Path.cwd()
        for candidate in [cur, *cur.parents]:
            hooks_path = candidate / ".agents" / "hooks.json"
            if load_hooks_config(hooks_path) is not None:
                found_any = True
                break

        return found_any

    def activate(self) -> None:
        """Documents the confirmed global+workspace duplication and flips
        the activation flag. Does not attempt to silently "fix" the
        duplication by, e.g., deleting one of the two real config files --
        that would be a destructive side effect outside this collector's
        scope. It only warns and enables best-effort in-stream dedup (see
        event_dedup_key()) if both a global and workspace hooks.json are
        present, matching the exact condition that produced double-firing
        in testing.
        """
        global_cfg = Path.home() / ".gemini" / "config" / "hooks.json"
        workspace_cfg = Path.cwd() / ".agents" / "hooks.json"
        if load_hooks_config(global_cfg) is not None and load_hooks_config(workspace_cfg) is not None:
            print(
                f"[{self.collector_id}] WARNING: both a global "
                f"({global_cfg}) and workspace ({workspace_cfg}) hooks.json "
                f"are present. Confirmed in testing (Antigravity/RESULTS.md "
                f"section 6a) that BOTH fire independently for every event "
                f"-- every hook (and side effect) runs twice. This "
                f"collector applies best-effort content-hash dedup "
                f"(dedup={self._dedup}); see class docstring KNOWN "
                f"LIMITATION for why this is not guaranteed-exact."
            )
        self._activated = True

    def capture(self) -> Iterator[RawEvent]:
        if not self._activated:
            raise RuntimeError(f"{self.collector_id}: capture() called before activate()")

        if self._replay_events is None:
            # Live mode placeholder: no live stdin/stdout hook subprocess
            # listener is wired up in this standalone demo build.
            return

        for payload in self._replay_events:
            if self._dedup:
                key = event_dedup_key(payload)
                if key in self._seen_keys:
                    # Best-effort duplicate (see KNOWN LIMITATION) --
                    # silently dropped rather than yielded twice.
                    continue
                self._seen_keys.add(key)

            # DOCUMENTED QUIRKS, not bugs to fix here:
            #  - `terminationReason` on Stop events uses real values that do
            #    NOT match documented examples. Confirmed real value seen in
            #    testing: "NO_TOOL_CALL" (SCREAMING_SNAKE_CASE), not the
            #    docs' "model_stop"/"max_steps_exceeded"/"error" examples
            #    (Antigravity/RESULTS.md section 3, Stop table + section 6
            #    item 3). Treat the docs' list as illustrative, not
            #    exhaustive -- do not validate/reject unrecognized values.
            #  - Argument keys inside toolCall.args are PascalCase
            #    (e.g. "CodeContent", "TargetFile", "CommandLine") despite
            #    the surrounding envelope being camelCase
            #    (conversationId, stepIdx, workspacePaths). Confirmed in
            #    Antigravity/RESULTS.md section 3 ("Notable: arg key casing
            #    is PascalCase ... this is the opposite convention from the
            #    envelope-level fields"). This collector does NOT normalize
            #    or re-case toolCall.args -- it is forwarded exactly as
            #    Antigravity sent it.
            yield RawEvent(
                timestamp=RawEvent.now_iso(),
                collector_id=self.collector_id,
                agent=AGENT_NAME,
                raw_payload=payload,  # untouched, exact shape from source
            )

    def deactivate(self) -> None:
        self._activated = False
        self._seen_keys.clear()
