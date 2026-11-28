# Context & Memory: specification

COP 12 in the build order: Runtime Monitoring, Interaction Discovery, Audit & Traceability, Policy Enforcement,
Anomaly Detection, Resource Management, Classification & Tagging, Risk Mitigation, Incident Response, Recovery &
Resilience, Behavior Analytics, then Context & Memory. The workbook and `docs/cops/cop_catalogue.json` number this
COP 4. The build order is recorded apart from that numbering.

`METRICS.yaml` is the contract the runtime reads. This file explains it. `DECISIONS.md` records every choice, the
previous contract and the open questions.

## Responsibility (changed on purpose, purvi 2026-11-28)

Context & Memory is a dedicated memory retrieval and routing COP. It answers:

> What previously recorded information is relevant to the requesting COP's current question, and can that
> information be retrieved reliably with its provenance intact?

It retrieves and organizes, for a requesting COP:

- previous COP findings and decisions;
- historical session evidence;
- validated precedents;
- previous incidents and recovery outcomes;
- previously recorded context;
- historical observations relevant to the requester's question.

It detects nothing. Context loss, contradiction, drift, anomaly, policy violation, incident and risk are not its
questions, and it never produces a governance judgement that competes with another COP. The previous contract
(question "Is the current context internally consistent, relevant to the task, and supported by useful
precedent?", labels CONTEXT_OK, STALE, CONTRADICTORY, CONTEXT_LOSS, PRECEDENT_FOUND) is kept in
`METRICS.yaml previous_contract`, in `rules._context_memory_previous_contract` (never called) and in `DECISIONS.md`.

## Workflow (D-CM7)

`cop_package.retrieve_memory(gateway, request)`. Each stage is written to `bundle.stages` with its input, output and
failure.

| Stage | Code | Fails as |
|---|---|---|
| request | `cm_request` | INVALID_REQUEST (a required identifier missing; nothing is filled in) |
| scope determination | `cm_scope` (the requester's own `memory.POLICIES` reads and scope) | ACCESS_DENIED when no asked type is readable; others NOT_ENTITLED |
| memory lookup | `MemoryGateway.read(..., include_excluded=True)` as the requester | STORAGE_UNAVAILABLE (no gateway, or the store raised) |
| access validation | the gateway's tenant, type, agent-scope, expiry and rejection rules | ACCESS_DENIED (cross-tenant request); EXPIRED, REJECTED listed |
| provenance validation | `cm_provenance`, `tools.scan` | INSUFFICIENTLY_SUPPORTED records; INSTRUCTION_SHAPED_CONTENT withheld |
| relevance filtering | `cm_relevance`, duplicates by event identity, ranking, max_results | FUTURE_EVENT, OUTSIDE_TIME_RANGE, OUT_OF_SESSION_SCOPE, NOT_RELEVANT, DUPLICATE_OF, LIMIT_REACHED |
| structured response | the bundle (`cm_bundle/1`) | |
| consumption record | `MemoryGateway.log` (one `cop_memory_access_log` row) | NOT_LOGGED limitation |

## Request (CM-1)

Required: `requesting_cop`, `tenant_id`, `case_id`, `behavioural_question`. Optional: `question_id`, `agent_scope`,
`session_id`, `run_id`, `memory_types`, `as_of`, `since`, `until`, `session_scope` (any, same_session,
cross_session), `entities` ({payload field: values}), `max_results`, `purpose`, `requested_tenant`. The runtime builds
requests only from identifiers the case and run already hold (`runtime_request`).

## Bundle

`contract`, `bundle_id`, `package_version`, `request`, `retrieved_at`, `scope`, `retrieval_status`, `label`,
`answers`, `records`, `buckets`, `excluded`, `not_entitled`, `disagreements`, `missing`, `limitations`,
`source_references`, `absence_is_evidence` (always false), `denied`, `storage_error`, `operations`, `stages`,
`consumption_record`.

Each record carries its own fields unchanged (`record_id`, `record_type`, `owner_cop`, `tenant_id`, `agent_scope`,
`validation_status`, `created_at`, `expires_at`, `payload`) beside what retrieval adds: `standing`,
`provenance_status`, `missing_required`, `missing_expected`, `status_problem`, `event_time`, `session_relation`,
`cycle` (CURRENT_CYCLE, PREVIOUS_CYCLE, CYCLE_UNKNOWN), `independent_of_requester`, `may_corroborate`, `use`,
`matching_basis`, `disagrees_with`.

## Questions

| Id | Question | Answers |
|---|---|---|
| CM-1 | What historical information is requested? | REQUEST_COMPLETE, REQUEST_INCOMPLETE |
| CM-2 | What relevant memory is available within the authorized scope? | RELEVANT_MEMORY_FOUND, NO_RELEVANT_MEMORY, NO_MEMORY_IN_SCOPE, ACCESS_DENIED, STORAGE_UNAVAILABLE |
| CM-3 | Is the retrieved memory sufficiently attributable, valid and usable? | ALL_ATTRIBUTABLE, PARTLY_ATTRIBUTABLE, NONE_ATTRIBUTABLE, NOTHING_TO_VALIDATE |
| CM-4 | What information can be returned, and what remains missing or unavailable? | COMPLETE, PARTIAL, NOTHING_RETURNABLE, UNAVAILABLE |

They are purvi's functional responsibilities, not replacement canonical questions. The workbook's five questions
(COMPLETE_DELEGATION_DATA rows 67 to 75) are kept in `previous_contract.questions`, with `question_migration`
saying which part each of CM-1 to CM-4 retains (U-CM1).

## Labels

NO_RELEVANT_MEMORY (quiet), MEMORY_RETURNED, MEMORY_PARTIAL, MEMORY_UNAVAILABLE; INCONCLUSIVE only from the shared
precheck. The previous five labels are retired, never produced and never reused (D-CM5). `INTERPRETATION.md` gives
the mapping.

## Metrics (15, registry consumers = context_memory)

None is read. All 15 stay listed in `METRICS.yaml mapping` with their standing (D-CM9):

| Standing | Metrics |
|---|---|
| computed for other COPs, NO_LONGER_APPLICABLE here | insights.retry, insights.failed, insights.repetition, telemetry.turn_linkage, telemetry.unknown_share, compaction_count |
| NO_LONGER_APPLICABLE and NOT_CALCULATED | session_reset, context_loss_after_compaction, context_consistency, context_relevance_score, context_completeness_score, memory_decision_consistency |
| NOT_CALCULATED pending their definition (U-CM3) | precedent_match, memory_retrieval_success_rate, memory_staleness_rate |

No metric, threshold or score is invented. Relevance is deterministic matching; semantic relevance is UNAVAILABLE
(`unavailable_operations`, U-CM4).
