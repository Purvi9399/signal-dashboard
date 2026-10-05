-- ============================================================================
-- Deterministic metric layer and COPS decisions.
--
-- trace_*        written by signal_traces.py (create if you haven't already;
--                columns match exactly what it posts)
-- metric_events  written by signal_metrics.py, one row per session x metric
-- cop_decisions  written by python3 -m cops.run --write, one row per COP run
--
-- Ids are deterministic, so re-running upserts instead of duplicating.
-- Apply the same tenant_id / RLS policies as observables before exposing
-- these beyond the service role.
-- ============================================================================

create table if not exists trace_sessions (
  session_id text primary key, tool text, operator text, workspace text, repo_name text,
  started_at timestamptz, ended_at timestamptz, duration_s numeric,
  turns int, lanes int, steps int, agents int, failed_steps int, approvals int,
  declined_inferred int, blocked int, effects int, unaccounted_effects int,
  tokens numeric, cost_usd numeric, wrapper_session_id text
);

create table if not exists trace_steps (
  step_id text primary key, session_id text not null, lane text, seq int, step text,
  tool_name text, turn_id text, agent_id text, parent_step_id text,
  started_at timestamptz, ended_at timestamptz, duration_ms numeric, failed boolean,
  exit_code text, command text, file_path text, mcp_server text, sensitivity_tier int,
  policy_rule_matched text, approval_wait_ms bigint, decided_by text,
  tokens numeric, cost_usd numeric, collector text, observable_id text
);
create index if not exists trace_steps_session_lane on trace_steps (session_id, lane, seq);

create table if not exists trace_effects (
  effect_id text primary key, session_id text, step_id text, kind text, file_path text,
  content_hash text, chars_added int, chars_removed int, reverted boolean,
  sensitivity_tier int, secret_detected boolean, outside_workspace boolean,
  git_commit text, occurred_at timestamptz, lag_s numeric, attributed_to text,
  observable_id text
);

create table if not exists metric_events (
  metric_event_id   uuid primary key,
  metric_id         text not null,
  metric_name       text,
  session_id        text not null,
  trace_id          text,
  tool              text,
  operator          text,
  workspace         text,
  value             numeric,
  threshold         numeric,
  direction         text,
  thresholds        jsonb,
  state             text not null,          -- NORMAL | WARNING | EXCEPTION | CRITICAL | UNKNOWN
  features          jsonb not null default '{}'::jsonb,
  evidence_refs     text[] not null default '{}',   -- observable:<id>, step:<id>
  required_fields   text[] not null default '{}',
  basis             text,
  basis_present     boolean,
  source            text,                    -- signal_metrics.py:<metric>@<version>
  metric_version    text,
  session_started_at timestamptz,
  session_ended_at  timestamptz,
  computed_at       timestamptz,
  error             text
);
create index if not exists metric_events_session on metric_events (session_id, metric_id);
create index if not exists metric_events_state on metric_events (metric_id, state);
create index if not exists metric_events_started on metric_events (session_started_at desc);

create table if not exists cop_decisions (
  decision_id          uuid primary key,
  run_id               text not null,
  cop                  text not null,
  cop_version          text,
  session_id           text not null,
  trace_id             text,
  metric_id            text,
  behavioural_question text,
  evidence_refs        text[] not null default '{}',
  metrics_read         jsonb not null default '[]'::jsonb,
  context_used         jsonb not null default '[]'::jsonb,
  upstream_decisions   text[] not null default '{}',
  interpretation       text,
  rationale            text,
  confidence           numeric(4,3),
  evidence_confidence  numeric(4,3),
  severity             text,
  action               text,
  action_reason        text,
  downstream_cops      text[] not null default '{}',
  interpreter          text,
  skipped_interpreter  boolean,
  mode                 text,
  evaluation_ms        int,
  failed               boolean,
  error                text,
  decided_at           timestamptz,
  inputs_hash          text
);
create index if not exists cop_decisions_session on cop_decisions (session_id, cop);
create index if not exists cop_decisions_action on cop_decisions (cop, action);
create index if not exists cop_decisions_failed on cop_decisions (failed) where failed;
