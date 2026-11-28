# Context & Memory: decisions

Proposed on 2026-11-28 for purvi's review. This package changes Context & Memory's own responsibility at purvi's
explicit instruction (2026-11-28): Context & Memory becomes a dedicated memory retrieval and routing COP. Nothing here
changes another COP's label names, behavioural question, metric_keys, thresholds, consumed edges, ground truth,
verifiers, authority or memory policy.

Numbering: COP 12 in the build order (purvi, 2026-11-28). The workbook and `docs/cops/cop_catalogue.json` number
Context & Memory 4. The build order is recorded apart from that numbering (project memory `cops-build-order`).

## Identity confirmed

| Source | What it says |
|---|---|
| `cops/specs.py` SPECS (before) | key `context_memory`; question "Is the current context internally consistent, relevant to the task, and supported by useful precedent?"; metric_keys none; missing_metrics compaction_count, context_loss_after_compaction, session_reset, precedent_match, context_consistency; labels CONTEXT_OK, STALE, CONTRADICTORY, CONTEXT_LOSS, PRECEDENT_FOUND; permitted_actions NONE, WATCH |
| `specs.SPEC_CONSUMES`, `specs.CONSUMES` | consumes runtime_monitoring; consumed by behavior_analytics (current cycle). No feedback edge |
| `profiles.LLM_COPS` | an LLM COP (prompt: lost constraints, contradictions, breaches after loss) with tools get_context_snapshots, inspect_context_references, compare_instruction_versions, verify_context_provenance, find_similar_validated_cases |
| `rules.context_memory` (before) | the previous rule (METRICS.yaml previous_contract.rule) |
| `requirements.REQUIREMENTS` | context_snapshots REQUIRED for CONTEXT_OK |
| `memory.POLICIES` | writes context_integrity_finding; reads context_integrity_finding; agent scope. Workflow Orchestration and Behavior Analytics also read context_integrity_finding |
| `triage.py` | THRESHOLDS 0.75/0.55/0.30; LABEL_ACTIONS STALE WATCH RECORD, CONTRADICTORY and CONTEXT_LOSS WATCH NOTIFY; NON_REMEDIAL_LABELS PRECEDENT_FOUND; specialist context_provenance |
| Workbook COMPLETE_DELEGATION_DATA rows 67 to 77 | behavioural question row 67 column 2 ("Is the agent operating with accurate, relevant and consistent context?"); five question texts (rows 67, 69, 71, 73, 75); metric list row 77 (15) |
| Workbook agents-harness row 8 | inputs, tools ("MemoryManager, history retrieval, session-state lookup, decision-history lookup"), authority ("Retrieve, summarize, record, flag inconsistency. No policy/action authority simply from memory"), validation ("Check provenance, freshness, relevance and contradictions"), downstream (Runtime, Incident, Policy, Governance Intelligence, Behavior Analytics) |
| Workbook many-to-many mapping row 9, Sheet1 rows 12, 35, 66, 102 | action WATCH; feeds Workflow Orchestration, Behavior Analytics, Recovery & Resilience; "LLM. Examines context snapshots, instruction changes and previous validated records" |
| `metric_registry.json` | 15 metrics with consumer context_memory, matching COPS_METRIC_MAPPING.md (sha256 5bd3c16f...) and row 77 |

## Consumers of the previous contract (inspected)

| Consumer | What it reads | Effect of the retrieval-only scope |
|---|---|---|
| Behavior Analytics, legacy rule (`rules._behavior_analytics_legacy`) | a verified substantive CONTEXT_LOSS or CONTRADICTORY: DRIFTING 0.6 LOW WATCH | that branch can no longer be reached from a real Context & Memory finding. BA still reaches DRIFTING from Workflow Orchestration DRIFTING or UNEXPECTED_BRANCH |
| Behavior Analytics package, BA-4 (`cop_package._ba_upstream`, METRICS.yaml upstream.context_memory altered [CONTEXT_LOSS, CONTRADICTORY], quiet [CONTEXT_OK]) | the same labels | a new label is classed CONTEXT (neither altered nor quiet): BA-4 reads NO_ALTERATION_SHOWN. BA's code and ground truth are unchanged; its harness supplies a synthetic CONTEXT_LOSS upstream and still passes. P-CM1 |
| Workflow Orchestration (`memory.POLICIES` reads context_integrity_finding) | earlier context_integrity_finding records | no new records of that type are written. Existing records stay readable and unchanged |
| Behavior Analytics memory (`tools.t_assess_behaviour_questions` reads context_integrity_finding) | the same | listed as context only (D-BA10); fewer records over time |
| `rules.DETERMINISTIC_TOOLS`, `profiles` prompt, tool allowlist and `ContextDetail` | the previous investigation | replaced by this package's own entries |
| `requirements.REQUIREMENTS` | context_snapshots REQUIRED | removed for this COP (retrieval needs no snapshot) |
| `triage.LABEL_ACTIONS`, `NON_REMEDIAL_LABELS`, specialist | previous labels | replaced by this package's labels; all non-remedial |
| `evidence_query`, `live_eval`, `signal_pages_export` | previous evidence and cases | this COP's own entries updated |
| Evaluation C09, D-CM, E2E, E2E-full (`runtime/evaluation.py`) | CONTEXT_LOSS, PRECEDENT_FOUND, CONTEXT_OK withheld for context_snapshots, a verified substantive reading | the expectations about this COP are migrated (D-CM19); the previous expectations are kept verbatim below |

No accepted COP's ground truth expects a real Context & Memory finding: Behavior Analytics' harness and
`tests/test_runtime_graph.py`, `tests/test_cop_influence.py` hand Behavior Analytics a synthetic CONTEXT_LOSS
finding, which still exercises BA's contract for that input.

## Made in this package

| # | Decision | Alternatives | Why |
|---|---|---|---|
| D-CM1 | Responsibility: retrieve and organize previously recorded COP memory (findings and decisions, historical session evidence, validated precedents, incidents and recovery outcomes, recorded context) for a requesting COP, with provenance intact. It detects nothing: not context loss, contradiction, drift, anomaly, violation, incident or risk | keep detection beside retrieval | purvi, 2026-11-28, items 1 and 12: no overlapping responsibilities |
| D-CM2 | The previous contract (question, labels, rule, tools, requirement, memory type, evaluation expectations) is kept for comparison: METRICS.yaml previous_contract, `rules._context_memory_previous_contract` (not called), and the tables in this file | delete it | purvi item 2: preserve the original contract in the decision history |
| D-CM3 | specs.behavioural_question becomes purvi's responsibility text: "What previously recorded information is relevant to the requesting COP's current question, and can that information be retrieved reliably with its provenance intact?" The workbook's questions are kept as written (METRICS.yaml previous_contract.questions); question_migration says which part of each is retained | keep the old wording | the old wording asks for a judgement this COP no longer makes. U-CM1 asks purvi to confirm the workbook should be updated |
| D-CM4 | Four questions CM-1 to CM-4 from purvi's item 4 functional responsibilities. They read memory records and the request, not metric events, so `requires` is empty and their observation is this package's own (no `evidence_assessment.assess`) | metric requirements | no metric measures retrieval (D-CM9) |
| D-CM5 | Labels: NO_RELEVANT_MEMORY (quiet), MEMORY_RETURNED, MEMORY_PARTIAL, MEMORY_UNAVAILABLE; INCONCLUSIVE only from the shared precheck. The previous five labels are retired: never produced and never reused with another meaning | map new outcomes onto CONTEXT_OK or PRECEDENT_FOUND | purvi item 4: do not reuse labels that imply detection; PRECEDENT_FOUND would also imply a judgement that a precedent applies |
| D-CM6 | Compatibility: no adapter. A consumer expecting CONTEXT_LOSS or CONTRADICTORY receives a retrieval label and reads no alteration. An adapter would need this COP to emit a detection label it no longer has grounds for, or to relabel a historical record as a current finding; both fabricate provenance | translate retrieved context_integrity_finding records into CONTEXT_LOSS | purvi item 6: implement an adapter only if it preserves truthful provenance. Required migration: P-CM1 |
| D-CM7 | The retrieval workflow, in order: request (CM-1) -> scope determination -> memory lookup (MemoryGateway.read) -> access validation (the gateway's tenant and type rules) -> provenance validation (CM-3) -> relevance filtering (CM-2) -> structured response (the bundle, CM-4) -> consumption record (one cop_memory_access_log row). Each stage records its inputs, outputs and failure in `bundle.stages` | a model-led search | purvi item 3 |
| D-CM8 | Authority: none. Action NONE for every label; severity NONE; nothing is enforced, paused or instructed | WATCH | there is no governance finding to watch; WATCH stays permitted in specs so the shared fallback is unchanged |
| D-CM9 | All 15 registry metrics stay listed (METRICS.yaml mapping). None is read: 5 category E metrics and compaction_count are computed for other COPs and NO_LONGER_APPLICABLE here; 5 category M and session_reset are NO_LONGER_APPLICABLE and NOT_CALCULATED; precedent_match, memory_retrieval_success_rate and memory_staleness_rate are NOT_CALCULATED pending their registry questions (U-CM3); memory_decision_consistency is NO_LONGER_APPLICABLE | read compaction_count | no metric or threshold is invented |
| D-CM10 | Every operation is deterministic. No approved semantic-search capability or retrieval contract exists, so semantic relevance is UNAVAILABLE (METRICS.yaml unavailable_operations), not approximated. The COP stays in profiles.LLM_COPS (the fixed allocation) and is decided without a model in every reading, as Risk Mitigation and Classification & Tagging are | move it to DETERMINISTIC_COPS | changing the allocation is a shared change; U-CM5 |
| D-CM11 | Standing, from the record's own status and provenance only: ESTABLISHED (VALIDATED, independently reviewed), VERIFIED_NOT_VALIDATED, UNVERIFIED_OBSERVATION, INSUFFICIENTLY_SUPPORTED. The original validation_status is always returned unchanged beside it | a combined score | purvi item 3: preserve each record's original status |
| D-CM12 | Authorization is the requesting COP's own memory.POLICIES (reads and scope). Retrieval runs MemoryGateway.read as the requester, so tenant isolation, type entitlement, agent scope, expiry and rejection are the gateway's existing rules. A type outside the requester's reads is NOT_ENTITLED and never asked of the gateway. The authorized tenant comes from the runtime's case; a different requested tenant is passed to the gateway, which denies and logs it | give Context & Memory read access to every type | purvi item 3: use the existing authorization rules; Context & Memory's own POLICIES entry is unchanged |
| D-CM13 | Provenance: METRICS.yaml provenance.required, expected and status_support. A record missing a required field, or VALIDATED without independent feedback, or VERIFIED with provenance.verified false, is INSUFFICIENTLY_SUPPORTED and returned apart. Missing expected fields are listed per record. Nothing missing is filled in | drop such records | purvi item 3: mark them rather than present them as verified; dropping would hide them |
| D-CM14 | Duplicates: METRICS.yaml event_identity_keys. One record per event is returned (highest standing, then earliest); the others are DUPLICATE_OF it, each with its own status. Records without an event identity are never merged: two cases reading the same session are two decisions, not one event (triage counts repeated rejected observations by case) | merge by session and label | merging decisions would hide repeated decisions |
| D-CM15 | Disagreement: returned records of one type about one session with different payload labels are listed together, unresolved. Nothing is preferred and nothing is labelled CONTRADICTORY | resolve by recency or standing | purvi item 8: return contradictory records with their original status |
| D-CM16 | Runtime Monitoring stays a consumed edge (no topology change) and is context only. The shared precheck still withholds the quiet NO_RELEVANT_MEMORY while Runtime Monitoring verifies telemetry INSUFFICIENT: current-cycle records may be missing too | drop the edge | an edge change ripples through the topology; the precheck is shared |
| D-CM17 | Time: event time is provenance.evaluated_at (the producing case's evaluation time); a record with an event after the request's as_of is FUTURE_EVENT and excluded; since and until bound it. created_at is the gateway's recording time and is returned, not compared with as_of (they come from different clocks). A record is CURRENT_CYCLE when written in the request's run and case, else PREVIOUS_CYCLE; CYCLE_UNKNOWN without run_id | compare created_at | in the runtime the gateway clock and the case's evaluation time are independent; comparing them would exclude every record |
| D-CM18 | Routing: every COP's memory reads in the runtime go through this contract: graph.retrieve_memory (the working memory view) and tools._memory_lookup (the package tools). The view keeps its shape (established, verified_pending, unverified, excluded) and adds the bundle; records set apart (insufficient provenance, instruction-shaped, future, duplicate) are no longer in the buckets. On a gateway denial both behave as before (unavailable view; MemoryAccessDenied from the tool) | a separate opt-in call | purvi item 6: other COPs request memory through the existing retrieval contract and receive a structured bundle |
| D-CM19 | The evaluation's expectations about this COP are migrated: C09 and D-CM now expect no retired label, no fabricated integrity finding for Behavior Analytics and a retrieval bundle; E2E no longer expects a withheld CONTEXT_OK for context_snapshots (it expects no context_snapshots dependency and no retired label); E2E-full keeps "verified substantive". The bench cases context_memory:normal, :abnormal and :missing (`runtime/live_eval.py` CASES) become retrieval worlds: an empty store (NO_RELEVANT_MEMORY), a validated record beside a forged VALIDATED one (MEMORY_PARTIAL), thin telemetry with an empty store (INCONCLUSIVE). `docs/cops/cops_topology.json` carries this COP's new question, labels and an illustrative response, and its consumed edge (the Runtime Monitoring link added and `reads_decisions_from` corrected from [] to [runtime_monitoring], as Behavior Analytics did for its own entry); `docs/cops/cop_catalogue.json` is a dated read-only extraction and is left as it was. The previous expectations are kept below | leave them failing | they test the retired detector, not another COP's contract |
| D-CM20 | This COP writes no memory (`cop_specific.memory_write_policy: none`, read by graph.validate_memory_update). Its POLICIES entry keeps context_integrity_finding so earlier records stay readable and owned | write each bundle | purvi item 7: no competing historical truth and no duplicate entries |

## Previous evaluation expectations (kept verbatim)

| Scenario | Expectation |
|---|---|
| C09 | Context & Memory CONTEXT_LOSS, verified |
| C09 | names C1 as lost |
| C09 | Behavior Analytics consumed the integrity finding |
| C09 | verified finding stored as VERIFIED memory |
| D-CM | first: incomplete evidence, tool requests, revision, CONTEXT_LOSS verified |
| D-CM | first: stored as VERIFIED |
| D-CM | second: retrieves and cites the validated record, verified |
| E2E | Context & Memory withholds CONTEXT_OK: no context snapshots |
| E2E-full | the five COPs gated in E2E reach a verified substantive finding (kept) |
| bench context_memory:normal | compaction keeps every constraint: CONTEXT_OK |
| bench context_memory:abnormal | compaction drops C1, then a migration file is written: CONTEXT_LOSS |
| bench context_memory:missing | compaction, no context snapshots: INCONCLUSIVE |

## Proposed for approval (not implemented)

| # | Proposal | Why it is not implemented |
|---|---|---|
| P-CM1 | Behavior Analytics BA-4 and its legacy rule read context alteration from Context & Memory. Migrate them to read it from the COP that will own context-loss detection (U-CM2), or retire that input. Until then BA reaches DRIFTING only through Workflow Orchestration | changes an accepted COP's consumed contract |
| P-CM2 | Wire the workbook's further recipients as explicit requests (agents-harness row 8: Runtime, Incident, Policy, Governance Intelligence; many-to-many row 9: Workflow Orchestration, Recovery & Resilience). Every COP already reads its memory through this contract (D-CM18); no current-cycle edge is needed and none is added | adding edges changes the topology |

## Unresolved (for purvi)

| # | Question | Today | Options |
|---|---|---|---|
| U-CM1 | The workbook still asks the previous questions (rows 67 to 75) | kept as written; CM-1 to CM-4 implement item 4; question_migration maps them | update the workbook; keep both |
| U-CM2 | Context-loss and contradiction detection now has no owner | not detected by any COP; compaction_count still computed | assign to Workflow Orchestration; a new COP; retire |
| U-CM3 | precedent_match, memory_retrieval_success_rate, memory_staleness_rate: the registry asks whether agent memory or COPS memory is meant | NOT_CALCULATED; access-log rows exist to compute the COPS-memory rate | define them over cop_memory_access_log |
| U-CM4 | Semantic retrieval | UNAVAILABLE | approve a semantic-search capability and contract |
| U-CM5 | Context & Memory stays in profiles.LLM_COPS but never calls a model | decided without a model | move it to DETERMINISTIC_COPS |
| U-CM6 | Confidence 0.7 is the previous CONTEXT_OK constant | kept, uncalibrated | calibrate; fix at 1.0 for deterministic retrieval |
| U-CM7 | The required and expected provenance fields (D-CM13) and event identity keys (D-CM14) are this package's | as METRICS.yaml | approve or amend |
| U-CM8 | No retrieval limit is approved | nothing truncated unless the request names max_results (NO_APPROVED_LIMIT) | approve a default limit |
| U-CM9 | A COP's own validated record counts as precedent in the shared VS-01 memory item (`support._memory`) and triage | unchanged; this package marks it OWN_EARLIER_OUTPUT, may_corroborate false | have VS-01 read may_corroborate |

## Conditional acceptance and decisions (purvi, 2026-11-28)

A-CM1: Context & Memory is conditionally accepted; the retrieval-only implementation is approved. Ground truth for
the changes below was written first (GROUND_TRUTH.yaml: the scenarios added at the end and `superseded:`).

| # | Decision | Implementation |
|---|---|---|
| P-CM1 | Approved: remove Behavior Analytics' obsolete dependency on the retired labels | the Context & Memory → Behavior Analytics edge is removed from `specs` (it served only the retired detector). Context & Memory has no current-cycle consumer. In a case it serves one bundle to every other COP of the case that has a memory policy (D-CM21). Behavior Analytics reads memory through the shared retrieval interface and never needs a Context & Memory label (packages/behavior_analytics/DECISIONS.md A-BA1) |
| U-CM1 | The retrieval-only question and the label migration are approved | as built |
| U-CM2 | Context-loss detection stays unassigned; no overlapping role is introduced | no COP detects it; compaction_count is still computed |
| U-CM3 | Any new retrieval metric must be derived from actual audit records (`cop_memory_access_log`), not estimates | none added; precedent_match, memory_retrieval_success_rate and memory_staleness_rate stay NOT_CALCULATED |
| U-CM4 | Semantic search stays deferred | UNAVAILABLE |
| U-CM5 | Deterministic classification approved, provided profile and runtime compatibility tests pass | moved from `profiles.LLM_COPS` to `DETERMINISTIC_COPS`; its prompt, LLM tool list and ContextDetail are retired |
| U-CM6 | Confidence stays uncalibrated | 0.7, UNCALIBRATED_CONFIDENCE |
| U-CM7 | The provenance and event-identity rules are approved provisionally | as METRICS.yaml |
| U-CM8 | The default retrieval limit stays configurable and unresolved | NO_APPROVED_LIMIT |
| U-CM9 | A COP's own historical outputs never independently corroborate its present finding in VS-01 | `support._memory` counts only established records owned by another COP; the memory view carries `owner_cop` and `may_corroborate`. A record of the COP's own is neutral, named as its own earlier output. Triage's confidence basis is unchanged (outside VS-01) |

| # | Decision | Alternatives | Why |
|---|---|---|---|
| D-CM21 | With no consumer edge, Context & Memory's case requests are one per other COP of the case run that has a memory policy (`graph._tool_ctx` passes the case order). Run on its own it has no request and is INCONCLUSIVE (no COP requested memory) | no requests in the case graph; every COP with a policy | the bundles describe what this case's COPs can retrieve, without an edge that would make any COP depend on this COP's label |
| D-CM22 | A requester is never served its own output of the same run and case (OWN_CURRENT_OUTPUT, excluded; it does not make a request PARTIAL). Another COP's current-cycle record is still returned, marked CURRENT_CYCLE | return it marked CURRENT_CYCLE | with D-CM21 a COP that ran earlier in the case would be handed back what it just wrote; it already holds it and it can never corroborate itself. Ground truth own_current_output was written before the code |
