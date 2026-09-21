-- ============================================================================
-- Project Signal — observables table
--
-- The curated layer. One row per observable occurrence, assembled from
-- however many collector rows contributed. Source rows stay untouched in
-- their own tables; this is derived from them.
--
-- Covers Claude Code and Cursor. Fields that only one tool can supply are
-- kept anyway, so the same table serves Codex, Copilot and Antigravity later.
--
-- Postgres / Supabase.
-- ============================================================================

create extension if not exists "pgcrypto";

-- ---------------------------------------------------------------------------
-- enums, so bad values fail at write time rather than at analysis time
-- ---------------------------------------------------------------------------

create type signal_tool as enum (
  'claude-code', 'cursor', 'codex', 'copilot', 'antigravity', 'custom', 'unknown'
);

create type signal_surface as enum (
  'chat', 'agent', 'cli', 'terminal', 'ide', 'mcp', 'filesystem', 'cloud', 'model'
);

create type signal_collector as enum (
  'local_hooks', 'otel', 'cli_wrapper', 'mcp_proxy', 'fs_watcher', 'webhook', 'derived'
);

create type signal_observable_type as enum (
  'session_start', 'session_end',
  'user_prompt', 'agent_response', 'agent_reasoning',
  'model_request',
  'tool_request', 'tool_result', 'tool_failure', 'tool_batch',
  'mcp_request', 'mcp_result', 'mcp_blocked', 'mcp_discovery',
  'shell_request', 'shell_result', 'shell_stall', 'shell_interrupt',
  'file_read', 'file_create', 'file_modify', 'file_delete',
  'file_rename', 'file_revert', 'file_permission_change',
  'permission_request', 'permission_granted', 'permission_denied',
  'permission_mode_change',
  'subagent_start', 'subagent_stop', 'delegation',
  'plan_created', 'plan_completed', 'compaction',
  'transcript_appended', 'artifact_written',
  'collector_config_changed', 'notification',
  'derived_signal', 'other'
);

create type signal_attribution as enum (
  'agent', 'human', 'subagent', 'other_process', 'unknown'
);

create type signal_confidence as enum (
  'reported',   -- the tool said so directly
  'observed',   -- we saw the effect, not the cause
  'parsed',     -- read out of a file the tool wrote
  'correlated', -- assembled from more than one source
  'inferred'    -- computed, nothing emitted it
);

-- ---------------------------------------------------------------------------
-- the table
-- ---------------------------------------------------------------------------

create table observables (

  -- === identity of this record ============================================
  observable_id        uuid primary key default gen_random_uuid(),
  schema_version       text        not null default 'signal-obs-v1',
  occurred_at          timestamptz not null,          -- when it happened
  captured_at          timestamptz not null default now(),
  ingested_at          timestamptz not null default now(),
  sequence_num         bigint,                        -- ordering within a turn

  -- === what tool ===========================================================
  tool                 signal_tool not null,
  tool_version         text,
  surface              signal_surface,

  -- === correlation keys ====================================================
  -- Claude Code: session_id / prompt_id.  Cursor: conversation_id / generation_id.
  session_id           text not null,
  conversation_id      text,
  turn_id              text,          -- prompt_id (CC) or generation_id (Cursor)
  message_id           text,
  tool_use_id          text,
  request_id           text,

  -- === agent identity ======================================================
  agent_id             text,
  agent_type           text,
  parent_agent_id      text,
  is_multi_agent       boolean,
  multi_agent_source   text,          -- platform_determined | human_determined
  delegation_depth     smallint default 0,

  -- === operator and org ====================================================
  operator_email       text,
  operator_id          text,
  operator_username    text,
  org_id               text,
  team_id              text,
  hostname             text,
  machine_id           text,

  -- === where ===============================================================
  workspace            text,
  cwd                  text,
  repo_name            text,
  git_branch           text,
  git_commit           text,

  -- === what happened =======================================================
  observable_type      signal_observable_type not null,
  status               text,          -- ok | error | denied | timeout | blocked
  success              boolean,
  error_type           text,
  error_message        text,
  latency_ms           integer,

  -- === prompt and conversation =============================================
  prompt_text          text,
  prompt_length        integer,
  prompt_word_count    integer,
  response_text        text,
  response_index       smallint,      -- streamed responses arrive in parts
  response_is_final    boolean,
  reasoning_text       text,
  attachment_count     smallint,
  composer_mode        text,          -- agent | ask | edit
  slash_command        text,
  mentions_files       boolean,

  -- === model call ==========================================================
  model                text,
  model_id             text,
  input_tokens         integer,
  output_tokens        integer,
  cache_read_tokens    integer,
  cache_write_tokens   integer,
  total_tokens         integer,
  cost_usd             numeric(12,6),
  cost_is_estimated    boolean default false,
  request_latency_ms   integer,
  time_to_first_token  integer,
  stop_reason          text,
  thinking_blocks      smallint,
  sampling_params      jsonb,
  effort_level         text,

  -- === tool call ===========================================================
  tool_name            text,
  tool_category        text,          -- shell | filesystem | web | mcp | subagent
  tool_is_builtin      boolean,
  tool_arguments       jsonb,
  tool_result          jsonb,
  tool_result_size     integer,
  tool_duration_ms     integer,
  tool_interrupted     boolean,
  tool_timeout_ms      integer,
  batch_size           smallint,

  -- === mcp =================================================================
  mcp_server           text,
  mcp_tool             text,
  mcp_transport        text,
  mcp_server_version   text,
  mcp_tools_available  text[],

  -- === shell ===============================================================
  command              text,
  command_cwd          text,
  exit_code            integer,
  stdout               text,
  stderr               text,
  output_size          integer,
  sandboxed            boolean,
  risk_markers         text[],
  hung_seconds         numeric(8,2),
  is_developer_command boolean,       -- run by the human, not the agent

  -- === files ===============================================================
  file_path            text,
  file_extension       text,
  file_change_kind     text,          -- create | modify | delete | rename
  file_size            bigint,
  content_hash         text,
  old_content          text,
  new_content          text,
  lines_added          integer,
  lines_removed        integer,
  chars_added          integer,
  chars_removed        integer,
  reverted_to_earlier  boolean,
  outside_workspace    boolean,
  is_config_file       boolean,
  is_dependency_manifest boolean,
  is_lockfile          boolean,
  is_binary            boolean,

  -- === permission and human oversight ======================================
  permission_mode      text,
  permission_decision  text,          -- allow | deny | ask
  permission_source    text,          -- config | user_temporary | user_permanent | hook
  permission_scope     text,          -- once | always
  permission_options   jsonb,         -- what escalation was offered
  denial_reason        text,

  -- === agent behaviour =====================================================
  turn_count           integer,
  loop_count           integer,
  plan_item            text,
  plan_status          text,
  background_tasks     smallint,
  scheduled_jobs       smallint,
  compaction_reason    text,
  instructions_loaded  text[],

  -- === risk ================================================================
  sensitivity_tier     smallint default 1 check (sensitivity_tier between 1 and 3),
  violation_count      smallint default 0,
  escalation_flag      boolean default false,
  secret_detected      boolean default false,
  injection_markers    text[],
  policy_rule_matched  text,

  -- === provenance, the part that makes this curated ========================
  collector            signal_collector not null,   -- the winning source
  contributing         signal_collector[],          -- everything that reported it
  collectors_agree     boolean,                     -- false is itself a signal
  disagreement         jsonb,                       -- what each said, when they differ
  attribution          signal_attribution default 'unknown',
  confidence           signal_confidence not null,
  derived_from         uuid[],                      -- source observable ids
  raw_ref              text,                        -- pointer to the raw event

  -- === anything a collector had that this schema does not ==================
  extra                jsonb default '{}'::jsonb
);

-- ---------------------------------------------------------------------------
-- indexes for the queries we actually run
-- ---------------------------------------------------------------------------

create index on observables (session_id, occurred_at);
create index on observables (turn_id);
create index on observables (tool, occurred_at desc);
create index on observables (operator_email, occurred_at desc);
create index on observables (observable_type, occurred_at desc);
create index on observables (agent_id) where agent_id is not null;
create index on observables (file_path) where file_path is not null;
create index on observables (occurred_at desc);

-- fleet questions: same file or repo touched by several agents in a window
create index on observables (workspace, file_path, occurred_at)
  where file_path is not null;

-- governance: everything that should be looked at
create index on observables (occurred_at desc)
  where escalation_flag or violation_count > 0 or sensitivity_tier >= 3;

-- collectors disagreeing is a finding in its own right
create index on observables (occurred_at desc) where collectors_agree = false;

-- ---------------------------------------------------------------------------
-- source rows stay separate and immutable
-- ---------------------------------------------------------------------------

create table source_events (
  source_id     uuid primary key default gen_random_uuid(),
  received_at   timestamptz not null default now(),
  tool          signal_tool not null,
  collector     signal_collector not null,
  session_id    text,
  event_name    text,
  payload       jsonb not null,
  file_origin   text
);

create index on source_events (session_id, received_at);
create index on source_events (collector, received_at desc);
create index on source_events using gin (payload);
