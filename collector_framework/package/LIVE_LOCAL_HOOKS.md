# Live Local Hooks — Requirements Specification (ASD-STE)

Status: Active. Implementation slice for Copilot, Codex, and Antigravity.  
Related: [`LIVE_HOOKS.md`](LIVE_HOOKS.md), [`LIVE_FS.md`](LIVE_FS.md),
[`LIVE_OTEL.md`](LIVE_OTEL.md), [`LIVE_MCP.md`](LIVE_MCP.md),
[`LIVE_CLI.md`](LIVE_CLI.md), [`CURATE_SPEC.md`](CURATE_SPEC.md).

## 1. Purpose

The agent shall capture IDE observer events from the
`signal-local-hooks` VS Code / Antigravity extension while the operator
edits. The agent shall upload curated `observables` with `source_events`.

This technique is not native `hooks.json`. Claude Code and Cursor have
no `local_hooks` collector in this repo. Sample policy still denies
whole Antigravity.

## 2. Scope

### 2.1 In scope

- Live `activate()` / `capture()` / `deactivate()` for **local_hooks** on:
  `copilot`, `codex`, `antigravity`
- HTTP ingest of unmodified IDE JSON on the shared mux (default port
  **8000**)
- Blocking `capture()` until `deactivate()`
- Unit tests without VS Code / Antigravity IDE

### 2.2 Out of scope (this spec)

- Find → Decide → ActivationEngine construction rules
- Building, packaging, or sideloading the `.vsix`
- Rewriting the extension TypeScript
- Inventing `claude_code.local_hooks` or `cursor.local_hooks`
- Sample policy change
- Win32 / PKG, SSO, Purvi rewrite
- Patching `detected_ai_extensions` or synthesizing `onDidCloseTerminal`

## 3. Definitions

| Term | Meaning |
|---|---|
| Live local hooks | Replay list/path is `None` |
| Replay | Copilot/Codex `replay_events` list; Antigravity `replay_path` |
| IDE JSON | Per-`type` payload from `captured_ide_events.jsonl` |

## 4. Agent surfaces

| Agent | Live | `raw_payload` | Known trap |
|---|---|---|---|
| copilot | POST `/webhook/copilot/local` | IDE JSON, unmodified | `onDidCloseTerminal` never fired; Tab-accept only |
| codex | POST `/webhook/codex/local` | Same | IDE native hooks silent; this collector is the IDE surface |
| antigravity | POST `/webhook/antigravity/local` | Same | Unmatched undo only; `detected_ai_extensions` all false |

Do not write `~/.vscode` or Antigravity user settings. The extension (or
a test) POSTs JSON to the path.

## 5. Functional requirements

| ID | Requirement |
|---|---|
| FR-L1 | Replay shall not bind a port and shall not write IDE settings. |
| FR-L2 | Live `activate()` shall attach the agent path on the shared mux. |
| FR-L3 | Live `capture()` shall block and yield `RawEvent`s. `raw_payload` unmodified. |
| FR-L4 | `deactivate()` shall detach the path and stop the feed. |
| FR-L5 | Tests shall POST sample IDE JSON. Tests shall not install a `.vsix`. |
| FR-L6 | Sample policy deny of `antigravity` shall still prevent activation. |
| FR-L7 | Do not invent close-terminal or accept events. Do not add Claude/Cursor collectors. |

## 6. Non-functional

| ID | Requirement |
|---|---|
| NFR-L1 | New Project Signal callables shall have McCabe complexity ≤ 5. |
| NFR-L2 | Secrets shall not appear in git. |
| NFR-L3 | Find → Decide → ActivationEngine APIs stay unchanged. |

## 7. Implementation order

1. Spec (this file).
2. Live collectors (TDD) on the existing HTTP mux.
3. Keep curate adapters unchanged.

All six collector techniques then have a live path. Remaining work is
leftover packaging/export/SSO docs, not a new collector technique.

## 8. Acceptance

1. Replay still yields supplied IDE events; `agent` matches.
2. Live POST of `{type: "onDidChangeTextDocument", ...}` yields that JSON.
3. Antigravity `replay_path` still works.
4. Replay does not bind a dedicated extra port.
5. `radon cc` ≤ 5 on new callables.
