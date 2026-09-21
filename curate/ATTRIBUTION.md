# Curation attribution

Vendored from Purvi’s offline curation toolchain (Project Signal colleague).

| File | Role |
|---|---|
| `signal_curate.py` | Extract, group, precedence resolve, derive |
| `precedence_map.py` | Per-field collector preference order |

**Do not** rewrite merge keys, `curate()`, or `PRECEDENCE` without her review.

Project Signal additions in this package:

- `adapter_source_events.py` — RawEvent / `source_events` → curator loaded tuples
- `extract_codex_copilot.py` — thin Codex/Copilot hooks extractors (Phase 1)
- `extract_mcp_stdio.py` — MCP Proxy stdio wire + Cursor network (Phase 2)
- `extract_fs_watcher.py` — FS Watcher watchdog + Claude transcript lines (Phase 3)
- `extract_otel_raw.py` — OTel flat/OTLP reshape → Purvi `extract_otel` (Phase 4)
- `extract_cli_wrapper.py` — CLI Wrapper pty stdin/stall/session (Phase 5)
- `observables_sink.py` — upsert `observables` only (no re-write of `source_events`)
- CLI: `signal-agent curate ...`
- Live path: `export/live_curate.py` used by `upload-jsonl` / `run --live --upload`

Received into this repo: 2026-09-18.

## Ping Purvi (follow-up)

Please confirm:

1. OK to keep these files vendored under `collector_framework/curate/`.
2. Whether antigravity (and codex/copilot) PRECEDENCE entries exist elsewhere
   to merge later — v1 uses `resolve()` fallback when the tool key is missing.
