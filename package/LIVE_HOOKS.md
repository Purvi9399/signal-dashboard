# Live hooks — Requirements Specification (ASD-STE)

Status: Active. All five agents’ live hooks implemented. Sample policy still denies Antigravity.  
Related: [`LIVE_EXPORT.md`](LIVE_EXPORT.md), [`UPLOAD.md`](UPLOAD.md), [`CURATE_SPEC.md`](CURATE_SPEC.md).

## 1. Purpose

The agent shall capture hook telemetry while the operator uses an AI coding
tool. The agent shall upload curated `observables` with `source_events`.

Policy allow shall not be enough. The agent shall (1) install hook config
when policy approved the pair, (2) listen for hook deliveries, (3) stay up
until stop, (4) upload.

## 2. Scope

### 2.1 In scope

- Live `activate()` / `capture()` / `deactivate()` for **hooks** on:
  `claude_code`, `cursor`, `copilot`, `codex`, `antigravity`
- Per-agent hook-config install and restore
- One local HTTP listener in the `signal-agent` process (path-routed)
- `run --live` stays up until SIGINT/SIGTERM or `--listen-seconds`
- Existing Edge/direct upload after (or on) stop
- Unit tests without a live AI-tool session

### 2.2 Out of scope (this spec)

- Find → Decide → ActivationEngine construction rules
- `local_hooks`, `fs_watcher`, `otel`, `mcp_proxy`, `cli_wrapper` live paths
  (later specs; hooks first)
- Win32 / PKG rebuild
- SSO
- Rewrite of Purvi merge / `PRECEDENCE`
- Antigravity whole-agent allow (sample policy still denies; install only
  when policy approved)

## 3. Definitions

| Term | Meaning |
|---|---|
| Live hooks | `replay_events is None` (ActivationEngine `mode="live"` default ctor) |
| Replay | Explicit list (including `[]`); no listener; no settings write |
| Hook install | Merge org HTTP/command hooks into the agent’s real config file |
| Listener | `127.0.0.1` HTTP server that queues POST JSON for `capture()` |

## 4. Agent hook surfaces (later slices copy Claude)

| Agent | Config the collector shall merge | Delivery to Signal | Known trap |
|---|---|---|---|
| claude_code | `~/.claude/settings.json` `type: http` | `POST /webhook/claude/event` | `--bare` skips hooks |
| cursor | User `~/.cursor/hooks.json` (`version: 1` command hooks) + stdin forwarder | `POST /webhook/cursor/event` | Sandbox may omit permission events |
| copilot | `.github/hooks/hooks.json` both casings, `type: http` | POST to configured URL | `COPILOT_HOOK_ALLOW_LOCALHOST=1` and `GITHUB_COPILOT_PROMPT_MODE_REPO_HOOKS=true` or **zero events, silent** |
| codex | `~/.codex/hooks.json` command forwarder | POST from command stdin JSON | Hooks silent unless `hooks.state` trust hash exists |
| antigravity | `.agents/hooks.json` / `~/.gemini/config/hooks.json` command + `forward.py` | `POST /webhook/antigravity/event` (`:9091` in research; v1 may share `:8000` with a path) | Global + workspace duplicate deliveries |

Default listen port for v1: **8000**. Path distinguishes agents.

## 5. Functional requirements

| ID | Requirement |
|---|---|
| FR-H1 | When live, `activate()` shall start (or attach to) the local HTTP listener for that collector’s path. |
| FR-H2 | When live, `capture()` shall block and yield `RawEvent`s as POSTs arrive. `raw_payload` shall be the hook JSON, unmodified. |
| FR-H3 | `deactivate()` shall stop the feed so `capture()` ends, then stop the listener. |
| FR-H4 | Replay construction (`replay_events` is a list) shall not bind a port and shall not write agent settings. |
| FR-H5 | When live and hook install is enabled, `activate()` shall merge hook config for **approved** agents only. `deactivate()` shall restore the previous file bytes (or delete a file this process created). |
| FR-H6 | Hook install shall not remove unrelated existing hooks. |
| FR-H7 | `run --live` shall drain `merged_capture()` until SIGINT/SIGTERM. Then it shall upload if `--upload` is set. |
| FR-H8 | `run --live --listen-seconds N` shall stop capture after N seconds (smoke / tests). |
| FR-H9 | Listener bind failure shall fail that collector’s `activate()` and shall not kill other collectors (registry isolation). |
| FR-H10 | Claude Code: FR-H1–H6 for `claude_code.hooks` with URL `http://127.0.0.1:<port>/webhook/claude/event`. |
| FR-H11 | Copilot, Codex, and Antigravity live hooks follow FR-H14–H16. Sample policy still denies Antigravity at Activate. |
| FR-H12 | Sample policy deny of `antigravity` or `mcp_proxy` shall still prevent activation of those pairs. |
| FR-H13 | Cursor: FR-H1–H6 for `cursor.hooks`. Merge `version: 1` command hooks that POST stdin JSON to `/webhook/cursor/event`. Share the port-8000 listener by path with Claude. |
| FR-H14 | Copilot: FR-H1–H6 for `copilot.hooks`. Merge HTTP hooks for **both casings** of every event. Set `COPILOT_HOOK_ALLOW_LOCALHOST=1` and `GITHUB_COPILOT_PROMPT_MODE_REPO_HOOKS=true` in-process on live activate; restore on deactivate. Path: `/webhook/copilot/event`. |
| FR-H15 | Codex: FR-H1–H6 for `codex.hooks`. Merge command forwarder into `~/.codex/hooks.json`. Keep fail-closed trust check (`CodexTrustNotGrantedError`) before live listen unless `require_trust=False`. Path: `/webhook/codex/event`. |
| FR-H16 | Antigravity: FR-H1–H6 for `antigravity.hooks` when the collector is constructed (tests / policy-allow). Merge one named block `signal-live` into the **global** `~/.gemini/config/hooks.json` only (do not also write workspace config). Keep content-hash dedup. Path: `/webhook/antigravity/event` on the shared port. |

## 6. Non-functional

| ID | Requirement |
|---|---|
| NFR-H1 | New Project Signal callables shall have McCabe complexity ≤ 5. |
| NFR-H2 | Tests shall not write the operator’s real `~/.claude/settings.json`, `~/.cursor/hooks.json`, `~/.codex/hooks.json`, `~/.gemini/config/hooks.json`, or repo `.github/hooks/hooks.json`. |
| NFR-H3 | Secrets shall not appear in git, `policy.json`, or hook config. |
| NFR-H4 | Find → Decide → ActivationEngine APIs stay unchanged. Live behavior lives in collectors and in `cmd_run` drain. |

## 7. Implementation order

1. Spec (this file) + Claude Code live hooks (TDD).
2. Cursor forwarder + path on the shared listener.
3. Copilot HTTP hooks + required env (done in this slice).
4. Codex command hooks + trust check (done in this slice).
5. Antigravity live hooks implemented; sample policy still denies the agent.

Then separate specs for live FS Watcher, OTel, MCP Proxy, CLI Wrapper.

## 8. Acceptance (Claude slice)

1. Replay unit tests still yield the supplied payloads.
2. Live unit test: POST a hook JSON to the listener; `capture()` yields one `RawEvent` with that payload; `agent` is `claude_code`.
3. Live unit test: temp `settings.json` gains `type: http` hooks; `deactivate()` restores the backup.
4. Replay / empty-list construction does not create a listener.
5. `radon cc` ≤ 5 on new callables.

## 9. Acceptance (Cursor slice)

1. Replay unit tests still yield the supplied payloads; `agent` is `cursor`.
2. Live unit test: POST Cursor hook JSON to `/webhook/cursor/event`; `capture()` yields that `raw_payload`.
3. Live unit test: temp `hooks.json` gains `version: 1` command entries; `deactivate()` restores the backup.
4. Claude and Cursor live on the same host/port: POSTs to each path reach only that collector.
5. Replay construction does not write `hooks.json` or bind a dedicated extra port for Cursor.
6. `radon cc` ≤ 5 on new callables.

## 10. Acceptance (Copilot / Codex / Antigravity)

1. Replay still yields supplied payloads for each agent.
2. Live POST to each path yields a `RawEvent` with unmodified `raw_payload`.
3. Temp config files restore on deactivate.
4. Copilot live activate sets the two required env strings; deactivate restores prior values.
5. Codex `require_trust=True` with no `hooks.state` raises `CodexTrustNotGrantedError` and does not write hooks.
6. Antigravity live dedup still drops a second identical POST.
7. `radon cc` ≤ 5 on new callables.
