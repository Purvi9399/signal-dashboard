# COPS inconclusive and incorrect cases: root causes and fixes

Branch `feat/cops-langgraph-runtime` (PR #4). Generated 2026-11-03. **No new model calls were made for this work.** Every result below comes from a deterministic re-run or from a replay of model replies recorded on 2026-11-02. Replays are labelled `recorded_reply_replay` and are never counted as new live calls.

## 1. Summary

| | Before the fixes | After the fixes (no new calls) |
|---|---|---|
| Test-bench cases | 54 | 54 |
| PASS / FAIL / INCONCLUSIVE | 44 / 2 / 8 | **48 / 1 / 5** |
| Findings verified | 29 | 32 |
| Ground truth matched | 44 | 48 |
| Expected inconclusives forced only by a verifier | 6 | 3 |
| Fleet F1 / F2 | PASS / FAIL | PASS / FAIL (needs a live run, see §7) |
| Authority respected | 54 / 54 | 54 / 54 |

Four cases were fixed at their source without a new call:

- `risk_mitigation:normal` now reads LOW.
- `governance_intelligence:normal` now reads NO_PATTERN.
- `governance_intelligence:abnormal` now reads RECURRING.
- `audit_traceability:abnormal` now reads BROKEN_TRACE. **This one is a semantic change and needs agreement** (§8).

The other six cases now have a precise, recorded cause, and the evidence or tool output behind each has been fixed. The recorded model reply still carries the original fault, though, and a replay cannot produce a new reply. A live re-run is needed for those (§7).

Originals are preserved for comparison:

- [`evaluation/test_bench_results_before_fixes.json`](evaluation/test_bench_results_before_fixes.json)
- [`evaluation/COPS_TEST_BENCH_before_fixes.html`](evaluation/COPS_TEST_BENCH_before_fixes.html)

Expectations (`expected_det`, `expected_llm`) were not changed. A test enforces this (`test_expectations_are_unchanged_and_independent_of_outputs`).

## 2. Root-cause table

Root-cause codes can co-occur:

- `EXPECTED_INCONCLUSIVE`
- `MISSING_EVIDENCE`
- `MISSING_DEPENDENCY`
- `VERIFIER_REJECTED`
- `MODEL_UNCERTAIN`
- `EVALUATION_MISMATCH`

They are defined in `collector/cops/runtime/diagnosis.py` (`CODES`). The decision status, the root causes and the evaluation verdict are kept as separate fields (`diagnose()`).

| Case | Before | After | Root causes now | Responsible check and exact fault | Fix at the source | Still needed |
|---|---|---|---|---|---|---|
| workflow_orchestration:abnormal | INCONCLUSIVE | INCONCLUSIVE | VERIFIER_REJECTED, EVALUATION_MISMATCH | SC-01 `VALUE_MISMATCH` on claim 5. It cites `analyse_execution_transitions.patterns.0.longest_run.length`, asserting 4, but 5 is observed. The number 4 is right (5 identical TEST steps make 4 self-transitions); the field cited counts steps. | `tools.analyse_execution_transitions` now returns `longest_run.steps` and `longest_run.self_transitions` beside `length`, and the WO prompt says what each counts. SC-01 is unchanged and still rejects the recorded claim. | Live re-assessment (recoverable) |
| classification_tagging:normal | INCONCLUSIVE | INCONCLUSIVE | MODEL_UNCERTAIN, EVALUATION_MISMATCH | No verifier failure. `inspect_event_metadata` returned only rows with an indicator, so on a clean session it returned `[]`. The model read that as "metadata unavailable". | The tool now reports `scanned_rows`, `metadata_available`, `selection` and `indicator_fields_checked`. The prompt defines UNCLASSIFIED as "examined, no defining indicator". | Fresh call, to show the clarified evidence changes the reading |
| risk_mitigation:normal | INCONCLUSIVE | **PASS (LOW)** | (none) | SC-01 rejected a claim that stated exactly that Resource Management was INCONCLUSIVE and "not evidence". That claim is a disclosure, not support. | SC-01 (`verifiers.py`) accepts a claim that states an unverified upstream's INCONCLUSIVE label exactly, and counts it as **no support**. A claim relying on an unverified *substantive* upstream label still fails (`UPSTREAM_UNVERIFIED`), and a misstated label still fails (`UPSTREAM_MISMATCH`). | None |
| governance_intelligence:normal | INCONCLUSIVE | **PASS (NO_PATTERN)** | (none) | Same as Risk Mitigation, with Recovery & Resilience as the upstream. | Same SC-01 disclosure rule. | None |
| governance_intelligence:abnormal | INCONCLUSIVE | **PASS (RECURRING)** | (none) | Same as above. | Same SC-01 disclosure rule. | None |
| workload_optimization:abnormal | INCONCLUSIVE | INCONCLUSIVE | VERIFIER_REJECTED, EVALUATION_MISMATCH | There were two faults. (1) SC-01 `CITATION_MALFORMED`: claim 6 is `metric_value` with no `metric_id`, and `metric:insights.token_outlier` appears only in `evidence_refs`. (2) The rule baseline read EFFICIENT: `insights.repetition` counts a repeat only when the whole argument payload matches, and these repeats differ in an incidental field. | (1) SC-01 names the malformed citation exactly and does **not** fill the metric in from `evidence_refs`. (2) `inspect_repeated_operations` adds `unchanged_repeat_reads` (6) and `unchanged_repeat_commands` (3), and `rules.workload_optimization` reads REDUNDANT when that count reaches an attention state on `insights.repetition`'s own thresholds (§8). | Live re-assessment (recoverable) |
| audit_traceability:abnormal | FAIL (PARTIAL) | **PASS (BROKEN_TRACE)** | (none) | Rule mapping. `insights.unattributed` = 3 is WARNING, and WARNING mapped to PARTIAL. Three disk changes that no step accounts for break attribution. | `reconstruct_trace` reports `disk_changes` and `disk_changes_attributed`. `rules.audit_traceability` turns PARTIAL into BROKEN_TRACE when unattributed disk changes are confirmed. UNATTRIBUTABLE stays reserved for CRITICAL. | **Agreement on semantics** (§8) |
| knowledge_policy_management:abnormal | INCONCLUSIVE | INCONCLUSIVE | VERIFIER_REJECTED, EVALUATION_MISMATCH | DS-03 `baseline_contradicts_quiet`. The rules read CONFLICT via `compare_policy_documents`; the model never called that tool, never saw its result, and proposed CURRENT. DS-03 was right. | The payload now carries `baseline_tool_results` (results only; the baseline *label* stays hidden, so DS-03 stays independent). The prompt requires a conflict check before CURRENT, and a DS-03 rejection is recoverable once. **DS-03 is unchanged.** | Fresh call |
| governance_architecture:normal | FAIL (GAP) | FAIL (GAP) | EVALUATION_MISMATCH | No verifier failure. The model judged *estate* coverage (`insights.no_oversight` and `insights.lockfile` have no COP consumer). The case expects *case* coverage. Both facts are true; they answer different questions. | `query_coverage_matrix` reports five levels per item: observed_in_case, metric_reader, cop_consumer, executable_condition, and tested (= `unknown`, never assumed). It separates `case_level_gaps` from `estate_level_gaps`. ADEQUATE is not forced by the existence of a metric reader. | **Decision** (§8), then a fresh call |
| governance_architecture:abnormal | INCONCLUSIVE | INCONCLUSIVE | VERIFIER_REJECTED, EVALUATION_MISMATCH | SC-01 `CITATION_MALFORMED`: claim 8 (`metric_state`) has no `metric_id`, no `asserted_state` and no reference, so it cites nothing. The other claims were correct. | SC-01 names the uncited claim. The claim is not dropped or rewritten. | Live re-assessment (recoverable) |
| fleet F2 (risk_mitigation) | FAIL | FAIL | VERIFIER_REJECTED | SC-01: a `metric_state` claim with no `metric_id` or `asserted_state`; `metric:insights.secrets` appears only in `evidence_refs`. EMERGING matches the rule baseline. Policy Enforcement (VIOLATION) and Incident Response (INCIDENT) propagated correctly. | Same citation guidance and recovery path as Workload Optimization. | The fleet run predates reply recording, so it **cannot be replayed**. Needs a live run. |

Every non-passing case's dashboard trace shows, for each failed check:

- check
- claim
- field
- asserted
- observed
- code
- recoverable

Trace payloads come from `test_bench.py` (`_calls_with_results`, `diagnose`).

## 3. Code changes

All changes are under `collector/cops/runtime/`, plus tests. The diff is 13 changed files (+903/−125) and 2 new files (`diagnosis.py`, `tests/test_runtime_root_causes.py`).

- **Structured verifier failures.**
  - `contracts.VerifierResult.failures` is new.
  - `verifiers.py` SC-01 records claim, statement, kind, field, asserted, observed, code, recoverable and message for each failure.
  - Codes:
    - `CITATION_MALFORMED`, `VALUE_MISMATCH` and `UPSTREAM_MISMATCH` are recoverable.
    - `CITED_SOURCE_UNAVAILABLE`, `UPSTREAM_UNVERIFIED` and `PRECEDENT_UNAVAILABLE` are not.
  - An exact upstream INCONCLUSIVE disclosure is neither a failure nor support.
- **Evidence-sufficiency precheck** (`requirements.evidence_profile`, `requirements.precheck`; graph node `evidence_precheck`, `route_after_precheck`).
  - Six states: AVAILABLE, RETRIEVED_AND_VALIDATED, MISSING_BUT_RETRIEVABLE, MISSING_AND_UNAVAILABLE, NOT_APPLICABLE and PRESENT_BUT_UNVERIFIED.
  - Classification without a taxonomy stops before any model call.
  - A deterministic quiet label under verified INSUFFICIENT telemetry is withheld by the COP itself (`graph._gate_required`, reason code `MISSING_EVIDENCE`) instead of being caught later by a verifier.
- **Bounded recovery** (`graph.plan_recovery`, `route_after_verify`, `_recovery_decision`).
  - At most one re-assessment (`llm` purpose `reassess`). It is given the verifier's structured feedback and the same tool results, and its output is **verified again by the same verifiers**.
  - It is attempted only when every blocking failure is recoverable.
  - It is never attempted in a replay (`FixtureProvider(replay=True)`) or when the call budget is spent.
  - If the re-assessment gives no candidate, the first rejection stands unchanged.
  - A recovered action still goes through authority.
- **Tool output clarified** (`tools.py`):
  - `longest_run.steps` and `self_transitions`;
  - event-metadata scan fields;
  - unchanged-repeat counts;
  - trace disk-change attribution;
  - coverage levels and case/estate gaps.
- **Rules** (`rules.py`):
  - `_state_of`;
  - WLO REDUNDANT from unchanged repeats;
  - AT PARTIAL becomes BROKEN_TRACE when effects are unattributed.
- **Prompts** (`profiles.py`, now version `prompt-2`):
  - citation fields;
  - `evidence_sufficiency`;
  - `baseline_tool_results`;
  - re-assessment;
  - per-COP notes for WO, CT, KPM and GA.
- **Diagnosis** (`diagnosis.py`, new):
  - `diagnose(rec)` keeps decision status, root causes and evaluation verdict apart.
  - `ENGINEERING_NOTES` holds the cause, fix and remaining work per case.
- **Bench and dashboard** (`test_bench.py`, `bench_report.py`):
  - result source per case;
  - original vs current;
  - diagnosis;
  - evidence journey;
  - verifier table;
  - sufficiency table;
  - filters.
- **Live eval** (`live_eval.py`): a precheck stop is a valid record with no call.

## 4. Evidence sufficiency for all 18 COPs

This is the static profile from `requirements.evidence_profile`. "Quiet label" is the COP's no-concern reading; the gate column says whether missing evidence blocks only that label or every label. Each metric's NOT_APPLICABLE supports only "the condition did not arise", and UNKNOWN or MISSING support nothing. Upstream roles:

- `bounds_quiet_label`: a verified INSUFFICIENT reading forbids the quiet label (DS-01).
- `context`: used only when verified, and never counted as support (SC-01).

| COP | Quiet label | Metrics | Required evidence (gate; N/A acceptable; retrieved by) | Optional | Upstream (role) | Missing metrics (not computed today) |
|---|---|---|---|---|---|---|
| workflow_orchestration | NORMAL | 4 | task_plan (quiet label; via get_task_milestones) | — | runtime_monitoring (bounds_quiet_label) | task_progress, intended_state, task_drift, unresolved_items, stalled_duration_per_turn, excessive_delegation_ratio |
| runtime_monitoring | SUFFICIENT | 5 | — | — | — | collector_heartbeat, event_freshness_live, missing_event_rate, collector_errors |
| anomaly_detection | NORMAL | 7 | — | — | runtime_monitoring (bounds_quiet_label); behavior_analytics (context) | tool_use_deviation_per_agent, resource_access_novelty_files, token_expansion_per_turn |
| context_memory | CONTEXT_OK | 0 | context_snapshots (quiet label; via get_context_snapshots, compare_instruction_versions, inspect_context_references, verify_context_provenance) | — | runtime_monitoring (bounds_quiet_label) | compaction_count, context_loss_after_compaction, session_reset, precedent_match, context_consistency |
| interaction_discovery | CONTAINED | 5 | — | — | runtime_monitoring (bounds_quiet_label) | edge_table, new_edges, fan_in, fan_out, max_delegation_depth, cross_agent_path_general_to_coding |
| incident_response | NO_INCIDENT | 3 | — | policy_store | runtime_monitoring (bounds_quiet_label); anomaly_detection, policy_enforcement, interaction_discovery, workflow_orchestration, recovery_resilience (context) | time_to_harm, intervention_available, affected_entities |
| classification_tagging | UNCLASSIFIED | 4 | taxonomy (**all labels**; via query_taxonomy) | — | anomaly_detection, agent_lifecycle_management (context) | actor_type, resource_type, incident_family, classification_confidence |
| policy_enforcement | ALLOWED | 5 | policy_store (quiet label; via evaluate_session_operations); entitlements (quiet label, N/A ok; via check_entitlements) | — | runtime_monitoring (bounds_quiet_label); interaction_discovery, agent_lifecycle_management, anomaly_detection (context) | entitlement_mismatch, indirect_privilege_use, policy_precedence, missing_required_approval |
| risk_mitigation | LOW | 5 | risk_register (quiet label; via read_risk_register, evaluate_control_prerequisites) | — | anomaly_detection, interaction_discovery, policy_enforcement, resource_management, incident_response (context) | compound_risk_score, reversibility, residual_risk |
| resource_management | NORMAL | 4 | budget_or_quota (quiet label; via get_quota) | — | runtime_monitoring (bounds_quiet_label) | budget_or_quota, cpu_gpu_memory, rate_limit_hits |
| governance_intelligence | NO_PATTERN | 0 | decision_store (quiet label; via query_aggregate_report, compare_time_windows) | — | classification_tagging, recovery_resilience, incident_response (context) | metric_state_trends_from_metric_events, decision_trends_from_cop_decisions, repeat_actor_concentration |
| behavior_analytics | STABLE | 5 | — | — | runtime_monitoring (bounds_quiet_label); context_memory, workflow_orchestration (context) | loop_score_per_lane, exploration_rate, planning_deviation, recovery_pattern |
| recovery_resilience | RECOVERED | 3 | recovery_records (quiet label, N/A ok; via get_recovery_records, validate_checkpoints) | — | behavior_analytics (context) | recovery_success_after_failure, irreversible_change, recovery_time |
| workload_optimization | EFFICIENT | 4 | task_classes (quiet label; via compare_task_classes) | — | runtime_monitoring (bounds_quiet_label); resource_management (context) | cost_per_task, cache_efficiency, redundant_reads |
| audit_traceability | COMPLETE | 4 | — | — | runtime_monitoring (bounds_quiet_label); interaction_discovery, policy_enforcement (context) | decision_evidence_coverage, raw_ref_resolvability |
| knowledge_policy_management | CURRENT | 0 | policy_store (quiet label; via get_policy_versions, compare_policy_documents, inspect_rule_dependencies, check_authorisation_records) | — | governance_intelligence, policy_enforcement (context) | policy_store, policy_version, rule_conflicts, rule_hit_rate, override_rate |
| agent_lifecycle_management | UNCHANGED | 1 | agent_registry (quiet label; via get_agent_registry) | — | runtime_monitoring (bounds_quiet_label); interaction_discovery (context) | tool_version_change, model_change, new_machine, first_last_seen, hook_config_removed |
| governance_architecture | ADEQUATE | 1 | — | — | governance_intelligence, knowledge_policy_management, audit_traceability (context) | observable_field_status_per_tool, cops_coverage, unhandled_exceptions, repeated_escalations |

What resolves uncertainty for each artifact is in `requirements.RESOLVES`, and it is shown per case in the dashboard's sufficiency table. Each row is labelled with the state it was in when the decision was made.

## 5. The six missing-evidence cases that passed only because a verifier intervened

| Case | Before | Now | Why |
|---|---|---|---|
| behavior_analytics:missing | verifier forced INCONCLUSIVE | **held back by the COP** | `_gate_required` withholds STABLE: Runtime Monitoring reads the telemetry INSUFFICIENT (reason code MISSING_EVIDENCE). |
| interaction_discovery:missing | verifier | **COP** | Same; CONTAINED withheld. |
| audit_traceability:missing | verifier | **COP** | Same; COMPLETE withheld. |
| workload_optimization:missing | verifier (DS-01) | verifier (DS-01) | The model proposed EFFICIENT without `task_classes`. DS-01 is the required-evidence check, and it acts after the model call because the precheck permits a non-quiet label. This is correct behaviour. |
| incident_response:missing | verifier (SC-01) | verifier (SC-01) | The model cited a tool call that returned MISSING_DEPENDENCY. **Open point:** the rule baseline reads WATCH (not a quiet label), so the precheck does not hold it back. The INCONCLUSIVE depends on the model citing a failed call. |
| governance_architecture:missing | verifier (SC-01) | verifier (SC-01) | GAP rejected for an uncited `metric_state` claim. **Open point:** GA has no required artifact and does not consume Runtime Monitoring, and its rule baseline reads ADEQUATE. A correctly cited GAP, or the deterministic ADEQUATE, would not be INCONCLUSIVE. Whether GA should be bounded by telemetry sufficiency is a specification question; it is not invented here. |

## 6. Action lifecycle

All effects are still dry-run (`actions.DryRunAdapter`); nothing was notified, paused or blocked. The stage counts changed only because the decisions changed:

| Stage | Before | After | Cases that moved |
|---|---|---|---|
| NO_ACTION_PROPOSED | 18 | 19 | governance_intelligence:normal (NO_PATTERN proposes nothing) |
| AUTHORISED_DRY_RUN | 20 | 22 | risk_mitigation:normal (LOW), governance_intelligence:abnormal (RECURRING) |
| PROPOSAL_WITHDRAWN | 15 | 12 | the three cases above previously withdrew their proposal on INCONCLUSIVE |
| REJECTED_BY_AUTHORITY | 1 | 1 | unchanged |

`audit_traceability:abnormal` was AUTHORISED_DRY_RUN before and after; only its label changed. A test checks that every final action stays within authority and that nothing executes (`test_every_final_action_stays_within_authority_and_nothing_executes`).

## 7. What still needs a live run (proposal; not run)

Replay cannot establish these, because the recorded replies carry the original faults and there is no recorded reply for a re-assessment:

| # | Case | What the live run would show | Calls (est.) |
|---|---|---|---|
| 1 | workflow_orchestration:abnormal | It cites `self_transitions`, or recovers after SC-01 feedback | 2–3 |
| 2 | classification_tagging:normal | The clarified scan fields lead to UNCLASSIFIED | 2–3 |
| 3 | workload_optimization:abnormal | It cites `metric_id`; REDUNDANT is verified | 2–3 |
| 4 | knowledge_policy_management:abnormal | With baseline tool results, it reads CONFLICT or explains it; DS-03 is unchanged | 2–3 |
| 5 | governance_architecture:abnormal | The uncited claim is corrected on re-assessment | 2–3 |
| 6 | governance_architecture:normal | ADEQUATE on case-level coverage (**after the §8 decision**) | 2–3 |
| 7 | fleet F2 | Risk Mitigation cites `metric_id`; the fleet passes | ~4 LLM COPs + up to 4 re-assessments |

Estimate:

- **12–18 case calls plus 4–8 fleet calls, about $0.35–0.65.**
- This is based on the measured 2026-11-02 rate of about $0.021 per call (`COPS_COST_ANALYSIS.md`), plus a margin for the larger re-assessment payload.
- I propose a hard cap of **$0.75** via `COPS_LIVE_MAX_TOTAL_USD`.
- Spent so far: about $1.89 of $5. Remaining: about $3.11.
- Optionally, re-running the three disclosure-fixed cases live (RiskM normal, GI normal and abnormal) would confirm the fix on fresh replies. That adds about 6 calls, about $0.13.

New live results would be labelled `live_new_call`. The 2026-11-02 results stay as `live_recorded` beside them.

## 8. Decisions that needed agreement (items 1–3 decided 2026-11-04, see §11)

1. **Audit Traceability:** should PARTIAL become BROKEN_TRACE when the trace confirms disk changes that no step accounts for? Recommended: yes. Unattributed effects are a break in attribution, and UNATTRIBUTABLE stays CRITICAL-only. If not, revert `rules.audit_traceability`, and the case returns to FAIL.
2. **Governance Architecture:** should a GAP for one case require a case-level gap? Recommended: yes, with estate-level gaps (computed metrics with no COP consumer) reported as latent findings instead of a case GAP. If GA should judge the estate, the scenario expectation would change, and that is not done here.
3. **Upstream disclosure in SC-01:** a claim stating an unverified upstream's INCONCLUSIVE label exactly counts as no support and is not a failure.
4. **WLO threshold reuse:** unchanged repeats are judged on `insights.repetition`'s existing thresholds (warning 1, exception 3). No new threshold was invented, but reusing it for a differently counted quantity should be agreed.
5. **IR and GA on missing evidence** (§5): should these COPs be bounded by telemetry sufficiency?

## 9. Tests and exact outputs

Full suite, from the repository root:

```
$ python3 -m pytest tests/ -q
322 passed, 1 skipped in 22.05s
```

(It was 289 passed and 1 skipped before; the 33 new tests are in `tests/test_runtime_root_causes.py`.)

```
$ python3 -m pytest tests/test_runtime_root_causes.py -v
============================== 33 passed in 4.27s ==============================
```

New tests:

- **SC-01:**
  - `test_sc01_upstream_disclosure_of_an_inconclusive_finding_is_not_a_failure_and_not_support`
  - `test_sc01_still_rejects_an_unverified_substantive_upstream_used_as_support`
  - `test_sc01_rejects_a_disclosure_that_misstates_the_upstream_label`
  - `test_sc01_metric_named_only_in_evidence_refs_is_a_malformed_citation_and_is_not_filled_in`
  - `test_sc01_uncited_metric_state_claim_names_exactly_what_is_missing`
  - `test_wo_steps_versus_self_transitions_are_separate_fields_and_the_mismatch_is_recorded_exactly`
- **Evidence sufficiency:**
  - `test_every_cop_has_an_evidence_profile_with_upstream_roles`
  - `test_precheck_keeps_missing_not_applicable_unverified_and_retrieved_apart`
  - `test_classification_without_a_taxonomy_stops_before_any_model_call`
  - `test_deterministic_quiet_label_on_insufficient_telemetry_is_withheld_by_the_cop_not_the_verifier`, for BA, ID and AT missing
- **Tools and rules:**
  - `test_workload_rule_reads_redundant_from_unchanged_repeats_and_efficient_stays_efficient`
  - `test_audit_trace_with_unattributed_disk_changes_is_broken_not_partial`
  - `test_event_metadata_distinguishes_examined_and_empty_from_unavailable`
  - `test_coverage_matrix_separates_the_five_levels_and_case_from_estate_gaps`
  - `test_payload_carries_baseline_tool_results_but_not_the_baseline_label`
- **Recovery:**
  - `test_recovery_reassesses_once_with_the_verifier_feedback_and_verifies_again`
  - `test_recovery_does_not_rewrite_a_failed_claim_into_a_passing_one`
  - `test_recovery_keeps_the_first_rejection_when_the_reassessment_gives_no_candidate`
  - `test_no_recovery_for_an_unrecoverable_failure`
  - `test_no_recovery_in_a_replay_of_recorded_replies`
  - `test_no_recovery_when_the_model_call_budget_is_spent`
  - `test_recovered_action_is_still_subject_to_authority`
- **Bench integrity:**
  - `test_bench_keeps_all_54_cases_and_every_cop_runs_three`
  - `test_expectations_are_unchanged_and_independent_of_outputs`
  - `test_legitimate_inconclusives_stay_inconclusive`
  - `test_no_case_that_passed_before_now_fails`
  - `test_every_final_action_stays_within_authority_and_nothing_executes`
  - `test_replayed_results_are_never_labelled_as_new_live_calls`
  - `test_root_causes_can_co_occur_and_use_the_defined_codes`
  - `test_cases_fixed_without_a_new_call_are_the_ones_the_analysis_predicts`
  - `test_dashboard_embeds_exactly_the_results_file`

Test fixtures that changed:

- `tests/test_runtime_llm.py`: the LLM runtime now supplies a taxonomy, and the prompt version is `prompt-2`.
- `tests/test_runtime_error_paths.py`: the Classification branch asserts the precheck stop with no call.

Regenerate the bench and dashboard (no cost):

```
cd collector && python3 -m cops.runtime.test_bench && python3 -m cops.runtime.bench_report
```

## 10. Dashboard

The generator is `collector/cops/runtime/bench_report.py`, which reads `docs/cops/evaluation/test_bench_results.json` and writes `docs/cops/evaluation/COPS_TEST_BENCH.html`. The HTML is an output and is not edited by hand.

New sections and changes:

- **"Why cases are inconclusive":** expected versus unexpected, split into held back by the COP and forced by a verifier, plus a root-cause table that includes the fleet.
- **"Before and after the root-cause fixes":** each case with its result source.
- **Each case:**
  - The case header shows root causes, result source, the live-recorded result and the before-fixes result.
  - The trace is now an evidence journey: input evidence with the sufficiency table, then the deterministic baseline, model planning, tool calls with expandable results, the model candidate, the verifier table with recovery, the final decision, and the action lifecycle with a dry-run note.
- **Filters:**
  - COP, type, case kind;
  - expected inconclusive, unexpected inconclusive;
  - verifier failure, actual failure;
  - changed by the fixes.

The page loads with no JavaScript errors and no horizontal scroll at 390 px.

## 11. Decisions applied on 2026-11-04 (no model calls)

**Audit Traceability (approved, with clarification).** Labels now follow meaning, and severity is reported beside the label without choosing it (`rules._audit_meaning`):

| Label | Meaning | Evidence that establishes it (`reconstruct_trace`) |
|---|---|---|
| PARTIAL | audit information is incomplete, but the essential chain is reconstructable | steps without a turn id; attribution metrics in an attention state with no broken link |
| BROKEN_TRACE | an essential causal or provenance link is broken | an effect no step accounts for (actor known), a sub-agent lane with no parent, a decision citing unresolvable evidence, sources disagreeing (EXCEPTION or worse), an upstream COP blocked on an action in the record |
| UNATTRIBUTABLE | the responsible actor cannot be established | actions or effects with no session or operator (`effects_without_actor`, `actions_without_actor`) |

`reconstruct_trace` now reports `actor` (operator, agent tool and session), `unattributed_effects_actor_known`, `effects_without_actor` and `actions_without_actor`. A baseline that reached UNATTRIBUTABLE or BROKEN_TRACE on metric severity alone, while the actor and every link are on record, now reads PARTIAL and keeps its severity. `audit_traceability:abnormal` reads BROKEN_TRACE: the operator and session are recorded, but no step accounts for the three disk changes.

Tests:

- `test_audit_partial_means_incomplete_but_reconstructable`
- `test_audit_broken_trace_means_a_causal_link_is_broken_while_the_actor_is_known`
- `test_audit_unattributable_means_the_responsible_actor_cannot_be_established`
- `test_audit_severity_does_not_choose_the_label`

*Limitation:* an effect that carries no session id at all is not grouped into any session today, so a per-session COP never sees it. Catching it needs an estate-level check.

**Governance Architecture (option B, two levels).** `query_coverage_matrix` adds `estate_gap_assessment`. For each computed metric no COP consumes, it gives:

- `required_by_spec`
- `documented_consumers` (from `metric_cop_mappings.json`)
- `alternative_controls`
- a classification

Findings:

| Metric | Required by a spec? | Documented intent | Alternative control | Classification |
|---|---|---|---|---|
| `insights.no_oversight` | no | governance_intelligence, policy_enforcement, risk_mitigation (`cops_topology.json` only) | Policy Enforcement: each operation the approved policy marks `require_approval` is checked against approvals recorded in the session (`rules.policy_enforcement`, APPROVAL_REQUIRED branch). It does not cover sessions with no policy store, or long sessions of operations the policy does not name. | covered_by_alternative_control |
| `insights.lockfile` | no | classification_tagging, risk_mitigation (`cops_topology.json` only) | none found in any spec, tool or rule | latent_unspecified |

Neither is a required gap under the specs, which are the agreed source of truth, so **ADEQUATE is justified for `governance_architecture:normal`, and its expectation is unchanged**. Both items are reported as latent observations in `gap_levels.latent_observations`. GAP now needs one of these:

- an active gap; or
- a latent item that a spec requires and nothing covers.

Tests:

- `test_ga_an_unconsumed_metric_alone_is_not_a_gap_and_is_reported_as_latent`
- `test_ga_a_required_uncovered_latent_gap_is_a_gap_without_being_triggered`
- `test_ga_an_active_gap_is_a_gap`

The GA prompt was revised and is now `governance_architecture/prompt-3`. *Open specification question:* should a dependency-manifest change without a lockfile be a required responsibility? If yes, a spec should own it, and GA would report it as a latent required gap until one does.

**SC-01 and support.** I checked the three newly passing cases for independent evidence.

- **risk_mitigation:normal (LOW):** three metric states, five register and control tool observations, and a verified Policy Enforcement ALLOWED.
- **governance_intelligence:normal (NO_PATTERN):** four aggregate-report observations across the 30-day and 90-day windows.
- **governance_intelligence:abnormal (RECURRING):** five aggregate-report observations, including 4 VIOLATION decisions in 4 sessions.

The INCONCLUSIVE disclosure supports nothing in each case.

Writing the requested negative test exposed a hole. A *quiet* label (NO_PATTERN, LOW) whose only claim was the inconclusive disclosure passed every check, because SC-02 judged support only for attention labels. SC-02 (now version 2) rejects any definite label whose claims are all unverified or inconclusive upstream findings (`no_independent_support`). Tests:

- `test_an_inconclusive_upstream_finding_alone_cannot_support_a_definite_conclusion`, for NO_PATTERN, RECURRING, LOW and MATERIAL
- `test_the_three_disclosure_cases_rest_on_independent_evidence`

**Bench after these changes (no calls):** 48 PASS / 1 FAIL / 5 INCONCLUSIVE. That is unchanged, with no regressions. Test suite: `337 passed, 1 skipped`.

## 12. Live re-run preflight

The re-run runner is `collector/cops/runtime/rerun.py`.

- **Scope:** exactly six cases and fleet F2. The six cases are:
  - workflow_orchestration:abnormal
  - classification_tagging:normal
  - workload_optimization:abnormal
  - knowledge_policy_management:abnormal
  - governance_architecture:abnormal
  - governance_architecture:normal
  - F2's LLM COPs are incident_response and risk_mitigation.
- **Hard cap:** one ledger for the whole run, checked before *every* provider call, including retries and re-assessments. A call is made only if the spend so far plus that call's worst case stays within the cap. The worst case is the estimated input plus the full `max_output_tokens` at the configured prices; at 8,000 output tokens it is about $0.10. The cap may not exceed $0.75.
- **Results:** written to `docs/cops/evaluation/rerun_2026-11-04/`. The 2026-11-02 `live_results.json` is never overwritten.
- **Action effects:** the preflight asserts that the runtime's adapter is `DryRunAdapter`.
- **Simulated pass:** a pass with scripted replies through the same guard made 19 simulated calls and recorded no problems (`rerun_simulated.json`, labelled simulated).
- **Preflight result:** every check passes except one. **No provider credential is available in this environment.** No paid call has been made.

Tests:

- `test_rerun_scope_is_exactly_the_approved_six_cases_and_fleet_f2`
- `test_rerun_cap_guard_refuses_a_call_whose_worst_case_would_cross_the_cap_including_retries`
- `test_rerun_preflight_refuses_without_settings_and_above_the_approved_cap`

## 13. Demo summary

- Ten cases did not pass, and each now has a traceable cause.
- Four were wrong for a fixable reason in our own code, and they pass now with no new model call:
  - three were a verifier treating an honest "the upstream was inconclusive" as a citation error;
  - one was a rule that called a broken trace "partial".
- Five are model replies with one specific, named fault. The faults are a wrong field, a missing `metric_id`, an uncited claim, a missed conflict check, and a misread empty result. The tools and prompts are fixed, the verifiers are unchanged, and the system now gets one supervised retry with the verifier's feedback. Proving that needs a small live run, about $0.35–0.65.
- One is a disagreement about what "coverage" means, and it needs a decision.
- No ground truth was changed, no verifier was weakened, and no inconclusive that should be inconclusive was turned green.
