# Live MCP Proxy — Requirements Specification (ASD-STE)

Status: Active. Implementation slice for all five agents.  
Related: [`LIVE_HOOKS.md`](LIVE_HOOKS.md), [`LIVE_FS.md`](LIVE_FS.md),
[`LIVE_OTEL.md`](LIVE_OTEL.md), [`CURATE_SPEC.md`](CURATE_SPEC.md).

## 1. Purpose

The agent shall capture MCP JSON-RPC (or Cursor network-capture) telemetry
while the operator uses an AI coding tool. The agent shall upload curated
`observables` with `source_events`.

Policy allow shall not be enough. Sample policy still denies `mcp_proxy`
for every agent. This spec covers the collector live path so Activate
can run it when policy later allows.

## 2. Scope

### 2.1 In scope

- Live `activate()` / `capture()` / `deactivate()` for **mcp_proxy** on:
  `claude_code`, `cursor`, `copilot`, `codex`, `antigravity`
- Stdio shuttle + log for Copilot/Claude/Codex/Antigravity
- Cursor: ingest decoded flow JSON; apply existing IGNORE_ROUTES and usage
  extract (no mitmproxy child in v1)
- Blocking `capture()` until `deactivate()`
- Unit tests without a live AI-tool MCP session

### 2.2 Out of scope (this spec)

- Find → Decide → ActivationEngine construction rules
- Rewriting the operator’s `mcpServers` / `config.toml` / `.mcp.json`
- Spawning `mitmdump` for Cursor
- Redaction of tool arguments or Codex `_meta` PII
- Sample policy change (all `mcp_proxy` remains denied)
- `cli_wrapper` live path
- Win32 / PKG, SSO, Purvi rewrite

## 3. Definitions

| Term | Meaning |
|---|---|
| Live MCP | Replay list/path is `None` |
| Replay | Explicit records / Copilot `replay_records` / Cursor `replay_flows` / Antigravity `replay_path` |
| Stdio proxy | Child MCP server; shuttle lines; log `{timestamp, direction, raw, parsed}` |
| Flow ingest | HTTP JSON `{route, session_id, request_body, response_body}` for Cursor |

## 4. Agent surfaces

| Agent | Live | `raw_payload` | Known trap |
|---|---|---|---|
| claude_code | Stdio proxy when `server_command` is set | `{timestamp, direction, raw, parsed}` | No Claude-specific wire capture in this repo |
| copilot | Same stdio proxy | Same | Connection fail is silence after `initialize`; `isError` inside `result` |
| codex | Same stdio proxy | Same | `_meta.x-codex-turn-metadata` on every tools/call |
| antigravity | Same stdio proxy | Same | `invoke_subagent` never on this wire |
| cursor | POST `/webhook/cursor/mcp` flow JSON | `network_context_capture` shape from existing builder | Not MCP-stdio; mitmproxy stays out of process |

If live and no `server_command` (stdio agents), do not spawn. Still hold a
feed so `capture()` blocks until deactivate.

## 5. Functional requirements

| ID | Requirement |
|---|---|
| FR-M1 | Replay shall not spawn a child and shall not bind a port. |
| FR-M2 | Live stdio with `server_command` shall spawn the child, shuttle lines, put log records on a feed. |
| FR-M3 | Live `capture()` shall block and yield `RawEvent`s. `raw_payload` unmodified. |
| FR-M4 | `deactivate()` shall terminate the child and stop the feed. |
| FR-M5 | Tests shall pass a temp/echo `server_command` and pipes. Tests shall not rewrite operator MCP config. |
| FR-M6 | Cursor live shall attach `/webhook/cursor/mcp`. Apply IGNORE_ROUTES and usage extract. Do not set OTEL/MCP env. |
| FR-M7 | Sample policy deny of `mcp_proxy` or `antigravity` shall still prevent activation. |
| FR-M8 | Do not invent JSON-RPC error events for connection failure. |

## 6. Non-functional

| ID | Requirement |
|---|---|
| NFR-M1 | New Project Signal callables shall have McCabe complexity ≤ 5. |
| NFR-M2 | Secrets shall not appear in git. Do not redact wire `_meta` here. |
| NFR-M3 | Find → Decide → ActivationEngine APIs stay unchanged. |

## 7. Implementation order

1. Spec (this file) + shared stdio helper.
2. Live collectors (TDD).
3. Keep curate adapters unchanged.

Then a separate spec for live CLI Wrapper.

## 8. Acceptance

1. Replay still yields supplied records; `agent` matches.
2. Live echo-server test: one JSON-RPC line in, `raw_payload.parsed` matches.
3. Cursor live POST of a large usage flow yields `network_context_capture`.
4. Cursor IGNORE_ROUTES flow yields nothing.
5. Antigravity `replay_path` still works.
6. `radon cc` ≤ 5 on new callables.
