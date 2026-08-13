# Collector Framework

A small, dependency-free (Python 3 stdlib only) framework for pulling raw,
agent-native telemetry events off a machine running an AI coding agent
(Claude Code, Cursor, and — as a template — Copilot, Antigravity, Codex,
etc.), without normalizing or policy-filtering them. Normalization and
policy come later in the pipeline, deliberately outside this framework.

This code was built by reading the project's existing production
webhook server (`projectsetup/backend/webhook_server.py`, 3577 lines),
the Cursor mitmproxy sidecar (`projectsetup/backend/cursor_proxy2.py`),
the real hook configs (`projectsetup/.claude/settings.json`,
`projectsetup/.cursor/hooks.json`), and real captured hook payloads
(`projectsetup/backend/events/*.jsonl`, `projectsetup/events/*.jsonl`).
None of those files were modified — only read.

## Repository layout

The package is organized by responsibility, one directory per pipeline
stage, mirroring the Deployment-phase component list (Collector Framework
→ Integration Packages → Discovery Engine → Policy Engine → ...):

```
collector_framework/
├── core/                     # Collector Framework (interface + registry)
│   ├── __init__.py           #   re-exports Collector, RawEvent, CollectorRegistry, CollectorFailure
│   ├── interfaces.py         #   Collector ABC + RawEvent dataclass (was core.py)
│   └── registry.py           #   CollectorRegistry
├── collectors/                # Integration Packages -- the 28 real collectors, one subfolder per agent
│   ├── claude_code/           #   claude_code_hooks.py, claude_code_otel.py, claude_code_cli_wrapper.py,
│   │                          #   claude_code_mcp_proxy.py, claude_code_fs_watcher.py
│   ├── cursor/                #   cursor_hooks.py, cursor_otel.py, cursor_cli_wrapper.py,
│   │                          #   cursor_mcp_proxy.py, cursor_fs_watcher.py
│   ├── copilot/                #   + otel/cli_wrapper/mcp_proxy/fs_watcher/local_hooks (6 total)
│   ├── antigravity/            #   + otel/cli_wrapper/mcp_proxy/fs_watcher/local_hooks (6 total)
│   └── codex/                  #   + otel/cli_wrapper/mcp_proxy/fs_watcher/local_hooks (6 total)
├── discovery/
│   └── discovery_engine.py    # Discovery Engine -- scans machine, reports viable (agent, technique) pairs
├── policy/
│   ├── policy_engine.py       # Policy Engine -- decides which viable pairs are approved to activate
│   └── sample_policy.json     # real, judgment-grounded sample policy
├── activation/
│   └── activation_engine.py   # Activation Engine -- wires approved pairs into a live CollectorRegistry
├── update/
│   └── update_engine.py       # Update Engine -- snapshot/diff/apply/verify/rollback for a deployed tree
├── package/
│   ├── cli.py                 # signal-agent CLI -- the single entry point frozen into the exe
│   ├── build_exe.py           # PyInstaller freeze script -- run this to produce dist/signal-agent
│   └── _baked_version.py      # generated at build time by build_exe.py; do not hand-edit
├── dist/
│   └── signal-agent           # the frozen executable (this platform only -- see "Deployable Package")
├── demos/
│   ├── run_demo.py                  # Claude Code + Cursor only
│   ├── run_demo_all_five.py         # all five Hooks collectors
│   ├── run_demo_full.py             # all 28 collectors
│   ├── run_pipeline_end_to_end.py   # the real, full pipeline: scan -> decide -> wire -> capture -> deactivate
│   └── run_update_demo.py           # snapshot -> diff -> apply -> verify -> rollback, in temp copies only
├── reference_tables/          # the two real observability-requirement CSVs (read-only reference, not code)
└── README.md
```

Every file keeps its original name from before this reorganization
(`claude_code_hooks.py` is still `claude_code_hooks.py`, just now living at
`collectors/claude_code/claude_code_hooks.py`) — only locations and import
paths changed, not identities or contents. This was verified after the
move by re-running `demos/run_demo_full.py`, `discovery/discovery_engine.py`,
and `policy/policy_engine.py` and diffing against pre-move output: identical
results (969 `RawEvent`s across 19 active collectors, the same 15
approved/12 denied/3 not-viable policy breakdown), modulo wall-clock
timestamps.

Each collector module still does a plain `from core import Collector,
RawEvent` internally — this keeps working because every moved file now
inserts its own package root onto `sys.path` at import time (mirroring the
pattern `discovery_engine.py` already used before this reorganization),
so nothing about how a collector is *written* changed, only how it
locates its sibling `core` package from its new, deeper location.

## Files

- `core/interfaces.py` — the `Collector` abstract base class and the `RawEvent` dataclass (formerly `core.py`).
- `core/registry.py` — `CollectorRegistry`, which detects/activates/aggregates collectors with failure isolation (formerly `registry.py`).
- `discovery/discovery_engine.py` — standalone `DiscoveryEngine`/`DiscoveryReport`: independently scans this machine for which of the five agents are installed and which of the 28 collectors below are currently viable. Read-only, registry-independent, never activates anything. See "Discovery Engine" section below.
- `policy/policy_engine.py` — `PolicyEngine`/`PolicyConfig`: takes a `DiscoveryReport` plus a JSON policy file and decides which discovered-and-viable `(agent, technique)` pairs are approved to activate, with a traceable reason per decision. Does not discover, does not activate. See "Policy Engine" section below.
- `policy/sample_policy.json` — a real, judgment-grounded sample policy (deny Antigravity org-wide, deny MCP Proxy everywhere, two narrower technique-specific denies, allow everything else Discovery Engine finds viable).
- `activation/activation_engine.py` — `ActivationEngine`: takes a `PolicyDecisionReport`'s approved pairs and actually registers + activates the real collectors into a live `CollectorRegistry`. The first component in this package with real side effects by design. See "Activation Engine" section below.
- `demos/run_pipeline_end_to_end.py` — runs all four components together for real against this machine: Discovery Engine scan -> Policy Engine decision -> Activation Engine wiring -> real merged `capture()` -> `deactivate_all()`.
- `update/update_engine.py` — `UpdateEngine`: hash-based snapshot/diff/apply/verify/rollback for a deployed `collector_framework` tree. See "Update Engine" section below.
- `demos/run_update_demo.py` — a real, safe (temp-copies-only, never touches the real tree) demo of the full update lifecycle.
- `collectors/claude_code/claude_code_hooks.py` — real Claude Code hooks collector.
- `collectors/cursor/cursor_hooks.py` — real Cursor hooks collector.
- `collectors/copilot/copilot_hooks.py` — real GitHub Copilot CLI hooks collector.
- `collectors/antigravity/antigravity_hooks.py` — real Antigravity (Google) hooks collector.
- `collectors/codex/codex_hooks.py` — real Codex CLI hooks collector.
- `demos/run_demo.py` — runnable end-to-end demo against real captured data (Claude Code + Cursor).
- `demos/run_demo_all_five.py` — runnable end-to-end demo against real captured data for all five Hooks collectors.
- `demos/run_demo_full.py` — runnable end-to-end demo registering all 28 collectors below (every technique implemented so far, for every agent), against real captured data.
- `reference_tables/` — the two real, field-level observability requirement CSVs (`observables_table_claude_cursor.csv`, `observables_table_codex_copilot_antigravity.csv`) used to ground the Claude Code / Cursor technique expansion below. Read-only reference, not code.
- Copilot: `collectors/copilot/{copilot_otel,copilot_cli_wrapper,copilot_mcp_proxy,copilot_fs_watcher,copilot_local_hooks}.py`.
- Antigravity: `collectors/antigravity/{antigravity_otel,antigravity_cli_wrapper,antigravity_mcp_proxy,antigravity_fs_watcher,antigravity_local_hooks}.py`.
- Codex: `collectors/codex/{codex_otel,codex_cli_wrapper,codex_mcp_proxy,codex_fs_watcher,codex_local_hooks}.py`.
- Claude Code: `collectors/claude_code/{claude_code_otel,claude_code_cli_wrapper,claude_code_mcp_proxy,claude_code_fs_watcher}.py` (no `claude_code_local_hooks.py` — see below).
- Cursor: `collectors/cursor/{cursor_otel,cursor_cli_wrapper,cursor_mcp_proxy,cursor_fs_watcher}.py` (no `cursor_local_hooks.py` — see below).

## Implemented collectors

| Collector | `collector_id` | Agent | Source of ground truth |
|---|---|---|---|
| `ClaudeCodeHooksCollector` | `claude_code.hooks` | Claude Code | `projectsetup/backend/webhook_server.py`, `projectsetup/.claude/settings.json` |
| `CursorHooksCollector` | `cursor.hooks` | Cursor | `projectsetup/backend/webhook_server.py`, `projectsetup/backend/cursor_proxy2.py`, `projectsetup/.cursor/hooks.json` |
| `CopilotHooksCollector` | `copilot.hooks` | GitHub Copilot CLI | `Copilot/RESULTS.md`, `Copilot/config/hooks.json`, `Copilot/captured_events.jsonl`, `Copilot_LocalHooks/RESULTS.md` |
| `AntigravityHooksCollector` | `antigravity.hooks` | Antigravity (Google) | `Antigravity/RESULTS.md`, `Antigravity/config/hooks.json`, `Antigravity/captured_events.jsonl` |
| `CodexHooksCollector` | `codex.hooks` | Codex CLI (OpenAI) | `Codex/RESULTS.md`, `Codex/config/hooks.json`, `Codex/captured_events.jsonl`, `Codex_GapClosure/RESULTS.md` |

All Hooks collectors were built by reading the real, empirically captured
test data from this project's prior hands-on hook-verification research
(6 techniques × 3 agents) — never from vendor documentation, which testing
repeatedly found incomplete or outright wrong on load-bearing details (see
Known limitations below for specifics). No spreadsheet files were used or
exist on disk; everything is traceable to a `RESULTS.md` and a
`captured_events.jsonl`/equivalent raw file per agent.

### Multi-technique coverage (Copilot, Antigravity, Codex)

For these three agents, the goal is to pull from as many independent
collection surfaces as possible so the richest source can be selected per
agent. Hooks is proven the most consistent surface across all three, but
five more techniques are now implemented per agent (Local Hooks only where
it's a genuinely distinct mechanism from Hooks — see notes below):

| Agent | Technique | `collector_id` | Source of ground truth |
|---|---|---|---|
| Copilot | OTel | `copilot.otel` | `Copilot/copilot_test/RESULTS.md` (plain Copilot Chat: confirmed **zero** OTel export), `Copilot_VSCode_CLI/RESULTS.md` + `evidence/tempo_trace_full.json` (CLI-session path: confirmed real OTLP export) |
| Copilot | CLI Wrapper | `copilot.cli_wrapper` | `cli_wrapper_demo/wrapper.py`, `cli_wrapper_demo/captured_events.jsonl` (real `copilot -p ...` pty capture), `Copilot_VSCode_CLI/RESULTS.md` |
| Copilot | MCP Proxy | `copilot.mcp_proxy` | `Copilot_MCP_Proxy/mcp_proxy.py`, `server/mcp_server.py`, all 3 `captured_mcp_events_run*.jsonl` (success / tool-error / connfail) |
| Copilot | FS Watcher | `copilot.fs_watcher` | **UNVERIFIED / BEST-EFFORT** — no dedicated Copilot FS Watcher test folder exists; watch roots inferred from incidental citations in `Copilot/RESULTS.md` / `Copilot_MCP_Proxy/RESULTS.md` |
| Copilot | Local Hooks | `copilot.local_hooks` | `Copilot_LocalHooks/RESULTS.md`, `captured_ide_events.jsonl` — a separate custom VS Code extension observing raw editor events, distinct from `copilot.hooks`' own `.github/hooks/hooks.json` mechanism |
| Antigravity | OTel | `antigravity.otel` | `Antigravity_OTel/RESULTS.md` + binary-strings/prometheus evidence — confirmed **architecturally dead**: no-op tracer, real telemetry goes to Google-proprietary Clearcut/businessaicode channels, not OTLP |
| Antigravity | CLI Wrapper | `antigravity.cli_wrapper` | `Antigravity_CLIWrapper/RESULTS.md`, `wrapper.py`, `captured_output_json.jsonl` / `captured_output_streamjson.jsonl` |
| Antigravity | MCP Proxy | `antigravity.mcp_proxy` | `Antigravity_MCPProxy/RESULTS.md`, `mcp_proxy.py`, `server/mcp_server.py`, baseline/delegation/connfail captures — confirms `invoke_subagent` delegation never crosses MCP |
| Antigravity | FS Watcher | `antigravity.fs_watcher` | `Antigravity_FSWatcher/fs_watcher.py`, `captured_fs_events.jsonl` (78 real events), `directory_structure.md` |
| Antigravity | Local Hooks | `antigravity.local_hooks` | `Antigravity_LocalHooks/RESULTS.md`, `captured_ide_events.jsonl` (119 real events) — genuinely distinct mechanism from `antigravity.hooks` (VS Code extension API events vs. subprocess `hooks.json`) |
| Codex | OTel | `codex.otel` | `Codex_OTel/RESULTS.md`, `config/config.toml.working_otel_section`, `sample_loki_logs.json` — documents the 3-endpoint config requirement, a `user_email`/`user_account_id` PII leak, and a hardcoded `success:"true"` bug |
| Codex | CLI Wrapper | `codex.cli_wrapper` | `Codex_CLIWrapper/RESULTS.md`, adapted `wrapper.py` |
| Codex | MCP Proxy | `codex.mcp_proxy` | `Codex_MCPProxy/RESULTS.md`, `mcp_proxy.py`, baseline/connfail/delegation captures, `hooks_crossref_delegation.jsonl` — includes a `correlate_with_hooks()` utility ported from the real MCP↔Hooks subagent-correlation technique |
| Codex | FS Watcher | `codex.fs_watcher` | `Codex_FSWatcher/RESULTS.md`, `fs_watcher.py`, cross-checked against `codex_hooks.py`'s `~/.codex/sessions/rollout-*.jsonl` layout |
| Codex | Local Hooks | `codex.local_hooks` | `Codex_LocalHooks/RESULTS.md`, `captured_ide_events.jsonl`, `Codex_GapClosure/RESULTS.md` — genuinely distinct mechanism from `codex.hooks` (VS Code IDE-extension editor-event observer vs. native `hooks.json` forwarder) |

### Claude Code and Cursor: now also multi-technique

Claude Code and Cursor were originally Hooks-only in this framework because
no dedicated technique-specific test folders (`*_OTel/`, `*_MCPProxy/`,
etc.) existed for them, unlike Copilot/Antigravity/Codex. That gap has
since been closed using a real, field-level observability requirements
table (`reference_tables/observables_table_claude_cursor.csv`) built from
actual hands-on testing of both agents across all 6 techniques, plus the
existing real production code (`projectsetup/backend/cursor_proxy2.py`
for Cursor's real MCP/network-proxy sidecar, and this machine's own real
`~/.claude/projects/*.jsonl` transcripts for Claude Code's FS-watcher
surface):

| Agent | Technique | `collector_id` | Source of ground truth |
|---|---|---|---|
| Claude Code | OTel | `claude_code.otel` | CSV rows citing "OpenTelemetry" for Claude Code (~18 rows: `service.version`, `prompt.id`, `user.account_id`, `organization.id`, `claude_code.cost.usage`, `claude_code.lines_of_code.count`, `api_request.duration_ms`, `tool_decision`, `command_name`, `event.sequence`). No real captured OTLP export sample exists anywhere in this repo for Claude Code (unlike `Codex_OTel`/`Antigravity_OTel`) — `capture()` is honestly empty unless fed replay data. |
| Claude Code | CLI Wrapper | `claude_code.cli_wrapper` | `cli_wrapper_demo/wrapper.py` mechanism, narrowed to the `claude` binary; CSV's `operator_username`/`hostname` citations. **Live mode spawns a real `claude` child process** — pass `replay_events=[]` (or real data) explicitly in any offline/demo context to avoid blocking on a live interactive session. |
| Claude Code | MCP Proxy | `claude_code.mcp_proxy` | Ported from the Copilot/Antigravity/Codex MCP proxy mechanism (JSON-RPC wire protocol is agent-agnostic); `detect()` checks `~/.claude.json`/`.mcp.json` for real `mcpServers` entries. No Claude-Code-specific MCP wire capture exists in this repo, so its own demo (and `run_demo_full.py`) replays Copilot's real capture, clearly labeled as a different agent's data used only to exercise the parsing logic. |
| Claude Code | FS Watcher | `claude_code.fs_watcher` | `claude_code_hooks.py`'s real `transcript_path` convention (`~/.claude/projects/<project>/<session>.jsonl`); CSV citations for `input_tokens`/`output_tokens`/`cache_read_tokens`/`cache_write_tokens`/`thinking_blocks` as FS-watcher-sourced for Claude Code. This is the most solidly grounded of the four new Claude Code collectors — its demo replays this machine's own real transcript lines when present. |
| Cursor | OTel | `cursor.otel` | **No positive evidence found** — the CSV cites "OpenTelemetry" on zero rows under "Cursor source" (vs. ~18 for Claude Code), and no `Cursor_OTel/` folder exists. Weaker claim than Antigravity's empirically-confirmed dead end (no dedicated live test was run), but consistent: Cursor's real telemetry is native hooks + wire traffic, not OTLP. `capture()` is honestly empty unless fed replay data. |
| Cursor | CLI Wrapper | `cursor.cli_wrapper` | `cli_wrapper_demo/wrapper.py` mechanism; CSV citations for `operator_username`, `hostname`, `stderr`, `hung_seconds`, `is_developer_command`. No `Cursor_CLIWrapper/` folder exists (unlike Codex/Antigravity siblings) and no confirmed headless `cursor` CLI invocation — flagged UNVERIFIED for live mode. |
| Cursor | MCP Proxy | `cursor.mcp_proxy` | Ported directly from the real, already-in-production `projectsetup/backend/cursor_proxy2.py` mitmproxy sidecar (`CursorProxyTracker` class, `IGNORE_ROUTES`, usage-pattern regexes, output payload shape) — the richest-grounded of all the new Cursor collectors. Live mode requires the real `mitmdump -s cursor_proxy2.py -p 8080` deployment with Cursor's traffic actually routed through it; this framework doesn't spawn that itself. |
| Cursor | FS Watcher | `cursor.fs_watcher` | CSV rows 33/34 ("read from .git or a git command" for `git_branch`/`git_commit`) — implements real `read_git_ref()` git-reading logic, exercised live against whatever real git repo the collector happens to sit in. No captured sample file exists to replay (unlike Antigravity/Codex FS watchers), so `capture()` is legitimately empty when not run inside a git repo — confirmed in this demo, since `Project_Signal` itself isn't a git repo. |

**Local Hooks was deliberately not built for either agent.** Per the CSV,
every "local hooks" field cited for Claude Code and Cursor (session_id,
model call fields, tool call fields, prompt/response text, etc.) describes
the exact same payload their existing `claude_code_hooks.py`/
`cursor_hooks.py` already capture — there is no second, separate
IDE-extension-observer mechanism the way there is for Antigravity/Codex/
Copilot (which have real, distinct VS-Code-extension-based local
observers). Building `claude_code_local_hooks.py`/`cursor_local_hooks.py`
would just duplicate the existing Hooks collector, so they don't exist.

## The four-method interface contract

Every collector implements `core.Collector`:

| Method | Responsibility | Must NOT do |
|---|---|---|
| `collector_id -> str` | Stable identifier, e.g. `"claude_code.hooks"` | — |
| `detect() -> bool` | Cheap, side-effect-free check: is this agent/CLI present *right now* on this machine (binary on PATH, config dir present, hooks config present, etc.) | Network calls, writes, spawning processes |
| `activate() -> None` | One-time setup *after* a positive `detect()`: register hooks, start/attach a local listener, set env vars, spawn a wrapper process | Must be safe to call once per registry run |
| `capture() -> Iterator[RawEvent]` | Yield events as they arrive (or are replayed), with the source's field names/shapes **completely untouched** inside `raw_payload` | Normalize, rename fields, filter/drop fields, apply policy |
| `deactivate() -> None` | Clean shutdown: unregister hooks, close handles, stop spawned processes | Leave stray listeners/processes running |

`detect()` is always called before `activate()`; the registry only
`activate()`s collectors that returned `True`. `capture()` may be called
after `activate()` and before `deactivate()`.

`RawEvent` is a minimal dataclass:

```python
@dataclass
class RawEvent:
    timestamp: str        # ISO8601 UTC
    collector_id: str      # e.g. "cursor.hooks"
    agent: str             # e.g. "cursor"
    raw_payload: Dict[str, Any]   # untouched source payload
```

### Why ABC, not Protocol

`core.py` uses `abc.ABC` rather than `typing.Protocol` because collectors
are explicitly registered and lifecycle-managed by `CollectorRegistry` —
we want a real nominal "is-a Collector" relationship, and we want
`abstractmethod` to fail loudly at instantiation time if a collector is
incomplete, not just at static-type-check time.

## Where Policy Engine hooks and normalization are deliberately absent

This framework's job ends at "raw agent-shaped event, wrapped in a
`RawEvent` envelope, off the machine." Explicitly **not** implemented
here, by design, and left as clean seams for later pipeline stages:

- **Policy Engine** (not present): no permission/redaction/allow-deny
  checks happen anywhere in `core.py`, `registry.py`, or the collectors.
  The natural hook-in point is a new stage that consumes
  `CollectorRegistry.merged_capture()`'s `Iterator[RawEvent]` and yields a
  filtered/redacted `Iterator[RawEvent]` before anything is normalized or
  persisted.
- **Normalization** (not present): nothing here maps Claude's
  `hook_event_name` and Cursor's `event_type` onto one canonical event
  taxonomy, and nothing builds the "tracejson" run objects
  `webhook_server.py` builds (session state tracking, delegation/parent-
  child stitching, tool-call open/close pairing, etc.). That logic is
  real and works (see `webhook_server.py`'s `claude_event()` /
  `cursor_event()` handlers) — it's just a separate, later stage that
  would consume this framework's `RawEvent` stream (ideally
  post-Policy-Engine) as its input.

## How to add a new collector (template: Copilot Hooks)

A next-step `copilot_hooks.py` would follow the exact same shape as
`claude_code_hooks.py` / `cursor_hooks.py`:

```python
from core import Collector, RawEvent

class CopilotHooksCollector(Collector):
    def __init__(self, replay_events=None):
        self._replay_events = replay_events
        self._activated = False

    @property
    def collector_id(self) -> str:
        return "copilot.hooks"

    def detect(self) -> bool:
        # Real signals a Copilot CLI/agent is configured on this machine,
        # e.g. a .github/hooks/hooks.json or config/hooks.json (both exist
        # for real in this repo under Copilot/ and Copilot_VSCode_CLI/),
        # or the `gh copilot` / `copilot` binary on PATH.
        ...

    def activate(self) -> None:
        # Start/attach to whatever local listener Copilot's hook runner
        # posts events to.
        self._activated = True

    def capture(self):
        # Yield RawEvent(..., raw_payload=<untouched Copilot hook JSON>)
        # Copilot's own hook payloads use `hook_event_name` (per the real
        # samples in projectsetup/events/*.jsonl with cwd
        # "/private/tmp/copilot_hook_test"), so very little translation is
        # needed versus the Claude Code collector — but keep the fields
        # exactly as Copilot sends them; do not reshape them to match
        # Claude's shape.
        ...

    def deactivate(self) -> None:
        self._activated = False
```

Then register it alongside the others:

```python
registry.register(CopilotHooksCollector(replay_events=copilot_events))
```

No other file needs to change — `CollectorRegistry` is agent-agnostic by
construction.

## Cursor collector: documented known behavior (not bugs)

Carried over from this project's prior hands-on testing/gap-assessment of
Cursor's hook surface, and corroborated by comments already present in
`webhook_server.py`:

- Permission/gate-style events may **not fire at all** when Cursor is
  running in sandbox mode. A missing gate event under sandbox mode is
  expected absence, not a collector malfunction.
- Token usage **is** present, just not on a permission-style event — it
  rides on `afterAgentThought`/`composerSync` and `stop`/`sessionEnd`
  hooks (see `webhook_server.py`'s handling of a `usage` dict there), and
  separately via the `cursor_proxy2.py` mitmproxy sidecar's
  `network_context_capture` payloads.
- MCP-related hooks (`beforeMCPExecution`) only fire if the user actually
  has an MCP server configured in Cursor; on a machine with none
  configured, this collector will legitimately never see one.

## Known limitations / TODOs

These are honest, empirically-sourced gaps — not yet production-hardened.
Each is also called out inline as a comment in the relevant collector.

- **Copilot: `PostToolUse` does not reliably indicate real tool failure.**
  A failing shell command (confirmed: `ls /nonexistent`, exit code 1) still
  produces a `PostToolUse` event with `tool_result.result_type: "success"`;
  the failure text is only present as free text inside
  `tool_result.text_result_for_llm`. `PostToolUseFailure` never fired even
  once for this case in testing, despite being registered. There is no
  reliable, structured way for a consumer of `copilot.hooks` events to
  detect tool-call failure. (`Copilot/RESULTS.md` section 5.)
- **Copilot: three distinct payload shapes, dispatched by casing, not by
  event name.** `PreToolUse`/`PostToolUse` only fire under PascalCase
  registration; `permissionRequest` only fires under camelCase
  registration, in its own third shape. A hook config that registers only
  one casing will silently miss events. `copilot_hooks.py` documents this
  but a `.github/hooks/hooks.json` deployed for real must still register
  every event in both casings (see `Copilot/config/hooks.json`).
- **Antigravity: global + workspace `hooks.json` duplication is only
  best-effort deduplicated, not solved.** Both scopes fire independently
  for every event when both are configured with the same hook. The real
  captured payloads carry no delivery-unique id, so `antigravity_hooks.py`
  falls back to a content-hash dedup that can false-collide on genuinely
  distinct events that happen to produce byte-identical payloads early in
  a session. Do not treat `antigravity.hooks` capture() output as
  guaranteed-exactly-once. (`Antigravity/RESULTS.md` section 6a.)
- **Antigravity: `toolCall.args` keys are PascalCase inside an otherwise
  camelCase envelope**, contradicting the vendor's own "all keys are
  camelCase" doc claim. Any downstream normalizer keying on casing
  assumptions will break on this field. (`Antigravity/RESULTS.md` section 3.)
- **Antigravity: `terminationReason` values don't match documented
  examples** — confirmed real value `"NO_TOOL_CALL"` (SCREAMING_SNAKE_CASE),
  not one of the docs' `model_stop`/`max_steps_exceeded`/`error` examples.
  Treat the enum as open/incomplete, not exhaustive.
- **Codex: hooks require trust approval or fail completely silently, with
  zero warning** — worse than either Copilot's or Antigravity's silent-
  failure classes, because it produces no signal at all. `codex_hooks.py`'s
  `activate()` checks for a persisted `hooks.state.*.trusted_hash` entry in
  `~/.codex/config.toml` and raises `CodexTrustNotGrantedError` instead of
  activating into a silent no-op — but there is no way to *grant* trust
  programmatically (no CLI subcommand, no writable RPC method; only the
  interactive `/hooks` TUI or the `--dangerously-bypass-hook-trust` flag).
  (`Codex/RESULTS.md` section 1, `Codex_GapClosure/RESULTS.md`.)
- **Codex: `model`, `permission_mode`, and `turn_id` are each missing from
  at least one real event type**, despite being documented as always-
  present common fields. Concretely: `model` is absent from `SessionEnd`;
  `permission_mode` is absent from `SessionEnd`, `PreCompact`, and
  `PostCompact`; `turn_id` is absent from `SessionStart` and `SessionEnd`.
  (`Codex/RESULTS.md` section 4.)
- **Codex: `tool_response` has no exit-code/success field at all** — a
  failing command with no stdout/stderr (`false`, exit code 1) produces
  `tool_response == ""`, byte-for-byte identical to a successful command
  with no output. Same class of issue as Copilot's `PostToolUse` gap.
  (`Codex/RESULTS.md` section 5.)
- **None of the collectors have been exercised against a live agent
  session** — only against the real captured JSONL/config data from prior
  research. `detect()` was run for real on the machine used to build this
  (see demo output), but `activate()`/`capture()`'s live-listener code
  paths are unimplemented placeholders across every collector in this
  framework, not just the newest ones.
- **Copilot OTel has no real capture for plain Copilot Chat** — only the
  separate CLI-session path (`Copilot_VSCode_CLI`) has a real positive
  OTLP export to replay. `copilot_otel.py`'s `__main__` demo uses that CLI-
  session evidence; a deployment targeting plain Copilot Chat's own
  `github.copilot.chat.otel.*` settings should expect this collector to
  legitimately yield nothing, per confirmed real-world testing.
- **Antigravity OTel is a confirmed dead end, not a bug to fix** —
  `antigravity_otel.py`'s `capture()` yields 0 events with no replay data
  fed in, by design: there is no real OTLP payload anywhere in this
  project's evidence to replay, because Antigravity does not export one.
- **Copilot FS Watcher is unverified/best-effort** — no dedicated Copilot
  FS-watcher research exists; do not treat its watch-root list as
  confirmed correct the way the other 19 collectors' sources are.
- **MCP Proxy collectors will legitimately see nothing for
  delegation/sub-agent scenarios** — confirmed for both Antigravity and
  (per `Codex_MCPProxy`'s `hooks_crossref_delegation.jsonl`) partially for
  Codex: `invoke_subagent`-style delegation does not cross the MCP
  transport captured here. Use the Hooks or FS Watcher collector, not MCP
  Proxy, if delegation visibility matters.
- **`claude_code_cli_wrapper.py`'s live mode spawns a real `claude` child
  process and blocks reading its pty output.** Instantiating it with no
  `replay_events` (the default) is correct for a real deployment but will
  hang any offline/demo/test context that doesn't expect a live interactive
  session — always pass `replay_events=[]` or real data outside of an
  actual live-deployment context. `run_demo_full.py` does this; be careful
  if you write a new script that constructs this collector with defaults.
- **Antigravity's global+workspace `hooks.json` duplication is only
  best-effort deduplicated in `antigravity_hooks.py` (see above), and
  `antigravity_local_hooks.py`/`antigravity_fs_watcher.py` do not attempt
  to cross-deduplicate against it at all** — running all three Antigravity
  collectors together in the same registry can currently yield up to 3x
  reporting of the same underlying agent action, from 3 different raw
  shapes. Downstream correlation is a normalization-stage problem, not
  solved here.

## Discovery Engine

`discovery_engine.py` is a standalone, registry-independent machine scanner.
It is a **separate component from `CollectorRegistry`**, built for the
Deployment phase's "identify tool capabilities" step (feeding the later
Configuration phase's decision of which of the 28 collectors above to
actually register and activate).

### The boundary: Discovery Engine finds and reports; it does not activate

This is the single most important thing to understand about this module:

- The Discovery Engine **scans and reports only**. It never calls
  `CollectorRegistry.register()`, never calls any collector's `activate()`,
  and never writes/creates/modifies anything on disk or in the environment.
  Every check it performs is read-only: directory/file existence checks,
  `shutil.which()` PATH lookups, and — for technique viability — calling
  the real, already-vetted `detect()` method (never `activate()`) on a
  freshly constructed, never-activated instance of the actual collector
  class for that agent/technique pair.
- Deciding which of the 28 collectors to actually instantiate-for-real
  (with live config, not defaults) and register into a `CollectorRegistry`
  based on a `DiscoveryReport`'s findings is a deliberately separate,
  later wiring step — **not implemented in this module**.
- It is safe to run as a periodic health/coverage check (the Continuous Ops
  phase's "monitor health & coverage" step), not just once at install time,
  because it has no side effects and is idempotent — running it twice in a
  row produces the same `viable_techniques` for every agent (verified: see
  Real output below, `run 2` timing).

### What it does

1. **Independently detects which of the five agents are installed** —
   `_detect_agent_installation()` walks PATH (`shutil.which`), common config
   directories (`~/.claude`, `~/.cursor`, `~/.copilot`, `~/.gemini/config`,
   `~/.codex`), the macOS `/Applications/*.app` bundles for Cursor and
   Antigravity, and the VS Code extensions directory
   (`~/.vscode/extensions`) for Copilot's and Codex's IDE extensions. This
   check does **not** require any `Collector` subclass to exist for an
   agent — it's a standalone scan, independent of
   `AGENT_TECHNIQUE_COLLECTORS`, so an agent with zero collectors built for
   it can still be correctly reported as installed.
2. **For each agent, identifies which of the 6 techniques are viable** —
   using the static `AGENT_TECHNIQUE_COLLECTORS` map of
   `(agent, technique) -> (module, class)` covering all 28 real collectors
   in this folder, it imports the module, constructs the collector with its
   default (verified side-effect-free) constructor, and calls its own real
   `detect()` — reusing the same empirically-grounded logic each collector
   already has, rather than re-deriving a second, driftable copy of it. A
   `(claude_code, local_hooks)` / `(cursor, local_hooks)` pair with no
   collector built (see "Local Hooks was deliberately not built..." above)
   is reported as `viable=False` with an explicit
   `"no collector implemented..."` note, not silently omitted.
3. **Returns a structured `DiscoveryReport`** — a dataclass containing one
   `AgentDiscovery` per agent (`agent`, `installed`, `confidence`,
   `install_paths`, `installation_signals`, `viable_techniques`,
   `technique_results` — each `TechniqueResult` carries the exact
   `collector_id` string a later wiring step would use to look up which
   real collector class to instantiate for real). `confidence` is a
   transparent, hand-computed `[0.0, 1.0]` score (fraction of independent
   installation signals that fired, plus a small bonus for having at least
   one viable technique) — not a black-box ML score.
4. **`claude_code_cli_wrapper.py`'s documented live-process hazard is
   carried through explicitly** — the Discovery Engine still constructs and
   `detect()`s it (its `__init__` does nothing but store `None` defaults,
   verified safe), but flags the result with an explicit warning note
   never to call `activate()` on a default-constructed instance outside a
   real deployment, exactly mirroring the hazard already documented below
   under "Known limitations."

### Real output from this machine

Run with `python3 discovery/discovery_engine.py` from inside
`collector_framework/` (also importable as `from discovery.discovery_engine
import DiscoveryEngine, scan_this_machine`). All five
agents were genuinely found installed on the machine this was built/tested
on — this is real output, not a theoretical example:

```
[claude_code] FOUND  (confidence=1.0)
  install_paths: claude_binary=/usr/local/bin/claude, claude_home=~/.claude, claude_json=~/.claude.json
  viable_techniques: ['hooks', 'otel', 'cli_wrapper', 'mcp_proxy', 'fs_watcher']
  (local_hooks: viable=False -- no collector implemented for this pair, by design)

[cursor] FOUND  (confidence=0.73)
  install_paths: cursor_app_bundle=/Applications/Cursor.app, cursor_home=~/.cursor
  installation_signals: binary_on_path=False, mac_app_bundle=True, config_dir=True
  viable_techniques: ['hooks', 'otel', 'cli_wrapper', 'mcp_proxy']
  (fs_watcher: viable=False -- no .git-tracked project root in cwd at scan time;
   local_hooks: viable=False -- no collector implemented for this pair, by design)

[copilot] FOUND  (confidence=0.73)
  install_paths: copilot_binary=/usr/local/bin/copilot, copilot_home=~/.copilot
  installation_signals: binary_on_path=True, config_dir=True, vscode_extension=False
  viable_techniques: ['hooks', 'otel', 'cli_wrapper', 'mcp_proxy', 'fs_watcher', 'local_hooks']

[antigravity] FOUND  (confidence=1.0)
  install_paths: agy_binary=/usr/local/bin/agy, antigravity_app_bundle=/Applications/Antigravity.app, gemini_config_dir=~/.gemini/config
  viable_techniques: ['hooks', 'otel', 'cli_wrapper', 'mcp_proxy', 'fs_watcher', 'local_hooks']

[codex] FOUND  (confidence=1.0)
  install_paths: codex_binary=/usr/local/bin/codex, codex_home=~/.codex, vscode_extension=~/.vscode/extensions/openai.chatgpt-26.727.40816-darwin-x64
  viable_techniques: ['hooks', 'otel', 'cli_wrapper', 'mcp_proxy', 'fs_watcher', 'local_hooks']

SUMMARY: 5/5 agents found: ['claude_code', 'cursor', 'copilot', 'antigravity', 'codex']
```

Note the two legitimate `viable=False` results above are not fabricated
gaps: Cursor's `fs_watcher` collector's real `detect()` requires a
`.git`-tracked project root under the current working directory (per its
own docstring), which was not true of the directory the scan was run from;
and both `local_hooks` gaps for Claude Code/Cursor reflect the deliberate
"not built" decision documented earlier in this README, not a scan failure.
If any of the five agents were genuinely absent from a machine, this module
reports `installed: false` for it rather than fabricating a positive — the
"5/5 found" result above reflects this specific machine's real state, not
an assumption baked into the code.

Repeated-run safety was verified directly: calling `scan_this_machine()`
twice in the same process produced byte-identical `viable_techniques` lists
for every agent (0.05s first run, 0.009s second run — no caching needed,
it's just that cheap), confirming it is safe to call from a periodic
Continuous Ops health check without any de-duplication or debounce logic.

## Policy Engine

`policy_engine.py` is a standalone `PolicyEngine`/`PolicyConfig` pair that
decides which discovered-and-viable `(agent, technique)` pairs from a
`discovery_engine.DiscoveryReport` are actually **approved to activate**,
and records a traceable reason for every single decision.

### Scope note: this is not "all governance"

Domain-based access control — which team/org/user gets to *see* the data
once it's flowing — is a separate, already-decided database/access-layer
concern elsewhere in the project, **not** part of this component. Policy
Engine here only answers one narrower question: *is this collection
technique allowed to run on this machine at all?*

### The boundary: Policy Engine decides and reports; it does not discover, does not activate

The same pattern used for the Discovery Engine applies here, one stage
later in the pipeline:

- Policy Engine **never discovers anything itself** — it takes an
  already-produced `DiscoveryReport` as a plain input argument to
  `PolicyEngine.evaluate()`. It never imports a collector class, never
  calls `detect()`, never touches the filesystem or PATH on its own (the
  one filesystem read it does is `PolicyConfig.load()`, an explicit,
  separate step for loading the *policy file itself* — not for discovery).
- Policy Engine **never activates anything itself** — its output,
  `PolicyDecisionReport`, is a decision + reason for every pair. Actually
  calling `CollectorRegistry.register()` / `activate()` for the
  approved pairs (`PolicyDecisionReport.approved_pairs()`) is a
  deliberately separate, later wiring step, not implemented here — the
  exact same boundary already drawn between Discovery Engine and the
  Registry.

### Config format: JSON, not YAML

This whole framework is deliberately "dependency-free (Python 3 stdlib
only)" (see the top of this README). PyYAML is not installed in this
project's environment and is not a stdlib module — adding it as a new
dependency just for a policy file format would break that constraint for
a governance-critical component. Every existing collector in this package
already reads JSON config (`hooks.json`, `mcp_config.json`,
`config.toml`'s sibling files, etc.), so JSON keeps the Policy Engine
config format consistent with every other config file this project already
parses. JSON has no native comment syntax; the workaround is a convention,
not a hack: **every rule requires an explicit `"reason"` string field**, so
auditability lives in the data itself, not in comments that a JSON parser
would have to strip.

### Default policy: default-deny at the engine level, `PolicyConfig.default()`

`PolicyConfig.default()` (used when no policy file is supplied at all) is
**default-deny with zero rules** — every pair is denied with an honest
`"no policy file loaded; default-deny fallback"` reason. This is a
governance/telemetry product; the safer failure mode for a security-
relevant control is "an unreviewed technique does not activate," not
"it silently activates and someone has to notice." Default-deny at the
engine level also matches the project's auditability requirement: every
approval has to be an explicit, reasoned line in *some* policy file,
engine-level default-allow could never guarantee that.

A **loaded policy file** is free to set `"default_mode": "allow"` for
itself — that is a different, explicit, recorded choice made by whoever
wrote that specific file (see `sample_policy.json` below), clearly
distinguished in its own `default_reason` text from the engine's built-in
no-policy-loaded fallback. Nothing about `PolicyConfig.default()`'s
deny-by-default posture is silently overridden — a real policy author
still has to explicitly write `"default_mode": "allow"` for their file
to behave that way, and that choice is itself visible and auditable in
the JSON.

### Config shape and rule precedence

```json
{
  "default_mode": "deny",
  "default_reason": "...",
  "agent_rules": {
    "<agent>": { "allowed": true|false, "reason": "..." }
  },
  "technique_rules": [
    { "agent": "<agent>"|"*", "technique": "<technique>"|"*", "allowed": true|false, "reason": "..." }
  ]
}
```

Decision precedence, most to least specific (implemented in
`PolicyEngine._decide()` / `_best_matching_technique_rule()`):

1. `agent_rules[agent].allowed == false` — denies **every** technique for
   that agent outright (e.g. "Antigravity is not yet approved org-wide"),
   regardless of anything in `technique_rules`.
2. The most specific matching `technique_rules` entry wins: exact
   agent + exact technique beats wildcard-agent + exact technique
   (e.g. `"MCP Proxy disabled everywhere"`) beats exact-agent +
   wildcard-technique beats a fully wildcard catch-all rule.
3. If nothing matches, `default_mode`/`default_reason` applies.

A pair Discovery Engine reported as `viable=False` (no collector built for
that agent/technique, or `detect()` returned `False`/errored) is **not**
silently dropped — it is still emitted in the decision report with
`decided_by="not_viable"` and the original discovery-time reason, since
policy has nothing to decide about something that can't run anyway but
that fact should still be visible in the final report, not disappear.

### Real sample policy (`sample_policy.json`)

Four judgment-grounded rules, each citing the real, already-documented
evidence behind it (not arbitrary examples):

| Rule | Scope | Why |
|---|---|---|
| `agent_rules.antigravity.allowed = false` | Antigravity, all 6 techniques | Newest/least mature integration: `antigravity_otel.py` confirms OTel is an architecturally dead end for this agent, and `antigravity_hooks.py` documents unresolved global+workspace `hooks.json` double-counting with only best-effort dedup. Deny the whole agent, not technique-by-technique, until that's fixed. |
| `technique_rules[]` `agent=*, technique=mcp_proxy` | MCP Proxy, every agent | The most invasive technique implemented in this package: `copilot_mcp_proxy.py`/`antigravity_mcp_proxy.py`/`codex_mcp_proxy.py`/`cursor_mcp_proxy.py` all capture full tool-call arguments/results verbatim off the wire, with zero redaction at the collector layer (redaction is a later, not-yet-built stage per `core.py`). |
| `technique_rules[]` `agent=cursor, technique=cli_wrapper` | Cursor CLI Wrapper only | `cursor_cli_wrapper.py`'s own `detect()` docstring flags this UNVERIFIED — no `Cursor_CLIWrapper/` research folder and no confirmed headless `cursor` CLI invocation exists, unlike its Codex/Copilot/Antigravity/Claude Code siblings. |
| `technique_rules[]` `agent=codex, technique=otel` | Codex OTel only | `Codex_OTel/RESULTS.md` documents a confirmed `user_email`/`user_account_id` PII leak and a hardcoded `success:"true"` bug in the real OTLP export. A data-quality/PII call, not a maturity one — hence only this one Codex technique is denied, not the whole agent. |
| `default_mode: "allow"` | everything else Discovery Engine finds viable | The explicit, recorded policy-author choice for this specific deployment (see "Default policy" above for why this isn't the engine's own built-in default). |

### Real output from this machine

Run with `python3 policy/policy_engine.py [path-to-policy.json]` from
inside `collector_framework/` (defaults to `sample_policy.json` alongside
it). This reuses the real `discovery_engine.scan_this_machine()` from the
previous section — no
discovery logic is reimplemented here:

```
SUMMARY: 15 (agent, technique) pairs approved for activation:
  - claude_code.hooks
  - claude_code.otel
  - claude_code.cli_wrapper
  - claude_code.fs_watcher
  - cursor.hooks
  - cursor.otel
  - copilot.hooks
  - copilot.otel
  - copilot.cli_wrapper
  - copilot.fs_watcher
  - copilot.local_hooks
  - codex.hooks
  - codex.cli_wrapper
  - codex.fs_watcher
  - codex.local_hooks

Denied (all with real, traceable reasons -- see full run for text):
  - claude_code.mcp_proxy          (technique_rule: MCP Proxy denied everywhere)
  - cursor.cli_wrapper             (technique_rule: unverified for Cursor)
  - cursor.mcp_proxy               (technique_rule: MCP Proxy denied everywhere)
  - copilot.mcp_proxy              (technique_rule: MCP Proxy denied everywhere)
  - antigravity.* (all 6)          (agent_rule: whole agent denied)
  - codex.otel                     (technique_rule: PII leak + success:"true" bug)
  - codex.mcp_proxy                (technique_rule: MCP Proxy denied everywhere)

Skipped as not-viable per Discovery Engine (policy made no decision, still surfaced):
  - claude_code.local_hooks   (no collector implemented for this pair)
  - cursor.fs_watcher         (no .git-tracked project root in cwd at scan time)
  - cursor.local_hooks        (no collector implemented for this pair)
```

15 approved, 12 denied-by-policy (6 of them from the Antigravity agent-wide
rule alone), 3 skipped as not-viable — this is real output from this
specific machine's real `DiscoveryReport`, not a fabricated example.
Verified idempotent: calling `PolicyEngine.evaluate()` twice against the
same `DiscoveryReport` in the same process produced identical
`(approved, reason)` for all 30 decision entries.

## Activation Engine

`activation/activation_engine.py` is the wiring step every earlier
component in this package deliberately deferred: it takes a
`PolicyDecisionReport`'s approved `(agent, technique)` pairs and actually
registers and `activate()`s the real collectors into a live
`CollectorRegistry`.

### The boundary: Activation Engine activates; it does not discover, does not decide

Completing the same pattern used throughout this package:

- **Does not discover** — reuses Discovery Engine's own
  `AGENT_TECHNIQUE_COLLECTORS` map (imported, not re-derived) to know which
  module/class implements a given `(agent, technique)` pair. It never
  scans the machine itself.
- **Does not decide** — only ever acts on `PolicyDecisionReport.approved()`.
  It has no policy logic of its own and cannot override a denial; a pair
  Policy Engine denied simply never reaches `ActivationEngine.wire()`.
- **Does activate, and re-validates before doing so** — `wire()` calls the
  registry's own `activate_detected()`, which re-runs each collector's real
  `detect()` at wiring time (not reusing Discovery Engine's possibly-stale
  earlier scan) and only `activate()`s the ones still true, with the
  registry's existing per-collector failure isolation. A pair approved a
  few minutes ago that's since become unavailable (e.g. the agent was
  uninstalled, or — as seen in the real run below — a trust/config
  precondition isn't met) doesn't raise; it's simply absent from
  `WiringResult.activated`, and if it fails specifically during
  `activate()` that failure is isolated and surfaced, not fatal.

### The hazard, corrected

Earlier sections of this README named exactly one collector
(`claude_code_cli_wrapper.py`) as spawning a real child process if
constructed with no replay data. Building this component required reading
every CLI Wrapper collector's actual `activate()` body to make wiring
generic and safe, and that turned up two more real cases that weren't
previously called out by name: **`copilot_cli_wrapper.py` and
`antigravity_cli_wrapper.py` both fork+exec a real child process the same
way** (`if self._replay_events/self._replay_path is None: <spawn>`).
`codex_cli_wrapper.py` and `cursor_cli_wrapper.py` are, by contrast,
confirmed non-hazardous — both are explicitly documented, unimplemented
live-mode placeholders whose `activate()` only sets `self._activated =
True`. `discovery_engine.py`'s `LIVE_MODE_HAZARD_COLLECTORS` set has been
corrected to list all three real cases, and both Discovery Engine's
per-technique notes and Activation Engine's `mode="live"` guard now
reflect this.

### Replay-safe construction, generically

Rather than hardcoding special-case logic for those three collectors,
`ActivationEngine` inspects every collector's `__init__` signature
(`inspect.signature`) and, for any parameter whose name indicates it takes
a *list* of replayable records (`replay_events`, `replay_records`,
`replay_flows` — confirmed the real naming convention across every one of
the 28 collectors), passes an explicit value: real data if supplied via
`replay_data={(agent, technique): [...]}`, otherwise an explicit empty
list — **never** left at the `None` default that triggers a live spawn.
Parameters that take a single optional replay object instead of a list
(`replay_path`, e.g. `antigravity_local_hooks.py`) are deliberately left
alone — their own `activate()` fails closed (a caught `RuntimeError`
asking for the `watchdog` package) rather than spawning anything, so
there's no equivalent hazard to neutralize there.

`mode="live"` (bare, default construction — what a real deployed machine
would actually want) is supported, but refuses to construct any of the
three real hazard collectors unless the caller explicitly passes
`allow_live_hazards=True`.

### Real output from this machine: the full pipeline, end to end

`demos/run_pipeline_end_to_end.py` runs all four components together for
the first time — real Discovery Engine scan, real Policy Engine decision
against `sample_policy.json`, real Activation Engine wiring, real merged
`capture()`, real `deactivate_all()`. Real Hooks replay data is fed in
(reusing `run_demo_all_five.py`'s own loaders against this project's real
captured `*.jsonl` files); every other approved pair is wired with an
explicit empty replay list, which is why only the three Hooks collectors
with real data show non-zero counts below — that's an honest reflection of
which techniques have live agent traffic during this offline demo, not a
wiring bug:

```
STAGE 1: DiscoveryEngine.scan()
  agents found: ['claude_code', 'cursor', 'copilot', 'antigravity', 'codex']

STAGE 2: PolicyEngine.evaluate()
  approved pairs (15): claude_code.{hooks,otel,cli_wrapper,fs_watcher},
  cursor.{hooks,otel}, copilot.{hooks,otel,cli_wrapper,fs_watcher,local_hooks},
  codex.{hooks,cli_wrapper,fs_watcher,local_hooks}

STAGE 3: ActivationEngine.wire()
  registered (15): [... all 15 approved pairs ...]
  activated  (14): [... all except codex.hooks ...]
  [registry] codex.hooks failed at activate: CodexTrustNotGrantedError:
    no evidence of hook trust found in ~/.codex/config.toml -- real Codex
    hooks fail COMPLETELY SILENTLY without this. Isolated by the registry,
    did not crash the run.

STAGE 4: registry.merged_capture()
  claude_code.hooks -> 323 RawEvent(s)
  copilot.hooks     -> 40 RawEvent(s)
  cursor.hooks      -> 104 RawEvent(s)
  TOTAL: 467 RawEvents across 14 activated collectors

STAGE 5: registry.deactivate_all()
  All active collectors deactivated cleanly.
```

This is real, not fabricated: `codex.hooks` genuinely failed its real
`activate()` on this machine because this `CODEX_HOME` has never gone
through Codex's interactive `/hooks` trust-approval flow — exactly the
documented hazard `codex_hooks.py` was built to fail loudly on instead of
silently, and the registry's failure isolation caught it and kept the
other 13 activated collectors running. Total runtime was ~0.2s with no
hang, confirming none of the three real CLI Wrapper hazards were
triggered — a live spawn would have blocked waiting for interactive input.

## Update Engine

`update/update_engine.py` is the piece meant to stop the rest of this
package from silently rotting as agent vendors ship changes over time --
the Deployment-phase component sitting between Activation Engine and final
packaging, and the concrete mechanism underneath Continuous Ops's
"monitor health & coverage" step.

### Scoped honestly: no fabricated update server

There is no real update registry/CDN for this project to query, and
building this module by pretending one exists would fabricate
infrastructure the same way a made-up telemetry payload would fabricate
evidence -- something every other component in this package has explicitly
refused to do. What Update Engine does instead is real and fully local:
treat a directory tree (a newer checkout, a staged release directory a
real deployment pipeline would drop files into) as a **candidate**, and
manage the transition from the currently-**installed** tree to it using
only hashing and file operations.

### The boundary: manages the package's own files; does not touch what's running

Same pattern as every other component here, applied to a different axis:

- **Does not decide what's active** — an update changes what *code* exists
  on disk. It has nothing to do with which `(agent, technique)` pairs
  Policy/Activation Engine currently have registered or activated; those
  are entirely separate, unaffected concerns.
- **Never auto-deletes** — a file present in the installed tree but absent
  from the candidate is reported in `UpdateDiff.removed` and
  `ApplyResult.skipped_removed`, but `apply_update()` never deletes it.
  Deleting collector code unattended is a strictly higher-risk operation
  than adding or changing it; removal requires a separate, explicit step
  this module deliberately does not implement.
- **Every apply is reversible** — `apply_update()` backs up whatever it's
  about to overwrite (or notes that a file is new, with nothing to back
  up) into a timestamped backup directory before touching anything, and
  `rollback()` reverses exactly that recorded operation, byte-for-byte.

### The five operations

1. **`snapshot(root)`** — hashes (sha256) every tracked file (`.py`/`.json`
   only, matching this framework's actual footprint; `__pycache__`,
   `.git`, and `reference_tables/` are excluded) into a `Manifest`, grouped
   by component (`core`, `collectors.<agent>`, `discovery`, `policy`,
   `activation`, `demos`, `update`). The manifest's digest (a short hash of
   all file hashes together) **is** the package's version — concrete and
   independently verifiable, not a self-reported semver string nobody
   enforces.
2. **`diff(installed, candidate)`** — compares two manifests, returning
   `added`/`changed`/`removed` file lists, grouped by component. This is
   the real staleness/drift detection a periodic Continuous Ops check
   would run.
3. **`apply_update(installed_root, candidate_root, backup_root, diff)`** —
   copies every added/changed file from the candidate into the installed
   tree, backing up anything it overwrites first.
4. **`verify(root)`** — re-imports the updated tree's own `discovery`
   package fresh (clearing any stale cached modules first) and runs a
   real, side-effect-free `DiscoveryEngine().scan()` against it. An update
   that breaks basic importability, or makes discovery raise, is caught
   here before Policy/Activation Engine ever touch the updated tree.
5. **`rollback(installed_root, backup_root, apply_result)`** — restores
   every file `apply_update()` backed up, and removes any file it applied
   that was newly added (no backup exists for those, by definition) --
   reversing that specific apply exactly.

### Real output from a real (but safely sandboxed) run

`demos/run_update_demo.py` never touches the real `collector_framework/`
tree this file lives in — it copies the whole real package into two
temporary directories (an "installed" copy and a "candidate" copy),
applies a few realistic, clearly-labeled changes to the candidate copy
only (flip `sample_policy.json`'s `codex.otel` rule from denied to
approved — exactly the real unblock condition that rule's own `reason`
field already describes — add a `CHANGELOG.json`, and simulate a stale
file only the old install has), then runs the full lifecycle:

```
STAGE 1: snapshot()
  installed digest: 8c7196dc55577209  (53 tracked files)
  candidate digest: 7be5d2032fd72da7  (53 tracked files)

STAGE 2: diff()
  added   (1): ['update/CHANGELOG.json']
  changed (1): ['policy/sample_policy.json']
  removed (1): ['update/_demo_stale_file.json']
  unchanged: 51 files

STAGE 3: apply_update()
  applied   (2): ['update/CHANGELOG.json', 'policy/sample_policy.json']
  backed_up (1): ['policy/sample_policy.json']
  skipped_removed (reported, NOT deleted): ['update/_demo_stale_file.json']
  errors: []

STAGE 4: verify()
  ok: True, agents_scanned: 5, error: None
  post-update codex.otel rule 'allowed': True (was False before update)

STAGE 5: rollback()
  restored           (1): ['policy/sample_policy.json']
  removed_new_files  (1): ['update/CHANGELOG.json']
  errors: []

STAGE 6: verify rollback was exact
  post-rollback diff vs original: has_changes=False
  original digest:      8c7196dc55577209
  post-rollback digest: 8c7196dc55577209
  digests match: True
```

Confirmed for real, not asserted: the real `policy/sample_policy.json` on
disk was checked (`codex.otel` still `allowed: false`) both before and
after running this demo, and a filesystem-mtime check across the whole
`collector_framework/` tree turned up nothing newer than this README
except the two new files this feature itself added
(`update/update_engine.py`, `demos/run_update_demo.py`) — the demo's
temp-copy isolation genuinely holds.

## Deployable Package

`package/cli.py` + `package/build_exe.py` are the final "Build the package"
step on the roadmap: **structure it as a deployable exe package**. This is
deliberately the last, thinnest layer -- it adds zero new detection/
decision/activation/update logic. It only gives the five already-built,
already-tested components (Collector Framework, Discovery Engine, Policy
Engine, Activation Engine, Update Engine) a single, stable, non-interactive
command-line surface, then freezes that into one binary an MDM tool
(Intune/Jamf, per the roadmap) can push to a device without that device
needing its own Python install.

### The `signal-agent` CLI

```
signal-agent scan                                    # Discovery Engine only
signal-agent decide [--policy PATH]                  # Discovery + Policy Engine
signal-agent run [--live] [--allow-live-hazards]      # full pipeline: discover -> decide -> activate -> capture -> deactivate
signal-agent version                                  # this install's real, verifiable manifest digest
signal-agent update check   --installed DIR --candidate DIR
signal-agent update apply   --installed DIR --candidate DIR --backup DIR
signal-agent update rollback --installed DIR --backup DIR
```

Every command accepts a top-level `--json` flag for machine-readable
output, and every command exits non-zero on failure -- built specifically
so an MDM/orchestration layer can script against it rather than parse
human text. `run`'s default mode is `replay` (side-effect-free, safe to
run anywhere, including as this exe's own smoke test); `--live` is what
an actual deployed device would use to really register and activate
collectors against real running agent processes, matching Activation
Engine's own `mode="live"` hazard-guarding (`--allow-live-hazards` is
required to construct the collectors documented as live-process-spawning
hazards, same as calling `ActivationEngine` directly).

### Why `update` always requires `--installed` explicitly

A frozen, `--onefile` executable's own Python modules are embedded inside
the binary at build time -- they are not real, individually-hashable files
on disk at runtime, so the exe has nothing meaningful to snapshot about
*itself*. `signal-agent update ...` therefore always operates on an
explicit, separate, real on-disk directory the caller names -- exactly the
directory a deployment tool would already be managing as "the current
install" -- rather than pretending the exe can introspect its own
internals. `signal-agent version` similarly reports a version digest that
`build_exe.py` computed from the real source tree *at build time* and
baked into the binary as a constant, not a runtime filesystem walk.

### Building it

```bash
python3 -m pip install pyinstaller
python3 package/build_exe.py
```

Produces `dist/signal-agent` (or `dist/signal-agent.exe` on Windows).

**Honest limitation -- no cross-compilation.** PyInstaller does not
cross-compile: running `build_exe.py` on macOS produces a macOS binary,
on Windows a Windows `.exe`, on Linux a Linux binary. Since Intune targets
Windows and Jamf targets macOS, a real rollout needs both. This
development machine is macOS-only with no Windows access, so the
`dist/signal-agent` binary checked in here is a real, functioning macOS
build, verified below -- it is not a stand-in for Windows.

**Closing the Windows gap: `.github/workflows/build-exe.yml`.** A CI
matrix workflow (at the repo root, one level above `collector_framework/`)
builds and smoke-tests `signal-agent` on both a real `windows-latest` and
a real `macos-latest` GitHub Actions runner -- actual Windows/macOS VMs,
not an emulation shortcut -- on every push to `main` that touches this
package, or on manual dispatch. Each job runs `package/build_exe.py`,
then actually executes the frozen binary's `version` and `scan` commands
on that platform as a smoke test (a build that produces a broken/dead
binary fails CI, not just "builds successfully"), then uploads the binary
as a build artifact. This workflow is written and ready, but **has not
run yet** as of this writing -- this repository is not yet pushed to
GitHub. Once pushed, the first workflow run will produce the first real,
CI-verified Windows `signal-agent.exe` this project has ever had.

### Real output from the actual frozen binary on this machine

Every command below ran against the real `dist/signal-agent` binary, not
`cli.py` via the Python interpreter:

```
$ ./dist/signal-agent version
installed version digest (baked in at build time): 78b262d65082a33c
tracked files at build time: 55

$ ./dist/signal-agent decide
claude_code hooks        APPROVED   (default) ...
claude_code mcp_proxy    DENIED     (technique_rule) MCP Proxy is the most invasive technique ...
...

$ ./dist/signal-agent run
[copilot.otel] WARNING: OTEL_EXPORTER_OTLP_ENDPOINT is unset. ...
[registry] codex.hooks failed at activate: CodexTrustNotGrantedError: ...
mode: replay
registered (15): [...]
activated  (14): [...]
events captured this run: 0
```

Identical results to running the same commands via `python3 package/cli.py`
directly against source -- confirming the freeze changed nothing about
behavior. One real bug this surfaced and fixed during development: the
first frozen build failed `decide`/`run` with
`ModuleNotFoundError: No module named 'collectors'`, because
`discovery_engine.py` loads all 28 collector classes via
`importlib.import_module()` with a dynamically-built module name string
(see `AGENT_TECHNIQUE_COLLECTORS`) -- PyInstaller's static import-graph
analysis cannot see through that, so it silently left the whole
`collectors` package out of the bundle. Fixed with
`--collect-submodules collectors` in `build_exe.py`, then re-verified for
real against the rebuilt binary.

`update apply`/`update check`/`update rollback` were also verified for
real against the frozen binary, run against temporary copies of the real
`core`/`discovery`/`policy`/`activation`/`update`/`collectors`/`demos`
directories (never the real `collector_framework/` tree) -- add, apply,
verify (`ok: True, agents_scanned: 5`), and rollback all behaved
identically to the source-run Update Engine demo.

## Running the demo

Run from inside `collector_framework/`:

```bash
python3 demos/run_demo.py                    # Claude Code + Cursor only
python3 demos/run_demo_all_five.py           # all five Hooks collectors
python3 demos/run_demo_full.py               # all 28 collectors, every technique implemented so far
python3 demos/run_pipeline_end_to_end.py     # the real, full pipeline: scan -> decide -> wire -> capture -> deactivate
```

`detect()` runs for real against this machine. `capture()` has no live
agent session to listen to in this offline demo, so each collector is fed
from its own real captured JSONL/config data (see the tables above for
exact file paths) — the demo output explicitly labels this `SIMULATED FROM
CAPTURED DATA` per collector, and would say `LIVE` for a collector actually
streaming from a running listener. `run_demo_full.py` reuses
`run_demo_all_five.py`'s loader functions for the 5 Hooks collectors and
each new module's own loader/replay-path convention for the other 15, so
no replay data is synthesized specially for the consolidated demo.
