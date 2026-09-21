# Curate — Requirements Specification (ASD-STE)

Status: Active.  
Related: [`UPLOAD.md`](UPLOAD.md), colleague files in [`../curate/`](../curate/).

## 1. Purpose

The system shall turn Project Signal raw events into `public.observables`
using the existing colleague curation stack.

The system shall not invent a second normalizer.

## 2. Scope

### 2.1 In scope (Phases 1–4)

- Vendor `signal_curate.py` and `precedence_map.py`
- Adapter from RawEvent JSONL / `source_events` into curator loaded tuples
- Hooks / `local_hooks` adapters for: antigravity, claude-code, cursor, codex, copilot
- Reuse Purvi `extract_claude_hook` / `extract_cursor_hook` where payloads match
- Thin Codex / Copilot hooks extractors when Purvi has no dedicated function
- MCP Proxy adapters: stdio JSON-RPC wire (copilot/codex/antigravity/claude-code)
  via Purvi `extract_row`; Cursor network token capture → `model_request`
- FS Watcher adapters: watchdog `created`/`modified`/`deleted`/`moved*` → file_* /
  `transcript_appended`; Claude transcript JSONL lines → `transcript_appended`
- OTel adapters: flat Loki/attrs + OTLP `resourceLogs` → Purvi `extract_otel`
  (batches expand to multiple tuples)
- CLI `curate` and on-device curate during `upload-jsonl` / `run --live --upload`
- Upsert into `observables` (direct sink or Edge)

### 2.2 Out of scope (later phases)

- CLI wrapper adapters (Phase 5)
- Rewrite of merge / precedence logic
- Extend full `PRECEDENCE` for antigravity/codex/copilot (use resolve fallback)
- Re-upsert `source_events` from curation
- Find → Decide → Activate changes
- SSO identity gate

## 3. Functional requirements

| ID | Requirement |
|---|---|
| FR-C1 | The adapter shall map RawEvent envelopes to `(collector, fields, raw)` tuples. |
| FR-C2 | Antigravity hooks shall map to a non-`other` `observable_type` when possible. |
| FR-C3 | The pipeline shall call vendored `curate()` on adapted tuples. |
| FR-C4 | `--supabase` / direct upload shall upsert curated rows into `public.observables` only. |
| FR-C5 | `--from-batch ID` shall load `source_events` for that `ingest_batch_id`. |
| FR-C6 | Find → Decide → Activate shall stay unchanged. |
| FR-C7 | `upload-jsonl` and `run --live --upload` shall curate on-device and send observables with source_events. |
| FR-C8 | Claude-code hooks RawEvents shall adapt via Purvi `extract_claude_hook` when typed. |
| FR-C9 | Cursor hooks RawEvents shall adapt via Purvi `extract_cursor_hook` (with event-name normalize if needed). |
| FR-C10 | Codex and Copilot hooks / local_hooks RawEvents shall adapt to non-`other` types when typed. |
| FR-C11 | Stdio MCP Proxy RawEvents (`tools/call`, call results, `tools/list` results) shall adapt to `mcp_request` / `mcp_result` / `mcp_discovery`. |
| FR-C12 | Cursor `network_context_capture` MCP-proxy RawEvents with usage tokens shall adapt to `model_request`. |
| FR-C13 | Untyped MCP wire noise (`initialize`, null `parsed`) shall be skipped. |
| FR-C14 | FS Watcher RawEvents with `created`/`modified`/`deleted`/`moved*` shall adapt to `file_create` / `file_modify` / `file_delete` (or `transcript_appended` for transcript paths). |
| FR-C15 | Claude-code FS Watcher transcript JSONL lines shall adapt to `transcript_appended` with usage tokens when present. |
| FR-C16 | Untyped FS Watcher noise (unknown `event_type`, missing `path`) shall be skipped. |
| FR-C17 | OTel RawEvents with known `event.name` short-names shall adapt via Purvi `extract_otel` to non-`other` types. |
| FR-C18 | OTLP `resourceLogs` batches shall expand to one curator tuple per typed log record. |
| FR-C19 | Untyped OTel noise (unknown event name, empty attrs) shall be skipped. |

## 4. Non-functional

| ID | Requirement |
|---|---|
| NFR-C1 | New Project Signal callables shall have McCabe complexity ≤ 5. |
| NFR-C2 | Vendored scripts are not required to meet CC ≤ 5. |
| NFR-C3 | Operator secrets stay in env; never in git or the frozen exe. |

## 5. Commands

```bash
cd collector_framework
python -m package.cli curate --dry-run --from-jsonl PATH
python -m package.cli curate --from-jsonl PATH --out curated.jsonl
python -m package.cli curate --from-batch INGEST_BATCH_ID --supabase
```

## 6. Env (operator)

| Variable | Purpose |
|---|---|
| `SUPABASE_URL` | Project URL |
| `SUPABASE_SERVICE_ROLE_KEY` or `SUPABASE_KEY` | Service role for fetch/upsert |
| `SUPABASE_TABLE` | Default `observables` |

## 7. ISO/IEC 25010 lenses

| Lens | Assessment |
|---|---|
| Functional suitability | Hooks, MCP Proxy, FS Watcher, and OTel adapters produce typed observables for five agents from RawEvent JSONL. |
| Compatibility | Reuses colleague `curate()` / extractors and existing `observables` enums. |
| Security | Observables write uses operator service role or Edge secret; no fleet bake-in of service role. |
| Maintainability | Vendored code isolated; new modules stay small (CC ≤ 5); phased collector coverage. |
| Portability | Stdlib REST for sink; no new freeze deps required for dry-run. |

## 8. Acceptance

1. Unit tests for all five tools’ hooks adapters pass (synthetic fixtures).
2. Unit tests for MCP Proxy adapters pass (stdio four agents + Cursor network).
3. Unit tests for FS Watcher adapters pass (watchdog five agents + Claude transcript lines).
4. Unit tests for OTel adapters pass (flat attrs + resourceLogs expand).
5. Antigravity live sample dry-run still yields `tool=antigravity` and non-`other` types.
6. Mixed synthetic dry-run yields curated tools across the five agents.
7. Offline export tests remain green.
