-- COPS runtime tables (collector/cops/runtime). Additive to cops_schema.sql.
-- Column lists mirror cops/runtime/persistence.py TABLES; tests check they agree.
-- Nothing in the runtime applies this migration or writes to these tables
-- unless a caller passes a poster explicitly (persistence.SupabaseRuntimeWriter).

alter table cop_decisions add column if not exists case_id text;
alter table cop_decisions add column if not exists tenant_id text;
alter table cop_decisions add column if not exists contract_version text;
alter table cop_decisions add column if not exists cop_mode text;
alter table cop_decisions add column if not exists decision_status text;
alter table cop_decisions add column if not exists verified boolean;
alter table cop_decisions add column if not exists finding jsonb;
alter table cop_decisions add column if not exists verifier_results jsonb;
alter table cop_decisions add column if not exists proposed_action jsonb;
alter table cop_decisions add column if not exists execution_status text;
alter table cop_decisions add column if not exists llm_calls int;
alter table cop_decisions add column if not exists tool_calls int;
alter table cop_decisions add column if not exists node_timings jsonb;
create index if not exists cop_decisions_case on cop_decisions (case_id, cop);

create table if not exists cop_llm_invocations (
  invocation_id        text primary key,
  decision_id          uuid,
  cop                  text,
  case_id              text,
  purpose              text,
  provider             text,
  model                text,
  model_version        text,
  prompt_version       text,
  simulated            boolean,
  evidence_refs        text[],
  tools_exposed        text[],
  input_tokens         int,
  output_tokens        int,
  latency_ms           numeric,
  cost_usd             numeric,
  cost_basis           text,
  structured_response  jsonb,
  validation           jsonb,
  retry_count          int,
  error                text,
  timed_out            boolean
);
create table if not exists cop_tool_calls (
  tool_call_id         text primary key,
  decision_id          uuid,
  cop                  text,
  case_id              text,
  tool                 text,
  args                 jsonb,
  requested_by         text,
  status               text,
  rejection_reason     text,
  result_summary       jsonb,
  injection_flags      text[],
  latency_ms           numeric
);
create table if not exists cop_memory_records (
  record_id            text primary key,
  tenant_id            text,
  agent_scope          text,
  owner_cop            text,
  record_type          text,
  payload              jsonb,
  provenance           jsonb,
  validation_status    text,
  created_at           timestamptz,
  expires_at           timestamptz
);
create table if not exists cop_memory_access_log (
  access_id            text primary key,
  at                   timestamptz,
  cop                  text,
  case_id              text,
  tenant_id            text,
  operation            text,
  record_type          text,
  record_id            text,
  outcome              text,
  reason               text
);
create index if not exists cop_memory_lookup on cop_memory_records (tenant_id, record_type, validation_status);
create index if not exists cop_memory_access_denied on cop_memory_access_log (tenant_id, outcome);
-- Tenant isolation is enforced in the runtime's MemoryGateway. If these tables are
-- exposed through PostgREST, add row-level security keyed on tenant_id as well;
-- that policy depends on how Supabase auth identifies a tenant and is not written here.
