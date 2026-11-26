# Applying `cops_schema.sql` to the live Supabase DB (D-0007 / D-0008)

**You run this** (MCP is intentionally read-only). This is the **first schema change** to the
live project `cdftgybwybuzcyurlccf`. Read the impact section before running.

## What it creates (5 new tables + indexes)
- `trace_sessions`, `trace_steps`, `trace_effects` — per-session execution traces (written by `signal_traces.py`)
- **`metric_events`** — one row per (session × metric); the persisted signals layer (written by `signal_metrics.py`)
- **`cop_decisions`** — one row per COP run (written by `cops.run --write`)

## Impact — low, by design
- **Additive only.** Every statement is `create table if not exists` / `create index if not exists` /
  `alter … add column if not exists`. It **cannot** modify or drop the existing 10 observability tables,
  and changes **no existing data**.
- **Idempotent.** Safe to run more than once (re-running is a no-op).
- **Decoupled.** Nothing starts writing to these tables until you run `signal_metrics.py` / `cops.run`.
  Creating them changes no current behaviour.
- ⚠️ **RLS:** like `observables`, these tables will have **RLS disabled** → readable/writable with the
  anon key. Consistent with the current posture, but the advisor will flag it. Add RLS policies before
  exposing beyond the service role (separate decision).

## Steps
1. **Pre-check (confirm absent).** In the Supabase **SQL Editor**, run:
   ```sql
   select table_name from information_schema.tables
   where table_name in ('metric_events','cop_decisions','trace_sessions','trace_steps','trace_effects');
   ```
   Expect **0 rows** (none exist yet).
2. **Apply.** Paste the **entire** contents of `signal_cops/collector/cops_schema.sql` (the superset — it has
   `deviation` + `context` on `metric_events` and `lens` on `cop_decisions`) into the SQL Editor and **Run**.
   > Use THIS file's schema, not Purvi's older `collector/cops_schema.sql` — the older one lacks
   > `deviation`/`context`, which `signal_metrics.py` writes (D-0008).
3. **Verify.** Re-run the pre-check query → expect **5 rows**. Then confirm the key columns exist:
   ```sql
   select column_name from information_schema.columns
   where table_name='metric_events' and column_name in ('deviation','context','value','state');
   ```
   Expect all 4.
4. **Populate (optional, after apply).** From the collector dir, with `SUPABASE_URL` + `SUPABASE_KEY` set:
   `python3 signal_metrics.py` → writes `metric_events`. (Idempotent; re-runs upsert.)

## Rollback (if ever needed)
Purely additive, so rollback is just dropping the new tables (no effect on observables):
```sql
drop table if exists cop_decisions, metric_events, trace_effects, trace_steps, trace_sessions cascade;
```

## After applying
- Update **DECISIONS.md D-0007** status Pending team → **Applied** (in both repos, per D-0010).
- The dashboard can then read `metric_events`; `cops.run` (default path) can read instead of `--local`.
