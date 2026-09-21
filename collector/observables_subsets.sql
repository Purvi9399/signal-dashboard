-- ============================================================================
-- Project Signal — themed subsets
--
-- One master table, ten themed subsets over it. Fields repeat across subsets
-- on purpose, which is free here because the subsets are views rather than
-- copies. Nothing has to be kept in sync and nothing can drift.
--
-- If a subset later needs to be a real table (for speed, or to hand to
-- someone without access to the master), change `create view` to
-- `create materialized view` and add a refresh. The field lists do not change.
-- ============================================================================


-- ============================================================================
-- COMMON THEMESET MASTER
--
-- Every subset carries these. They are what makes a row joinable, orderable
-- and attributable. Sundar's rule: whatever the source format, it must
-- preserve a timestamp and an identifier.
-- ============================================================================

create or replace view common_themeset as
select
  observable_id,
  schema_version,
  occurred_at,
  captured_at,
  sequence_num,

  tool,
  tool_version,
  surface,

  session_id,
  conversation_id,
  turn_id,

  operator_email,
  workspace,

  observable_type,
  status,
  success,

  collector,
  contributing,
  collectors_agree,
  confidence,
  attribution
from observables;


-- ============================================================================
-- 1. AGENT IDENTITY
--    Who acted. One agent or several, and which one.
-- ============================================================================

create or replace view obs_agent_identity as
select
  -- common
  observable_id, occurred_at, sequence_num, tool, tool_version, surface,
  session_id, conversation_id, turn_id, observable_type,
  collector, contributing, confidence, attribution,
  -- subset
  agent_id,
  agent_type,
  parent_agent_id,
  is_multi_agent,
  multi_agent_source,
  delegation_depth,
  operator_email,
  operator_id,
  operator_username,
  org_id,
  team_id,
  hostname,
  machine_id,
  raw_ref                      -- subagent transcript pointer
from observables
where agent_id is not null
   or parent_agent_id is not null
   or is_multi_agent is not null
   or operator_email is not null
   or observable_type in ('session_start','session_end',
                          'subagent_start','subagent_stop');


-- ============================================================================
-- 2. CAPABILITY / RESTRICTIONS
--    What the agent was allowed to do, what it asked for, what was refused.
-- ============================================================================

create or replace view obs_capability_restrictions as
select
  observable_id, occurred_at, sequence_num, tool, session_id, turn_id,
  observable_type, collector, confidence, attribution,
  -- what was asked and decided
  permission_mode,
  permission_decision,
  permission_source,
  permission_scope,
  permission_options,          -- what escalation was offered to the human
  denial_reason,               -- not obtainable on either tool today
  -- the boundary the agent operated inside
  sandboxed,
  tool_is_builtin,
  mcp_tools_available,         -- the full menu it could reach, not just what it used
  outside_workspace,
  -- policy
  policy_rule_matched,
  escalation_flag,
  violation_count,
  -- context
  tool_name,
  file_path,
  command
from observables
where permission_decision is not null
   or permission_mode is not null
   or sandboxed is not null
   or escalation_flag is true
   or mcp_tools_available is not null
   or observable_type in ('permission_request','permission_granted',
                          'permission_denied','permission_mode_change',
                          'mcp_blocked','mcp_discovery');


-- ============================================================================
-- 3. TASK & EXECUTION
--    What was asked for, what happened, whether it finished.
-- ============================================================================

create or replace view obs_task_execution as
select
  observable_id, occurred_at, sequence_num, tool, session_id, turn_id,
  observable_type, collector, confidence,
  -- the ask
  prompt_text,
  prompt_length,
  prompt_word_count,
  composer_mode,
  slash_command,
  attachment_count,
  mentions_files,
  -- the work
  turn_count,
  loop_count,
  plan_item,
  plan_status,
  batch_size,
  -- the answer
  response_text,
  response_index,
  response_is_final,
  reasoning_text,
  -- the outcome
  status,
  success,
  error_type,
  error_message,
  stop_reason
from observables
where observable_type in ('user_prompt','agent_response','agent_reasoning',
                          'plan_created','plan_completed','session_end',
                          'tool_batch')
   or prompt_text is not null
   or response_text is not null
   or plan_item is not null;


-- ============================================================================
-- 4. A2A INTERACTION
--    Agent to agent. Delegation, handoff, and what came back.
-- ============================================================================

create or replace view obs_a2a_interaction as
select
  observable_id, occurred_at, sequence_num, tool, session_id, turn_id,
  observable_type, collector, confidence,
  -- who delegated to whom
  agent_id,
  agent_type,
  parent_agent_id,
  delegation_depth,
  is_multi_agent,
  multi_agent_source,
  -- the delegation itself
  tool_name,                   -- Task, Agent, invoke_subagent
  tool_arguments,              -- what the subagent was told to do
  tool_result,                 -- what it sent back
  tool_duration_ms,
  -- what it cost
  input_tokens,
  output_tokens,
  total_tokens,
  cost_usd,
  -- its own record
  raw_ref,                     -- agent_transcript_path
  -- agent to tool, the other kind of A2A
  mcp_server,
  mcp_tool,
  mcp_transport
from observables
where observable_type in ('subagent_start','subagent_stop','delegation',
                          'mcp_request','mcp_result','mcp_blocked')
   or agent_id is not null
   or parent_agent_id is not null
   or tool_name in ('Task','Agent','invoke_subagent','dispatch_agent');


-- ============================================================================
-- 5. PERFORMANCE FACTORS
--    How long things took, and where the time went.
-- ============================================================================

create or replace view obs_performance as
select
  observable_id, occurred_at, sequence_num, tool, session_id, turn_id,
  observable_type, collector, confidence,
  -- latency at each layer
  latency_ms,                  -- whatever this observable measured
  request_latency_ms,          -- the model call
  time_to_first_token,
  tool_duration_ms,            -- one tool call
  -- the thing only the wrapper sees
  hung_seconds,                -- silence while the process was still alive
  -- volume, which drives latency
  input_tokens,
  output_tokens,
  total_tokens,
  cache_read_tokens,
  tool_result_size,
  output_size,
  batch_size,
  loop_count,
  turn_count,
  -- outcome, since a fast failure is not a fast success
  success,
  status,
  error_type
from observables
where latency_ms is not null
   or request_latency_ms is not null
   or tool_duration_ms is not null
   or hung_seconds is not null
   or time_to_first_token is not null;


-- ============================================================================
-- 6. TOOL USE
--    Which tools, with what arguments, and what came back.
-- ============================================================================

create or replace view obs_tool_use as
select
  observable_id, occurred_at, sequence_num, tool, session_id, turn_id,
  observable_type, collector, confidence, attribution,
  tool_use_id,
  -- the call
  tool_name,
  tool_category,
  tool_is_builtin,
  tool_arguments,
  tool_timeout_ms,
  -- the result
  tool_result,
  tool_result_size,
  tool_duration_ms,
  tool_interrupted,
  success,
  error_type,
  -- batching
  batch_size,
  -- external tools specifically
  mcp_server,
  mcp_tool,
  mcp_transport,
  mcp_server_version,
  mcp_tools_available,
  -- what the call touched
  file_path,
  command,
  sensitivity_tier
from observables
where tool_name is not null
   or mcp_server is not null
   or observable_type in ('tool_request','tool_result','tool_failure',
                          'tool_batch','mcp_request','mcp_result',
                          'mcp_blocked','mcp_discovery');


-- ============================================================================
-- 7. MODEL FACTORS
--    The LLM call. Cost, tokens, and what the model decided.
-- ============================================================================

create or replace view obs_model_factors as
select
  observable_id, occurred_at, sequence_num, tool, session_id, turn_id,
  observable_type, collector, confidence,
  -- which model
  model,
  model_id,
  -- what it consumed
  input_tokens,
  output_tokens,
  cache_read_tokens,
  cache_write_tokens,
  total_tokens,
  -- what it cost
  cost_usd,
  cost_is_estimated,           -- true on Cursor, which emits no cost field
  -- how it behaved
  request_latency_ms,
  time_to_first_token,
  stop_reason,
  thinking_blocks,
  reasoning_text,
  effort_level,
  sampling_params,
  -- what it produced
  response_text,
  response_index,
  response_is_final
from observables
where model is not null
   or total_tokens is not null
   or cost_usd is not null
   or observable_type in ('model_request','agent_response','agent_reasoning');


-- ============================================================================
-- 8. RUNTIME FACTORS
--    The machine and the shell. Where the work actually ran.
-- ============================================================================

create or replace view obs_runtime as
select
  observable_id, occurred_at, sequence_num, tool, tool_version, surface,
  session_id, turn_id, observable_type, collector, confidence, attribution,
  -- the machine
  hostname,
  machine_id,
  operator_username,
  -- the place
  workspace,
  cwd,
  command_cwd,
  repo_name,
  git_branch,
  git_commit,
  -- the shell
  command,
  exit_code,
  stdout,
  stderr,
  output_size,
  sandboxed,
  risk_markers,
  hung_seconds,
  is_developer_command,        -- the human's own commands, wrapper only
  -- how long
  latency_ms,
  tool_duration_ms
from observables
where command is not null
   or exit_code is not null
   or hostname is not null
   or git_branch is not null
   or observable_type in ('shell_request','shell_result','shell_stall',
                          'shell_interrupt','session_start','session_end');


-- ============================================================================
-- 9. STATE MANAGEMENT
--    Session lifecycle, context, memory and what the agent was told.
-- ============================================================================

create or replace view obs_state_management as
select
  observable_id, occurred_at, sequence_num, tool, session_id, conversation_id,
  turn_id, observable_type, collector, confidence,
  -- lifecycle
  status,
  error_type,                  -- carries the exit reason on session_end
  turn_count,
  loop_count,
  -- context
  compaction_reason,
  cache_read_tokens,           -- proxy for how much context was reused
  total_tokens,
  -- what was loaded into the agent
  instructions_loaded,
  -- what is still running
  background_tasks,
  scheduled_jobs,
  -- the record on disk
  raw_ref,
  file_path,                   -- transcript or artifact path on those rows
  file_size
from observables
where observable_type in ('session_start','session_end','compaction',
                          'transcript_appended','artifact_written')
   or compaction_reason is not null
   or background_tasks is not null
   or scheduled_jobs is not null
   or instructions_loaded is not null;


-- ============================================================================
-- 10. DATA & SECURITY
--     What was touched, what was sensitive, and what looked wrong.
-- ============================================================================

create or replace view obs_data_security as
select
  observable_id, occurred_at, sequence_num, tool, session_id, turn_id,
  observable_type, collector, confidence, attribution,
  -- what was touched
  file_path,
  file_extension,
  file_change_kind,
  file_size,
  content_hash,
  old_content,
  new_content,
  lines_added,
  lines_removed,
  chars_added,
  chars_removed,
  reverted_to_earlier,
  -- what kind of thing it was
  is_config_file,
  is_dependency_manifest,
  is_lockfile,
  is_binary,
  outside_workspace,
  -- risk
  sensitivity_tier,
  secret_detected,
  injection_markers,
  risk_markers,
  violation_count,
  escalation_flag,
  policy_rule_matched,
  -- what caused it
  tool_name,
  command,
  mcp_server
from observables
where file_path is not null
   or sensitivity_tier >= 2
   or secret_detected is true
   or escalation_flag is true
   or violation_count > 0
   or injection_markers is not null
   or observable_type::text like 'file%';


-- ============================================================================
-- 11. SPECIFIC SUBSETS
--     Per tool, so a tool-only field does not clutter the shared views.
--     Add one per tool as it is onboarded.
-- ============================================================================

create or replace view obs_claude_code as
select *
from observables
where tool = 'claude-code';

create or replace view obs_cursor as
select *
from observables
where tool = 'cursor';


-- ============================================================================
-- collector capability, kept beside the observables rather than inside them
--
-- Declared is what the tool can support. Observed is what has actually
-- arrived. The gap between them is the finding: a collector that is
-- applicable and configured but silent is a failure, and one that is not
-- applicable at all is not.
-- ============================================================================

create table if not exists collector_capability (
  capability_id   uuid primary key default gen_random_uuid(),
  tool            signal_tool not null,
  collector       signal_collector not null,
  declared        boolean not null,        -- the tool can support this
  configured      boolean not null default false,
  first_seen      timestamptz,
  last_seen       timestamptz,
  rows_seen       bigint default 0,
  observed_state  text,                    -- reporting | silent | not_applicable
  note            text,
  updated_at      timestamptz not null default now(),
  unique (tool, collector)
);

create index on collector_capability (tool);
create index on collector_capability (observed_state);
