# Live CLI Wrapper — Requirements Specification (ASD-STE)

Status: Active. Implementation slice for all five agents.  
Related: [`LIVE_HOOKS.md`](LIVE_HOOKS.md), [`LIVE_FS.md`](LIVE_FS.md),
[`LIVE_OTEL.md`](LIVE_OTEL.md), [`LIVE_MCP.md`](LIVE_MCP.md),
[`CURATE_SPEC.md`](CURATE_SPEC.md).

## 1. Purpose

The agent shall capture raw PTY chunks while an AI coding CLI runs under
a wrapper. The agent shall upload curated `observables` with
`source_events`.

Policy allow shall not be enough. Sample policy still denies Cursor
`cli_wrapper` and whole Antigravity. This spec covers the collector live
path so Activate can run it when policy later allows.

## 2. Scope

### 2.1 In scope

- Live `activate()` / `capture()` / `deactivate()` for **cli_wrapper** on:
  `claude_code`, `cursor`, `copilot`, `codex`, `antigravity`
- POSIX PTY (and existing Windows ConPTY) spawn when a `command` is set
- Chunk `raw_payload` `{timestamp, stream, content, wrapped_command}`
- Blocking `capture()` until the child ends or `deactivate()`
- Unit tests without wrapping a real `copilot` / `claude` / `codex` /
  `agy` / `cursor` session

### 2.2 Out of scope (this spec)

- Find → Decide → ActivationEngine construction rules
- Rewriting the operator’s PATH, aliases, or shell rc
- Parsing JSON or ANSI inside `content`
- Computing `hung_seconds` / `is_developer_command` here
- Sample policy change (Cursor `cli_wrapper` remains denied; Antigravity
  remains denied)
- Claiming Cursor has a confirmed headless agentic CLI
- Win32 / PKG, SSO, Purvi rewrite
- `local_hooks` live path

## 3. Definitions

| Term | Meaning |
|---|---|
| Live CLI | Replay list/path is `None` |
| Replay | Explicit chunk list, or Antigravity `replay_path` |
| PTY session | Child under a pty; chunks on a feed |
| Chunk | `{timestamp, stream, content, wrapped_command}` plus optional extras |

## 4. Agent surfaces

| Agent | Live | `raw_payload` | Known trap |
|---|---|---|---|
| copilot | PTY when `command` set (default `copilot`) | Chunk; no inner parse | Footer tokens exist only as printed text |
| claude_code | Same; default `claude` | Chunk + `operator_username` / `hostname` | No Claude-specific capture file in this repo |
| codex | PTY only when `command` is set | Chunk; keep ANSI and inner JSON in `content` | Interactive TUI has no reliable failure marker |
| antigravity | PTY when live; default `agy --output-format stream-json` | Chunk; `agy` JSON may split across chunks | Sample policy still denies the whole agent |
| cursor | PTY only when `command` is set. Do not default-spawn `cursor` | Chunk | Unverified headless CLI; sample denies this pair |

If live and no `command` (Codex/Cursor), do not spawn. Still hold a feed
so `capture()` blocks until deactivate.

## 5. Functional requirements

| ID | Requirement |
|---|---|
| FR-C1 | Replay shall not spawn a child. |
| FR-C2 | Live with `command` shall spawn under a PTY and put chunks on a feed. |
| FR-C3 | Live `capture()` shall yield `RawEvent`s. `raw_payload` unmodified. |
| FR-C4 | `deactivate()` shall terminate the child and stop the feed. |
| FR-C5 | Tests shall pass a temp/`python -c` `command`. Tests shall not wrap the operator’s real AI CLI. |
| FR-C6 | Cursor live shall not spawn `cursor` unless `command` is explicit. Keep the unverified note. |
| FR-C7 | Sample policy deny of `antigravity` or Cursor `cli_wrapper` shall still prevent activation. |
| FR-C8 | Do not parse `content`. Do not invent a Cursor capture JSONL. |

## 6. Non-functional

| ID | Requirement |
|---|---|
| NFR-C1 | New Project Signal callables shall have McCabe complexity ≤ 5. |
| NFR-C2 | Secrets shall not appear in git. |
| NFR-C3 | Find → Decide → ActivationEngine APIs stay unchanged. |

## 7. Implementation order

1. Spec (this file) + shared PTY helper.
2. Live collectors (TDD).
3. Keep curate adapters unchanged.

Then a separate spec for live `local_hooks`.

## 8. Acceptance

1. Replay still yields supplied chunks; `agent` matches.
2. Live `python -c` test: stdout text appears in `raw_payload.content`.
3. Cursor live with no `command` yields nothing and does not spawn.
4. Antigravity `replay_path` still works.
5. `radon cc` ≤ 5 on new callables.
