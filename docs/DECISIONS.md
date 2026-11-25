# Project Signal — Decision Log (DECISIONS.md)

> Append-only record of **important, hard-to-reverse, or cross-cutting decisions**. Every new
> session loads this (imported from `CLAUDE.md`). Keep entries short; newest at the top. Status:
> **Accepted** / **Proposed** / **Pending team** / **Superseded**. Don't delete — supersede.
>
> **Mirrored** across both repos (`malhar-sovereign/project-signal` and
> `Purvi9399/signal-dashboard`). Both copies are canonical — **add every new entry to both** so
> a session started in either repo sees the same decisions.

---

## D-0010 · Mirror CLAUDE.md + DECISIONS.md + PROJECT_SIGNAL_VISION.md into Purvi's repo
**Date:** 2026-11-20 · **Status:** Accepted
Both repos are now actively worked on, but project context (`CLAUDE.md`, decision log, vision)
lived only in `project-signal`, so a session in `signal-dashboard` started blind. Decision: mirror
the three docs into Purvi's repo (via PR) and keep the decision log in sync in both. `CLAUDE.md`'s
`@docs/...` imports resolve in either repo since the docs sit at the same relative path.
**Why it matters:** cross-repo work needs shared context + one source of agreed decisions.

## D-0007 · Persist the `metric_events` (and `cop_decisions`) schema layer
**Date:** 2026-11-20 · **Status:** Pending team
Metrics are currently computed in memory by `signal_metrics.py`; the live DB has only the 10
observability tables. The `metric_events` and `cop_decisions` tables are **already designed** in
`signal_cops/collector/cops_schema.sql` but not applied to the live project.
**Correction (verified in `signal_cops/collector/cops/run.py:6-8,62-64` + `reader.py:88`):** COPs can
already consume metrics **without** this table via `cops.run --local` (compute in memory from
observables, no round trip). So the table is **not** required for COPs to function.
It **is** required for: (1) persistence/history → trend/deviation/corpus metrics that need past
values stored over time; (2) the default DB-read path (`cops.run` without `--local`), decoupling
compute from COP runs; (3) the demo dashboard/performance view and verifier audit trail.
**Decision needed:** approve applying `cops_schema.sql` (new tables, no change to existing 10).
**Why it matters:** first schema change to the live DB; blocks trend/corpus metrics + the default
DB path until done (COPs still runnable via `--local` meanwhile).
**Diligence (2026-11-20):** verified NOT implemented on the live DB by anyone.
- Explicit MCP `information_schema.tables` check across all schemas for `metric_events`,
  `cop_decisions`, `trace_sessions/steps/effects` → **empty (none exist)**.
- Same `cops_schema.sql` DDL is committed in BOTH repos on ~every branch (Malhar's
  `malhar-sovereign/project-signal`; Purvi's `Purvi9399/signal-dashboard`) — shared design, not
  one person's. `signal_metrics.py` only `post()`s rows assuming the table exists; no apply/migration
  script targets Supabase. Purvi's `runtime/persistence.py` creates these tables only in a **local
  SQLite** store, and her dashboard (`dashboard_export.py`) **reads** `metric_events` — so applying
  it unblocks her dashboard too. → Not duplicative, not conflicting.

## D-0009 · Collector divergence — `collector_framework` is canonical; bring it into Purvi's repo
**Date:** 2026-11-20 · **Status:** Accepted (direction) / execution pending
Two divergent collector codebases exist. **Curation richness is ~at parity** (both `signal_curate.py`
emit ~134 identical fields; Malhar only leads by `denial_reason`+`git_branch` capture fixes). **Capture
capability diverges sharply:**
- Malhar `collector_framework/` (modular): **5 tools** (claude, cursor, codex, copilot, antigravity) ×
  6 techniques (hooks, otel, cli_wrapper, mcp_proxy, fs_watcher, local_hooks) = 39 modules; **has OTel
  collection**.
- Purvi `collector/` (flat): **3 tools** (claude, cursor, antigravity), hooks + shared
  fs_watcher/mcp_proxy/wrap/git; **no codex, no copilot, no OTel collectors**.
**Decision:** `collector_framework` is the **canonical collector**; Purvi's flat `collector/` capture
files are legacy. Collection runs from `collector_framework`. We will **port `collector_framework`
into Purvi's repo** so her repo has full capability (goal: 5-tool + OTel capture there too).
**Risk:** two copies drift — prefer a single source (subtree/shared package) over a plain vendored
copy. Execution approach TBD (see merge options).

## D-0008 · Apply the NEWER `cops_schema.sql` (Malhar's), and port metric logic into Purvi's repo
**Date:** 2026-11-20 · **Status:** Accepted (team approved applying the schema)
Work now happens in Purvi's `signal-dashboard` repo (dashboard + signals/COPs consumer); the
collection/curation pipeline + capture fixes stay in Malhar's `project-signal` (Purvi's repo has no
`collectors/`/`curate/`/`export/`).
**Critical:** the two `cops_schema.sql` differ — **Malhar's is the superset** (adds `deviation` +
`context` on `metric_events`, `lens`/`inputs_hash` on `cop_decisions`, plus `alter ... add column`
upgrades). Malhar's `signal_metrics.py` **writes `deviation`/`context`**, so **apply Malhar's schema,
NOT Purvi's older one** — Purvi's would be missing those columns and break the writes.
Likewise Malhar's `signal_metrics.py` is the superset (has `lifecycle.*`, `telemetry.evidence_score`,
`event_freshness`); it must be ported INTO Purvi's repo or those metrics regress.
Deps (`signal_traces.py`, `signal_paths.py`) are already identical across repos. New columns are
additive → Purvi's dashboard/readers stay backward-compatible until updated.

## D-0006 · No schema changes to the live DB without team sign-off
**Date:** 2026-11-20 · **Status:** Accepted
New tables / new columns on the production Supabase project (`cdftgybwybuzcyurlccf`) require a team
decision first (logged here). **Allowed without sign-off:** inserting rows into existing tables
(telemetry generation) and populating existing-but-empty columns via capture fixes. **Not allowed:**
`CREATE TABLE` / `ADD COLUMN` / destructive DDL.
**Why:** the DB is shared and the one-directional chain means schema ripples downstream.

## D-0005 · Generate gap-metric data via scenario prompts run by real coding-agent sessions
**Date:** 2026-11-20 · **Status:** Accepted
Close the **data-availability** gap (not functionality) by running scenario prompts through real
agents → telemetry → observables → metrics compute. Pack: `docs/scenario_prompts.md`;
coverage matrix included. Behaviour-forcing prompts; personas (junior→lead) for behavioural variance.
**Verified:** implemented metrics now compute from generated data (e.g. `lifecycle.version_change`,
`insights.secrets`, `interaction.subagent_lanes`).

## D-0004 · `plan_item` populated via mapping + synthetic injection (accept organic)
**Date:** 2026-11-20 · **Status:** Accepted
Headless `claude -p` disables TodoWrite/ExitPlanMode, so `plan_item` can't be force-generated by
automation. Decision: (1) keep the correct mapping for real interactive usage; (2) for the demo,
seed deterministically via `package/synthetic_plan_inject.py` (synthetic TodoWrite events).

## D-0003 · Upload mode = `direct` for controlled test runs (edge for production)
**Date:** 2026-11-20 · **Status:** Accepted
The edge function (`signal-ingest`) hit repeated Cloudflare 5xx outages, losing teardown flushes.
Switched `SIGNAL_UPLOAD_MODE=direct` (PostgREST) for our runs — reliable. Production should stay on
`edge` (keeps the service-role key off devices). Retry-with-backoff added to **both** paths
(`edge_client.py`, `supabase_sink.py`).
**Caveat:** direct uses the service-role key locally; fine for the controlled pilot machine only.

## D-0002 · Capture-fix mappings added to the curation layer (local, uncommitted)
**Date:** 2026-11-20 · **Status:** Accepted
Added to `curate/signal_curate.py` + `curate/extract_fs_watcher.py`: `plan_item` (TodoWrite/
ExitPlanMode), `tool_version` + `git_branch` + `cwd` (transcript), `tool_result`/`is_error` →
`success`/`error_type`, and permission-denial detection → `permission_decision`/`denial_reason`.
These were Bucket-B/0% for claude-code before. **No schema change** — all existing columns.
**Open:** review + commit; `machine_id` and `telemetry.source_agreement` inputs still data-starved.

## D-0001 · Transcripts are the single source of truth for the vision
**Date:** 2026-11-19 · **Status:** Accepted
`docs/PROJECT_SIGNAL_VISION.md` is derived strictly from the `Signal 17Nov`/`18Nov` transcripts and
is the project bible (imported by `CLAUDE.md`). If a decision contradicts it, update the doc.

---

## Known infra notes (not decisions, but shared gotchas)
- `/usr/local/bin/python3` has **no SSL certs** (`certs: None`) → HTTPS uploads fail; use anaconda's
  python (certs present). Cron/scripts must put `/opt/anaconda3/bin` first in PATH.
- `ingest_batch_id` has **no index** on the 169k-row `source_events` → `curate --from-batch` times
  out. An index is recommended (schema change → see D-0006).
- MCP Supabase connection is **read-only** (no DDL/writes); writes go via PostgREST service role.
- Cursor can't be driven headlessly (`cursor-agent` not installed); codex/copilot capture is shallow
  (fs_watcher only) pending hook-trust / hook-routing work; antigravity (`agy`) writes outside the
  watched dir.
