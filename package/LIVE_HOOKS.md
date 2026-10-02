# Live hooks — Requirements Specification (ASD-STE)

Status: Active. Claude Code is the first implementation slice.  
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
| cursor | `.cursor/hooks.json` + forwarder | `POST /webhook/cursor/event` | Sandbox may omit permission events |
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
| FR-H10 | Claude Code first slice: FR-H1–H6 for `claude_code.hooks` with URL `http://127.0.0.1:<port>/webhook/claude/event`. |
| FR-H11 | Cursor, Copilot, Codex, Antigravity hook install remain unimplemented until their slices. Their live `capture()` may stay empty until then. |
| FR-H12 | Sample policy deny of `antigravity` or `mcp_proxy` shall still prevent activation of those pairs. |

## 6. Non-functional

| ID | Requirement |
|---|---|
| NFR-H1 | New Project Signal callables shall have McCabe complexity ≤ 5. |
| NFR-H2 | Tests shall not write the operator’s real `~/.claude/settings.json`. |
| NFR-H3 | Secrets shall not appear in git, `policy.json`, or hook config. |
| NFR-H4 | Find → Decide → ActivationEngine APIs stay unchanged. Live behavior lives in collectors and in `cmd_run` drain. |

## 7. Implementation order

1. Spec (this file) + Claude Code live hooks (TDD).
2. Cursor forwarder + path on the shared listener.
3. Copilot HTTP hooks + required env.
4. Codex command hooks + trust check (fail closed if untrusted).
5. Antigravity only if policy allows the agent.

Then separate specs for live FS Watcher, OTel, MCP Proxy, CLI Wrapper.

## 8. Acceptance (Claude slice)

1. Replay unit tests still yield the supplied payloads.
2. Live unit test: POST a hook JSON to the listener; `capture()` yields one `RawEvent` with that payload; `agent` is `claude_code`.
3. Live unit test: temp `settings.json` gains `type: http` hooks; `deactivate()` restores the backup.
4. Replay / empty-list construction does not create a listener.
5. `radon cc` ≤ 5 on new callables.
