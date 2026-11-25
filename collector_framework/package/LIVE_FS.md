# Live FS Watcher — Requirements Specification (ASD-STE)

Status: Active. Implementation slice for all five agents.  
Related: [`LIVE_HOOKS.md`](LIVE_HOOKS.md), [`LIVE_EXPORT.md`](LIVE_EXPORT.md), [`CURATE_SPEC.md`](CURATE_SPEC.md).

## 1. Purpose

The agent shall capture filesystem telemetry while the operator uses an AI
coding tool. The agent shall upload curated `observables` with
`source_events`.

Policy allow shall not be enough. The agent shall (1) watch the agent’s
confirmed on-disk roots, (2) yield `RawEvent`s as the OS reports changes,
(3) stay up until stop, (4) upload.

## 2. Scope

### 2.1 In scope

- Live `activate()` / `capture()` / `deactivate()` for **fs_watcher** on:
  `claude_code`, `cursor`, `copilot`, `codex`, `antigravity`
- Per-agent watch roots already documented in each collector
- Blocking `capture()` that ends when `deactivate()` stops the feed
- `run --live` stay-up already specified in LIVE_HOOKS.md (FR-H7 / FR-H8)
- Unit tests without a live AI-tool session
- Shared `watchdog` helper with McCabe complexity ≤ 5

### 2.2 Out of scope (this spec)

- Find → Decide → ActivationEngine construction rules
- `hooks`, `local_hooks`, `otel`, `mcp_proxy`, `cli_wrapper` live paths
- Win32 / PKG rebuild
- SSO
- Rewrite of Purvi merge / `PRECEDENCE`
- Parsing file contents except Claude Code JSONL transcript tailing
  (already in `claude_code_fs_watcher.py`)
- Widening Antigravity watch roots to global scratch (research blind spot)
- Inventing Copilot brain/transcript paths (still UNVERIFIED / BEST-EFFORT)
- Sample policy change (Antigravity remains denied at Activate)

## 3. Definitions

| Term | Meaning |
|---|---|
| Live FS | `replay_events is None` and Antigravity `replay_path is None` |
| Replay | Explicit list or Antigravity `replay_path`; no Observer |
| Watch root | Directory the collector schedules on the Observer |
| Feed | In-process queue; `capture()` blocks until `stop()` |

## 4. Agent watch surfaces

| Agent | Roots the collector shall watch | `raw_payload` | Known trap |
|---|---|---|---|
| claude_code | `~/.claude/projects` (override in tests) | Transcript JSONL line on `.jsonl` append; else `{event_type, path, is_directory, size_bytes}` | Mid-write JSON lines skipped |
| cursor | Detected project root and `.git` | FS record; `git_ref` only on `.git` paths | No `.cursor/hooks.json` → live activate fails |
| copilot | `~/.copilot/logs`, `~/.copilot/hooks` | Generic FS record | Roots inferred; not a dedicated Copilot FS study |
| codex | `~/.codex/sessions` plus optional workspace | `{event_type, path, is_directory, size, watch_root}` | OS coalesces appends; counts are not volume |
| antigravity | `~/.gemini/antigravity/brain` and `.../antigravity-cli/brain` | Generic FS record (`size_bytes`) | Does not see global scratch deliverables |

Default Observer: third-party `watchdog` **polling** observer (portable;
does not require FSEvents). Missing package shall fail live
`activate()` with a clear error. Replay shall not require `watchdog`.

## 5. Functional requirements

| ID | Requirement |
|---|---|
| FR-F1 | When live, `activate()` shall start a recursive Observer on that collector’s watch roots that exist. |
| FR-F2 | When live, `capture()` shall block and yield `RawEvent`s as the handler queues records. `raw_payload` shall be the record dict, unmodified. |
| FR-F3 | `deactivate()` shall stop the Observer and stop the feed so `capture()` ends. |
| FR-F4 | Replay construction shall not start an Observer and shall not create directories under the operator home. |
| FR-F5 | Tests shall pass temp watch roots. Tests shall not write the operator’s real `~/.claude`, `~/.copilot`, `~/.codex`, or `~/.gemini` trees. |
| FR-F6 | Live `activate()` without `watchdog` shall raise `RuntimeError` and shall not kill other collectors (registry isolation). |
| FR-F7 | Claude Code: on `.jsonl` create/modify under a watch root, `capture()` shall yield parsed JSON objects for new lines. Other events shall yield the generic FS record. |
| FR-F8 | Cursor: live `activate()` shall fail if no project root is set or detected. `.git` events may include `git_ref` from a real `git` command. |
| FR-F9 | Copilot: schedule only roots that exist. Empty root list still starts a feed that ends on deactivate. |
| FR-F10 | Codex: keep fail-open (no trust gate). Live records shall include `watch_root`. Do not invent per-append line counts. |
| FR-F11 | Antigravity: live `activate()` may `mkdir` configured brain roots (matching research `fs_watcher.py`). Sample policy still denies the agent at Activate. Keep `replay_path` replay. |
| FR-F12 | Sample policy deny of `antigravity` or `mcp_proxy` shall still prevent activation of those pairs. |

## 6. Non-functional

| ID | Requirement |
|---|---|
| NFR-F1 | New Project Signal callables shall have McCabe complexity ≤ 5. |
| NFR-F2 | Secrets shall not appear in git or watch records invented by tests. |
| NFR-F3 | Find → Decide → ActivationEngine APIs stay unchanged. |
| NFR-F4 | Do not read file bytes except Claude Code transcript JSONL tailing. |

## 7. Implementation order

1. Spec (this file) + shared live FS helper.
2. Live `activate` / `capture` / `deactivate` for all five collectors (TDD).
3. Keep curate adapters unchanged.

Then separate specs for live OTel, MCP Proxy, CLI Wrapper.

## 8. Acceptance

1. Replay unit tests still yield the supplied payloads; `agent` matches.
2. Live unit test: write a file under a temp root; `capture()` yields a
   `RawEvent` whose `raw_payload` includes that path.
3. Claude live: append a JSONL object; `raw_payload` is that object.
4. Replay / `replay_path` does not start an Observer.
5. Codex untrusted live FS still activates (no `CodexTrustNotGrantedError`).
6. `radon cc` ≤ 5 on new callables.
