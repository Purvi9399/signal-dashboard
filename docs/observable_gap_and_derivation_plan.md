# Observable Gap & Derivation Plan

**What this is.** A data-driven map of which `observables` are **absent** (exist as a column but
carry no data) and, for each, whether its value can be **calculated from data we already have**
(no new collection) or **needs new collection** (collector enhancement). This operationalises the
17/18 Nov transcript rule: *an observable may only be derived from existing collections; new
collection is gated behind behavioural questions* (see `docs/PROJECT_SIGNAL_VISION.md` §2–§4).

**Measurement basis.** Population measured across **all 37,258 rows** of `public.observables`
(live project `cdftgybwybuzcyurlccf`). "Absent" = **0 rows populated**. Method: per-column
non-null/non-empty count via `jsonb_each_text(to_jsonb(observables))`. Figures are a point-in-time
snapshot; re-run the query (bottom of doc) to refresh.

> Note on earlier figures: `list_tables` previously reported ~1,283 observable rows — that was a
> stale Postgres planner estimate. The true count is **37,258**.

---

## Summary

- **27 observables are absent** (0% populated).
- **~18 of the 27 are calculable today** — their values are already in `source_events.payload` or
  derivable from other populated observables; they just aren't mapped/derived into `observables`
  yet. **No new collection required.**
- **~6 genuinely need new collection** (collector enhancement) — not present in any existing raw
  data. These go through the behavioural-question → collector-fix → human fix/ignore loop.
- A further tier of observables is **present but barely populated (<1%)** — not absent, just starved
  because the behaviours that produce them rarely occurred historically. The scenario pack
  (`docs/scenario_prompts.md`) is designed to populate these.

---

## 🟢 Bucket A — Calculable NOW from existing raw data (no new collection)

Already present in `source_events.payload` or trivially derivable from a populated observable;
needs only mapping/derivation logic in the curation layer.

| Observable | Derive from | Notes |
|---|---|---|
| `request_id` | `payload.request_id` | **Already in 8,409 raw events** — pure mapping gap |
| `time_to_first_token` | `payload.ttft_ms`, `codex.turn_ttft` | Present in raw (api_request / turn events) |
| `mcp_transport` | `payload.transport_type` / `protocol` | From MCP events |
| `mcp_server_version` | `payload` of `mcp_initialize` events | Partial — only where init captured |
| `error_message` | `payload.error`, `error.message`, `stderr` | Multiple raw sources |
| `denial_reason` | `payload.reason` on permission-denied events | |
| `permission_scope` | `payload.permission_suggestions` / `server_scope` | |
| `attachment_count` | length of `payload.attachments` | |
| `command_cwd` | `cwd` / payload on shell events | `cwd` observable is 24.9% populated |
| `is_multi_agent` | `delegation_depth > 0` or subagent presence | `delegation_depth` is 46.6% populated |
| `multi_agent_source` | `payload.subagent_type` / `is_background_agent` | |
| `parent_agent_id` | subagent-start linkage / `payload.parent_conversation_id` | |
| `mentions_files` | parse `prompt_text` for `@`-file mentions | Limited by `prompt_text` availability |
| `lines_added` / `lines_removed` | diff `old_content`/`new_content`, or `payload.edits` | Limited by content capture |
| `repo_name` | parse `cwd` / `workspace` path | Basic form from path; full form needs git remote |

## 🔵 Bucket A2 — Produced by the internal pipeline (not collection)

| Observable | How it should be populated |
|---|---|
| `derived_from` | Set when the curation layer creates derived/correlated observables |
| `redacted_fields` | Set by the redaction logic (`redaction_level` is 100% populated) |
| `extra` | Housekeeping catch-all — not a metric input |

## 🔴 Bucket B — Needs NEW collection (collector enhancement)

Not present in any existing raw data. Each must go through the **behavioural-question →
collector-fix → human fix/ignore** loop (vision doc §4.1). Do these **post-hoc**, not on the spot —
they also touch permissions/policy.

| Observable | Why it needs collection |
|---|---|
| `git_branch` | No git context in any raw event; collector must capture it |
| `git_commit` | Same as above |
| `hostname` | Device-level metadata; trivial collector config, but not currently emitted |
| `machine_id` | Device-level metadata; trivial collector config |
| `injection_markers` | Needs a prompt-injection **scanner** (analogous to `secret_detected`, which *is* 74% populated) — the 17 Nov PII/secrets example |
| `instructions_loaded` | Needs session-start capture of loaded instruction files (CLAUDE.md etc.) |
| `compaction_reason` | Needs the PreCompact hook captured |
| `team_id` | Needs an **org/team reference registry** — not telemetry at all |

> Bucket B is slightly longer than the "~6" headline because `hostname`/`machine_id` are
> device-metadata that are *technically* a trivial collector config rather than a hard
> collection problem — count them as whichever side your effort model prefers.

---

## ⚠️ Present-but-sparse tier (<1% populated — NOT absent)

These exist with data but are too sparse to drive metrics reliably. They are sparse because the
**behaviours** that emit them were rare in the historical corpus — the scenario pack targets them.

| Observable | % populated | Populated by scenario(s) |
|---|---|---|
| `policy_rule_matched` | 0.03% | PE-1…PE-4 |
| `stderr` | 0.03% | REC-1, RES-2, PE-1 |
| `mcp_tool` | 0.08% | ID-1, AD-2, PE-4 |
| `slash_command` | 0.09% | any `/command` usage |
| `plan_item` / `plan_status` | 0.19% | WF-1, WF-2 |
| `hung_seconds` | 0.23% | RES-2 |
| `loop_count` | 0.29% | AD-1, REC-2 |
| `stop_reason` | 0.30% | BA-1 |
| `mcp_tools_available` | 0.33% | ID-1, ALM-1 |
| `risk_markers` | 0.36% | IR-1, RM2-1, PE-1 |
| `cache_write_tokens` | 0.37% | RES-1 |
| `tool_timeout_ms` | 0.46% | RES-2 |

---

## Recommended sequencing

1. **Harvest Bucket A first (cheap wins).** Map the ~15 fields already sitting in `payload`
   (especially `request_id`, `ttft_ms`, `mcp_transport`, `error_message`, `denial_reason`) in the
   curation layer (`collector_framework/curate/adapter_source_events.py` + `export/mapping.py`).
   No collector change, no re-collection — just richer observables from data already stored.
2. **Wire Bucket A2** into the pipeline where derived observables / redaction run.
3. **Populate the sparse tier** by running the scenario pack (`docs/scenario_prompts.md`).
4. **Defer Bucket B** to the post-hoc collector-enhancement loop, driven by behavioural questions
   — starting with the highest-value governance field, `injection_markers`, and the reference-data
   item `team_id`.

---

## Refresh query

```sql
SELECT key AS column_name,
       count(*) FILTER (WHERE value IS NOT NULL AND value <> '' AND value <> '{}' AND value <> '[]') AS populated_rows,
       round(100.0 * count(*) FILTER (WHERE value IS NOT NULL AND value <> '' AND value <> '{}' AND value <> '[]') / count(*), 2) AS pct_populated
FROM public.observables o, jsonb_each_text(to_jsonb(o)) AS kv(key, value)
GROUP BY key
ORDER BY populated_rows ASC, column_name;
```
