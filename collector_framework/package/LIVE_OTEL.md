# Live OTel — Requirements Specification (ASD-STE)

Status: Active. Implementation slice for all five agents.  
Related: [`LIVE_HOOKS.md`](LIVE_HOOKS.md), [`LIVE_FS.md`](LIVE_FS.md),
[`CURATE_SPEC.md`](CURATE_SPEC.md).

## 1. Purpose

The agent shall capture OpenTelemetry payloads while the operator uses an
AI coding tool that actually exports OTLP. The agent shall upload curated
`observables` with `source_events`.

Policy allow shall not be enough. For agents with a real exporter, the
agent shall (1) point export at Signal, (2) listen, (3) stay up until
stop, (4) upload.

## 2. Scope

### 2.1 In scope

- Live `activate()` / `capture()` / `deactivate()` for **otel** on:
  `claude_code`, `cursor`, `copilot`, `codex`, `antigravity`
- Local OTLP/HTTP JSON receiver in the `signal-agent` process
- Per-agent path prefix on one host/port (default **4318**)
- Blocking `capture()` until `deactivate()` for collectors that listen
- Unit tests without otel-lgtm and without a live AI-tool session

### 2.2 Out of scope (this spec)

- Find → Decide → ActivationEngine construction rules
- `hooks`, `fs_watcher`, `local_hooks`, `mcp_proxy`, `cli_wrapper` live
- Win32 / PKG rebuild
- SSO
- Rewrite of Purvi merge / `PRECEDENCE`
- Redaction of Codex `user_email` / `user_account_id` (later stage)
- Claiming Cursor or Antigravity emit OTLP (research: no / dead end)
- Sample policy change (Antigravity remains denied; Codex otel remains
  denied in sample policy)

## 3. Definitions

| Term | Meaning |
|---|---|
| Live OTel | `replay_events is None` (Copilot: `replay_otlp_export is None`) |
| Replay | Explicit list or Copilot OTLP export dict; no listener; no config write |
| OTLP prefix | `/otel/<agent>` ; SDK/config appends `/v1/traces`, `/v1/logs`, `/v1/metrics` |
| Feed | In-process queue; `capture()` blocks until `stop()` |

## 4. Agent OTel surfaces

| Agent | What live shall do | `raw_payload` | Known trap |
|---|---|---|---|
| claude_code | Set in-process `CLAUDE_CODE_ENABLE_TELEMETRY=1` and `OTEL_EXPORTER_OTLP_ENDPOINT` to the prefix | HTTP JSON body, unmodified | Env gating unverified in this repo |
| copilot | Set in-process CLI OTel env (`COPILOT_OTEL_ENABLED`, endpoint). Plain Chat settings do not export | Live: HTTP JSON body. Replay: OTLP `batches[]` | Chat `github.copilot.chat.otel.*` is a confirmed no-op |
| codex | If no `[otel]` table, append three otlp-http URLs under the prefix. Do not clobber an existing `[otel]` | HTTP JSON body, unmodified | Single base URL 404s; need `/v1/logs|traces|metrics`. PII on every log |
| cursor | Bind `/otel/cursor` only. No env mutation. No settings write | HTTP JSON if anything POSTs | CSV never cites OTel for Cursor |
| antigravity | No listener. No config write. Capture empty unless replay | Replay only | Confirmed no-op TracerProvider |

Default listen: `127.0.0.1:4318`. Path prefix distinguishes agents.

## 5. Functional requirements

| ID | Requirement |
|---|---|
| FR-O1 | When live for Claude, Copilot, or Codex, `activate()` shall attach `/otel/<agent>/v1/{traces,logs,metrics}` on the shared mux. |
| FR-O2 | When live and listening, `capture()` shall block and yield `RawEvent`s. `raw_payload` shall be the POST JSON object, unmodified. |
| FR-O3 | `deactivate()` shall detach those paths, stop the feed, restore env and Codex config backup. |
| FR-O4 | Replay shall not bind a port and shall not write `config.toml` or mutate env. |
| FR-O5 | Tests shall not write the operator’s real `~/.codex/config.toml` and shall restore process env. |
| FR-O6 | Bind failure shall fail that collector’s `activate()` only (registry isolation). |
| FR-O7 | Claude Code: FR-O1–O5 with prefix `/otel/claude`. Restore env on deactivate. |
| FR-O8 | Copilot: FR-O1–O5 with prefix `/otel/copilot`. Keep `replay_otlp_export` batch walk. |
| FR-O9 | Codex: FR-O1–O5 with prefix `/otel/codex`. Append `[otel]` only when absent. `require_configured` stays informational. Sample policy still denies `codex`+`otel`. |
| FR-O10 | Cursor: live binds `/otel/cursor`. Keep the “no positive evidence” note. Do not set OTEL env. |
| FR-O11 | Antigravity: live shall not bind and shall not write files. Keep the dead-end note. Sample policy still denies the agent. |
| FR-O12 | Sample policy deny of `antigravity` or `mcp_proxy` or `codex`+`otel` shall still prevent activation of those pairs. |

## 6. Non-functional

| ID | Requirement |
|---|---|
| NFR-O1 | New Project Signal callables shall have McCabe complexity ≤ 5. |
| NFR-O2 | Secrets shall not appear in git or in OTEL_RESOURCE_ATTRIBUTES set by Signal. |
| NFR-O3 | Find → Decide → ActivationEngine APIs stay unchanged. |

## 7. Implementation order

1. Spec (this file) + shared OTLP helper on the existing HTTP mux.
2. Live collectors (TDD).
3. Keep curate adapters unchanged.

Then a separate spec for live MCP Proxy, then CLI Wrapper.

## 8. Acceptance

1. Replay still yields supplied payloads / Copilot batches.
2. Live POST to `/otel/claude/v1/traces` yields that JSON as `raw_payload`.
3. Copilot live POST to `/otel/copilot/v1/logs` yields that JSON.
4. Codex temp `config.toml` gains `[otel]` when empty; deactivate restores.
5. Antigravity live does not bind; replay still works.
6. `radon cc` ≤ 5 on new callables.
