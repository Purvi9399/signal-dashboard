-- ============================================================================
-- Continuous pipeline (collector/cops/runtime/pipeline.py). Not applied anywhere yet.
--
-- Apply after observables_schema.sql, cops_schema.sql and cops_runtime_schema.sql, and only with
-- the owner's approval. pipeline.SupabaseSink refuses to run until pipeline_schema_check reports
-- every unique key below, so nothing relies on a constraint that is not there.
-- Column lists mirror pipeline.PIPELINE_TABLES; tests/test_pipeline.py checks they agree.
-- ============================================================================

-- Late evidence: a filled field marks its record revised, so the pipeline sees the session again.
alter table observables add column if not exists revised_at timestamptz;
create index if not exists observables_ingested on observables (ingested_at);
create index if not exists observables_revised on observables (revised_at);

-- The keys signal_curate.to_supabase already relies on (its comment says a UNIQUE index enforces them;
-- neither is in this repository's SQL).
create unique index if not exists observables_dedup_key_key on observables (dedup_key);
create unique index if not exists source_events_source_ref_key on source_events (source_ref);

-- Which baseline and inputs a metric value was computed from.
alter table metric_events add column if not exists baseline_version text;
alter table metric_events add column if not exists inputs_digest text;

create table if not exists pipeline_checkpoints (
  name text primary key, watermark text, run_id text, updated_at timestamptz
);

create table if not exists pipeline_runs (
  run_id text primary key, started_at timestamptz, finished_at timestamptz, status text,
  watermark_from text, watermark_to text, sessions jsonb, versions jsonb, error text
);

create table if not exists pipeline_session_state (
  session_id text primary key, status text, attempts int, inputs_digest text, route_key text,
  investigation_id text, previous_investigation_id text, baseline_version text, metric_version text,
  last_run_id text, updated_at timestamptz, error text
);

create table if not exists pipeline_baselines (
  baseline_version text primary key, metric_version text, metrics_config_sha256 text,
  signal_metrics_sha256 text, cut text, sessions int, observables_digest text, reason text,
  created_at timestamptz
);

create table if not exists pipeline_decision_versions (
  decision_id text primary key, investigation_id text, run_id text, case_id text, session_id text,
  cop text, label text, status text, verified boolean, route_key text, inputs_digest text,
  events_digest text, metric_version text, metrics_config_sha256 text, signal_metrics_sha256 text,
  signal_traces_sha256 text, baseline_version text, policy_version text, package_version text,
  mapping_version text, contract_version text, verifier_registry text, supersedes text,
  evaluated_at timestamptz
);

create table if not exists observable_conflicts (
  conflict_id text primary key, observable_id text, dedup_key text, session_id text, field text,
  kept_value jsonb, kept_source text, incoming_value jsonb, incoming_source text, status text,
  seen_at timestamptz
);

create table if not exists cop_activation_records (
  record_id text primary key, seq int, case_id text, investigation_id text, cop text, transition text,
  mapping_version text, policy_id text, decision_id text, prev_record_id text, at text, body jsonb
);

-- Read by pipeline.verify_schema through PostgREST: every unique index on the tables it relies on.
create or replace view pipeline_schema_check as
select t.relname::text as table_name,
       array_agg(a.attname::text order by k.ord)::text[] as columns,
       i.indisunique as is_unique
from pg_index i
join pg_class t on t.oid = i.indrelid
join pg_namespace n on n.oid = t.relnamespace and n.nspname = 'public'
join lateral unnest(i.indkey) with ordinality as k(attnum, ord) on true
join pg_attribute a on a.attrelid = t.oid and a.attnum = k.attnum
where i.indisunique
group by t.relname, i.indexrelid, i.indisunique;
