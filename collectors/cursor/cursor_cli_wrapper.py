"""
Cursor CLI Wrapper (pty passive-capture) collector.

STATUS: UNVERIFIED / BEST-EFFORT for the *mechanism*, but the *fields it
would carry* are real and directly cited in
reference_tables/observables_table_claude_cursor.csv's "Cursor source"
column. This is a narrower situation than copilot_fs_watcher.py's -- here
the CSV clearly says these exact fields come from a "CLI wrapper" for
Cursor, but no dedicated `Cursor_CLIWrapper/` evidence folder exists in
this project (compare: `Codex_CLIWrapper/`, `Antigravity_CLIWrapper/`
both exist with real wrapper.py + captured_output_*.jsonl; there is no
Cursor equivalent). This module documents that gap honestly rather than
inventing a fabricated captured_events.jsonl-style replay source.

WHAT IS A CURSOR "CLI" AT ALL -- checked directly, not assumed:
  - cursor_hooks.py's own detect() checks `shutil.which("cursor")` as one
    of three heuristics, so this framework's existing code already treats
    a `cursor` binary on PATH as a real, checkable signal.
  - cursor_proxy2.py (the real production Cursor mitmproxy sidecar) never
    mentions a `cursor` CLI at all -- it intercepts Cursor.app's own
    network traffic to `cursor.sh`, i.e. Cursor's real "CLI wrapper"
    surface (per this collector's evidence) is most plausibly the shell
    launcher binary macOS/Cursor installs (`cursor` on PATH, which opens
    the GUI app / attaches a new window to an existing instance), NOT an
    independent agentic CLI comparable to `codex`/`copilot`/`agy` that
    executes agent turns headlessly in a terminal.
  - No `Cursor_CLIWrapper/` folder, no cursor-flavored wrapper.py, no
    cursor-flavored captured_output_*.jsonl exists anywhere in this
    project (checked via `find . -iname "*cursor*cliwrapper*"` and
    directory listing) -- unlike the Codex/Antigravity CLI Wrapper
    collectors in this framework, which port a real, previously-run
    wrapper script and its real captures, this collector has no such
    concrete artifact to port from for Cursor.
  This collector therefore honestly narrows itself: detect() checks for
  the real, confirmed things (a `cursor` binary on PATH, matching
  cursor_hooks.py's own precedent) but does NOT claim that binary is a
  headless agentic CLI equivalent to `codex exec`/`copilot`. TODO:
  unverified, needs a live capture pass (spawn `cursor` under a pty via
  cli_wrapper_demo/wrapper.py's mechanism and see what -- if anything --
  it actually prints to a terminal, the same kind of test
  Codex_CLIWrapper/RESULTS.md and Antigravity_CLIWrapper/RESULTS.md
  performed for their targets) before trusting this collector's live-mode
  path in production.

FIELDS THIS COLLECTOR IS GROUNDED IN (CSV "Cursor source" column, `CLI
wrapper` cited explicitly -- these are the same wrapper-only fields cited
for Claude Code too, i.e. this is a technique fact independent of which
agent is wrapped):
  - row 25 operator_username: CC=CLI wrapper, Cursor=CLI wrapper.  Note:
    "OS username from the collector."
  - row 28 hostname:          CC=CLI wrapper, Cursor=CLI wrapper.  Note:
    "Collector environment."
  - row 86 stderr:            CC=local hooks, Cursor=CLI wrapper.  Note:
    "CC separates them. Cursor merges into output." -- i.e. per the CSV,
    Cursor's *native* hooks/afterShellExecution already merges
    stdout+stderr into one `output` field (see cursor_hooks.py's
    KNOWN_HOOK_EVENTS docstring, `afterShellExecution`), so a CLI wrapper
    is specifically the way to recover a genuinely SEPARATE stderr stream
    for Cursor, mirroring exactly what codex_cli_wrapper.py's own {stream:
    "stdout"|"stderr"} chunk shape already captures for Codex.
  - row 90 hung_seconds:         CC=CLI wrapper, Cursor=CLI wrapper.  Note:
    "WRAPPER ONLY. Nothing else can see silence while a process is alive."
  - row 91 is_developer_command: CC=CLI wrapper, Cursor=CLI wrapper.  Note:
    "WRAPPER ONLY. Commands the human ran, which the agent never saw."
  (row 84 exit_code is explicitly Cursor=`local hooks`, NOT CLI wrapper --
  "Cursor puts exitCode inside tool_output. CC needs the wrapper or
  stderr" -- so unlike Claude Code, a Cursor CLI wrapper is NOT this
  collector's route to exit codes; that field already exists on
  cursor_hooks.py's afterShellExecution payload. Not duplicated here.)

MECHANISM (generic pty-wrapper, ported the same way codex_cli_wrapper.py
ports it): adapted from cli_wrapper_demo/wrapper.py's pty + stdin
passthrough + JSONL-chunk approach -- the same {timestamp, stream,
content, wrapped_command} record shape codex_cli_wrapper.py and
copilot_cli_wrapper.py already use, reused here purely as a MECHANISM
reference since no Cursor-specific wrapper script exists to port from
directly.

No normalization: raw_payload keeps exactly the {timestamp, stream,
content, wrapped_command} shape the generic wrapper mechanism produces --
untouched.
"""

from __future__ import annotations

import getpass
import shutil
import socket
from pathlib import Path
from typing import Iterator, List, Optional

import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parents[2]))
from core import Collector, RawEvent

AGENT_NAME = "cursor"
COLLECTOR_ID = "cursor.cli_wrapper"

# CSV-cited, wrapper-only fields for Cursor (see module docstring rows 25,
# 28, 86, 90, 91). Recorded here as constants purely for documentation/
# traceability -- this collector does not compute risk/derived fields, it
# only captures raw pty chunks; a later normalization stage would extract
# these from the wrapped invocation's real environment.
WRAPPER_ONLY_FIELDS = (
    "operator_username",  # CSV row 25 -- os.getlogin()/getpass.getuser()
    "hostname",            # CSV row 28 -- socket.gethostname()
    "stderr",              # CSV row 86 -- separate stream, Cursor's own hooks merge it
    "hung_seconds",        # CSV row 90 -- wrapper-only, needs timing instrumentation
    "is_developer_command",# CSV row 91 -- wrapper-only, human-typed vs agent-issued
)


class CursorCLIWrapperCollector(Collector):
    """pty-wrapper-based collector for whatever `cursor` binary is on PATH.

    STATUS: mechanism is real and reused verbatim from this framework's
    other CLI Wrapper collectors (Codex/Copilot), but there is no
    dedicated Cursor evidence folder confirming what (if anything) a
    `cursor` binary prints when wrapped -- unlike codex_cli_wrapper.py
    and copilot_cli_wrapper.py, which port a real, previously-captured
    wrapper script + JSONL samples. See module docstring for exactly what
    was checked and what remains unverified.

    Live mode (real deployment, IF a headless-capable `cursor` binary is
    ever confirmed to exist): activate() would spawn `cursor <args>`
    behind a pseudo-TTY exactly as cli_wrapper_demo/wrapper.py does,
    forwarding stdin transparently and appending every captured chunk to
    a JSONL sink as {timestamp, stream, content, wrapped_command}.
    capture() would then drain that sink.

    This build supports feeding capture() from replay_events for
    deterministic testing; there is no real Cursor capture file anywhere
    in this project to load a default sample from (unlike
    codex_cli_wrapper.py's _load_sample_wrapper_chunks()), so this
    collector's __main__ demo is explicit that it has none.
    """

    def __init__(self, replay_events: Optional[List[dict]] = None) -> None:
        self._replay_events = replay_events
        self._activated = False

    @property
    def collector_id(self) -> str:
        return COLLECTOR_ID

    def detect(self) -> bool:
        """Real, cheap, side-effect-free checks for "is a `cursor` binary
        present on this machine's PATH" -- the same check cursor_hooks.py
        already uses as one of its three heuristics. Also checks the
        macOS app bundle, since Cursor's `cursor` shell command is
        installed by, and only meaningful alongside, Cursor.app itself
        (per "Shell Command: Install 'cursor' command" inside the app,
        the standard way this kind of launcher shim gets onto PATH).
        Deliberately does NOT claim this proves a headless/agentic CLI
        surface exists -- only that the binary this collector would wrap
        is present.
        """
        found_any = False

        if shutil.which("cursor") is not None:
            found_any = True

        if Path("/Applications/Cursor.app").exists():
            found_any = True

        return found_any

    def activate(self) -> None:
        """No live pty-spawn implemented in this standalone demo build
        (matching codex_cli_wrapper.py's / copilot_cli_wrapper.py's own
        posture -- live mode is a placeholder, replay mode is what's
        exercised here). Marking activated is all this does.
        """
        self._activated = True

    def capture(self) -> Iterator[RawEvent]:
        if not self._activated:
            raise RuntimeError(f"{self.collector_id}: capture() called before activate()")

        if self._replay_events is None:
            # Live mode placeholder -- no live pty-wrapped child process
            # spawned in this standalone demo build, and (unlike Codex/
            # Copilot) no confirmed-real headless invocation of `cursor`
            # to spawn even if it were implemented. See module docstring.
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


def _synthetic_demo_chunks() -> List[dict]:
    """No real Cursor CLI-wrapper capture exists anywhere in this project
    (no `Cursor_CLIWrapper/` folder, unlike its Codex/Antigravity
    siblings) -- these are CLEARLY LABELED synthetic placeholders, built
    only from real environment values (actual username/hostname on this
    machine) plus the CSV-cited field names, to exercise capture()'s
    plumbing end-to-end without fabricating fictitious command output.
    """
    real_user = getpass.getuser()
    real_host = socket.gethostname()
    return [
        {
            "timestamp": RawEvent.now_iso(),
            "stream": "stdout",
            "content": "TODO: unverified, needs live capture -- placeholder only",
            "wrapped_command": "cursor",
            "_synthetic_placeholder": True,
            "_note": (
                "No real Cursor_CLIWrapper/ capture exists in this project. "
                "operator_username/hostname below are the ONLY real values "
                "in this record (read from this machine at demo time, per "
                "CSV rows 25/28); everything else is a labeled placeholder."
            ),
            "operator_username": real_user,
            "hostname": real_host,
        }
    ]


if __name__ == "__main__":
    collector = CursorCLIWrapperCollector()
    print(f"[{collector.collector_id}] detect() -> {collector.detect()}")
    print(
        f"[{collector.collector_id}] NOTE: no dedicated Cursor_CLIWrapper/ "
        f"evidence folder exists in this project (unlike Codex_CLIWrapper/ "
        f"or Antigravity_CLIWrapper/) -- this collector's live-mode wrapper "
        f"mechanism is UNVERIFIED against a real `cursor` invocation. See "
        f"module docstring."
    )

    demo_events = _synthetic_demo_chunks()
    collector = CursorCLIWrapperCollector(replay_events=demo_events)
    collector.activate()
    print(f"[{collector.collector_id}] activate() ok")

    count = 0
    for event in collector.capture():
        count += 1
        print(f"\n--- RawEvent #{count} (SYNTHETIC PLACEHOLDER, not real captured data) ---")
        print(f"timestamp:          {event.timestamp}")
        print(f"collector_id:       {event.collector_id}")
        print(f"agent:              {event.agent}")
        print(f"operator_username:  {event.raw_payload.get('operator_username')} (real, this machine)")
        print(f"hostname:           {event.raw_payload.get('hostname')} (real, this machine)")
        print(f"wrapped_command:    {event.raw_payload.get('wrapped_command')}")

    collector.deactivate()
    print(f"\n[{collector.collector_id}] deactivate() ok. Total events yielded: {count}")
