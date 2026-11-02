# PR #4: fleet completion report

Branch `feat/cops-langgraph-runtime` of `Purvi9399/signal-dashboard`, 2026-11-02. Nothing has been pushed. GitHub still shows `8a45418`. Local commits on top of it: `3e53680` (the five acceptance fixes), `503e632` (this work), `313f17a` (scoring of a refused live attempt) and the commit that adds the live results.

## Status

| Item | Result |
|---|---|
| Implementation | All 18 COPs pass every check in `fleet_report.implementation_status`: every node of the shared subgraph, a rule, registered tools, and for the 10 LLM COPs a prompt, a detail schema and tools. The split is 10 LLM and 8 deterministic, unchanged. |
| Offline tests | **249 passed, 0 failed, 1 skipped** (real pytest). The skipped test is the opt-in live smoke test. |
| Evaluation scenarios | **25 of 25 pass**, 121 of 121 expectations. LLM replies in them are scripted, so every LLM record is `simulated=True`. |
| Real LLM calls completed | **60 in the full live run**, 2 per case across 30 cases. Every call succeeded, and all 60 records were verified: `simulated=False`, provider `anthropic`, the model id, measured tokens, a request id and latency. Usage was 232,423 input and 80,123 output tokens, **$1.27**, and 594 s spent in model calls. With the smoke runs before it (about $0.43), the total is **about $1.70 of the $5 cap**. An earlier attempt was refused for lack of credits ($0, `live_attempt_2026-11-02.json`). |
| Live ground-truth agreement | **21 of 30 agree, 8 inconclusive, 1 false positive, 0 missed findings, 0 unsupported quiet labels.** Results are in `docs/cops/evaluation/live_results.json` and `LIVE_RESULTS.md`. |

## 1. Audit

### The five earlier fixes

They are verified on the local branch. They are not on GitHub until a push.

| # | Fix | Evidence |
|---|---|---|
| 1 | Missing evidence cannot produce an unjustified quiet finding | `runtime/requirements.py` `required_unavailable`, `graph._gate_required`, and DS-01's "required evidence unavailable" condition. Tests: `test_deterministic_quiet_label_is_withheld_without_required_artifact` and `test_llm_quiet_label_without_required_artifact_is_rejected_by_ds01`. In the offline check, none of the 10 missing-evidence cases published the quiet label. |
| 2 | AU-01 validates the authorised action | `verifiers.au01` runs on the original proposal (`subject: original_proposal`) and on the final action (`verify_action`, `subject: final_action`). The proposed action records `original_proposal`, `original_verification`, `corrected_proposal` and `final_authority`. Tests: the four `test_au01_*`, plus two new tests in which the model claims approval or invents an action. |
| 3 | Evaluation docs describe coverage accurately | E2E is retitled "five artifacts absent" and carries 13 finding expectations. E2E-full supplies every artifact. RUNTIME.md section C matches `results.json`. |
| 4 | Confidence cap and provider error paths are tested | `test_confidence_is_capped_at_the_evidence_ceiling`, `test_timeout_retries_then_stops`, `test_malformed_output_retries_then_stops`, `test_unavailable_provider_is_recorded_not_fabricated`, and the 28 new live-path tests. |
| 5 | Unambiguous COP identifiers | Result tables use full spec keys (`evaluation.table`). `grep -rnw RM docs/cops/evaluation collector/cops/runtime` finds only the code comment that explains the change. |

### Compatibility with the Signal contracts

- **Decision rows:** `decision_id` uses the same uuid5 formula as `cops.models.COPDecision`, so runtime rows and legacy rows upsert into the same `cop_decisions` row (`test_decision_row_has_every_column_and_stable_id`).
- **Schema:** `collector/cops_runtime_schema.sql` matches `persistence.TABLES` (`test_schema_files_match_tables`). It now also has the three new invocation columns. The migration has not been applied anywhere.
- **Malhar's contracts:** `collector_framework/cops/contracts.py` in Malhar's repository is not readable from this session. No mapping onto it is claimed, and Malhar's repository was not touched.

### Gaps found before changing code

| # | Gap | How it was shown |
|---|---|---|
| G1 | The live provider would fail on its first real call. The schema it sent had `additionalProperties: true` on `args` and `detail`, plus `maxItems`, `minimum` and `maximum`. Structured outputs reject all of these. | Dumped `SCHEMAS[...].model_json_schema()` and compared it with the API's documented limits. |
| G2 | Any provider error other than a timeout, a connection failure or a refusal crashed the whole case: 429, 5xx, 400, 401, or any other exception. | A probe raising a plain exception from the provider gave `CRASH Boom HTTP 529 overloaded`. |
| G3 | Invocation records could not show whether token counts were measured, why the reply stopped, or the provider request id. | `LLMInvocation` fields. |
| G4 | A reply cut at the output limit was indistinguishable from bad JSON, and 4,000 output tokens is small once thinking is on. | `AnthropicProvider.complete`. |
| G5 | There was no way to continue a case after a crash. `resume_case` only answers approval interrupts. | `graph.py`. |
| G6 | There was no live evaluation: no per-COP normal, abnormal and missing-evidence cases, no overall spending cap, and no check of invocation records. | |
| G7 | Found by the offline dry check: a quiet model reading passed verification even when the COP's own rule baseline read a problem on the same evidence (`governance_architecture`, `knowledge_policy_management` and `governance_intelligence` abnormal cases). RP-01 is diagnostic only. | `live_eval --offline`, before DS-03 |
| G8 | Live calls would hit the 60 s per-investigation and 30 s per-call limits. | `LLMBudget` defaults |

## 2. Changes

| File | Symbol | Change |
|---|---|---|
| `runtime/llm.py` | `wire_schema`, `decode_wire` | API-compatible reply schema. Bounds are dropped from the wire but still enforced by pydantic. Open objects travel as JSON strings. |
| `runtime/llm.py` | `AnthropicProvider` | Typed error mapping: timeout; 429 and 5xx as `ProviderTransient`, retried once; other 4xx as unavailable, not retried; `max_tokens` as `ProviderTruncated`; refusal. Also adds the request id, stop reason, `COPS_MAX_OUTPUT_TOKENS` (default 16,000), optional `COPS_EFFORT`, and an injectable client for tests. |
| `runtime/llm.py` | `LLMGateway.invoke` | Any other provider exception is recorded as `provider_error`, and the case continues. The record gains `tokens_measured`, `stop_reason` and `request_id`. A price lookup falls back to the requested model id. |
| `runtime/contracts.py` | `LLMInvocation` | Adds `tokens_measured`, `stop_reason` and `request_id`. |
| `runtime/persistence.py`, `cops_runtime_schema.sql` | `cop_llm_invocations` | Adds the same three columns. |
| `runtime/graph.py` | `recover_case` | Continues a case from its last checkpoint after a crash. Finished COPs are not run again. |
| `runtime/verifiers.py` | DS-03 | A quiet model reading fails when the rule baseline reads a substantive non-quiet label (BLOCKS_FINDING). It changes nothing for an attention reading. |
| `runtime/live_eval.py` | new | 30 fictional cases with `EXPECTED` fixed in advance, `deterministic_upstream`, `check_live_config`, a total cap, `verify_records` and a report. |
| `runtime/fleet_report.py` | new | Generates the 18-row matrix (`docs/cops/evaluation/FLEET_MATRIX.md`). |
| `requirements-cops-runtime.txt` | | The optional `anthropic==2.3.0`, the version the stub tests ran with. |

**What DS-03 changed in the 25 scenarios.** All 25 still pass. Four published findings changed:

- `knowledge_policy_management` in E2E: was CURRENT, now INCONCLUSIVE. The baseline reads CONFLICT, because `sensitive_policy` has overlapping allow and require_approval read rules.
- `risk_mitigation` in E2E-full: was LOW, now INCONCLUSIVE. The baseline reads EMERGING.
- `anomaly_detection` in C17 (NORMAL) and `incident_response` in C06 (NO_INCIDENT): another check already rejected these, and DS-03 now fails as well.

## 3. Tests

```
python3 -m pytest -p no:cacheprovider tests/ -q -rs
249 passed, 1 skipped
SKIPPED tests/test_runtime_llm.py:169: opt-in live smoke test: needs COPS_LLM_LIVE=1, COPS_LLM_DATA_APPROVED=1, COPS_MODEL and credentials
```

That is 53 more than the 196 at `3e53680`: 30 in `test_runtime_live_path.py`, 18 in `test_runtime_live_eval.py`, and 5 in `test_runtime_verifiers.py`. The 5 are two DS-03 tests, the unchecked-value test and two path tests, and three of them come from live-run findings.

| Requested area | Tests |
|---|---|
| Missing evidence | `test_runtime_error_paths.py` (requirement gates, DS-01); `test_offline_run_of_all_thirty_cases_is_simulated_and_verifiable` (no quiet label on any missing-evidence case) |
| Malformed model output | `test_malformed_output_retries_then_stops` (x10 COPs), `test_malformed_live_reply_is_rejected_by_the_schema`, `test_reply_cut_at_max_tokens_is_malformed_and_retried`, `test_detail_that_fails_the_cop_schema_is_inconclusive` |
| Provider timeout and errors | `test_timeout_retries_then_stops` (x10); `test_each_provider_failure_is_recorded_and_inconclusive` (timeout, 429, 529, 400, 401, connection); `test_refusal_is_unavailable_not_a_finding`; `test_an_unexpected_provider_exception_does_not_stop_the_case` |
| Hallucinated claims | `test_ev02_and_id02_catch_invented_references`, `test_sc01_catches_a_misread_tool_result`, `test_mc01_recomputes_from_raw_and_can_fail`, `test_ds03_rejects_a_quiet_model_reading_the_rule_baseline_contradicts`; scenario C16 |
| Unauthorised tool requests | `test_prohibited_tool_request_is_refused_and_logged` (x10); scenarios C13 and C14 |
| The model authorising itself | `test_model_text_claiming_approval_does_not_execute_anything`, `test_model_cannot_choose_an_action_outside_its_vocabulary`, `test_au01_*` |
| Budget exhaustion | `test_llm_call_budget_stops_the_loop`, `test_tool_budget_refuses_extra_requests`, `test_token_and_cost_budgets_are_checked_before_each_call`, `test_total_cap_stops_scheduling_cases` |
| Checkpoint recovery | `test_case_recovers_from_its_checkpoint_after_a_crash` (new); `test_interrupt_checkpoint_approve_resume_after_restart` |
| No fixture substitution on live failure | `test_each_provider_failure_is_recorded_and_inconclusive` asserts that the provider stays `AnthropicProvider`, and that every record is `simulated=False` |
| Deterministic COPs make no model calls | `test_deterministic_cops_make_no_model_calls`, `test_fleet_matrix_has_eighteen_rows_and_marks_live_as_not_run` |

**Coverage** (`coverage run --branch`, sandbox only):

- `runtime/` overall: 87%
- `graph.py`: 99%
- `llm.py`: 95%
- `verifiers.py`: 95%
- `live_eval.py`: 78%

The uncovered part of `live_eval.py` is mainly the live branch of `main` and the report text for skipped cases.

## 4. Results matrix

| COP | Implementation | Type | Test scenarios | Findings | Verification | Failures | Latency | Token usage | Cost |
|---|---|---|---|---|---|---|---|---|---|
| `runtime_monitoring` | complete | deterministic | 23 offline | DEGRADED x1, INSUFFICIENT x1, SUFFICIENT x21 | 23 verified, 0 rejected, 0 inconclusive | 0 | median 1 ms per run | none (no model call) | $0 (no model call) |
| `workflow_orchestration` | complete | LLM | 5 offline + 3 live | INCONCLUSIVE x2, NORMAL x2, STALLED x1 (simulated); live: normal=NORMAL, abnormal=INCONCLUSIVE, missing=INCONCLUSIVE | 3 verified, 2 rejected, 0 inconclusive; live 1/3 verified | 0; live: 0 call errors, disagreed on abnormal | median 6 ms per run (simulated); live 55472 ms in model calls | live 23767 in / 6848 out | $0.1160 (configured price x measured tokens) |
| `context_memory` | complete | LLM | 4 offline + 3 live | CONTEXT_LOSS x2, CONTEXT_OK x1, INCONCLUSIVE x1 (simulated); live: normal=CONTEXT_OK, abnormal=CONTEXT_LOSS, missing=INCONCLUSIVE | 3 verified, 0 rejected, 1 inconclusive; live 2/3 verified | 0; live: 0 call errors, disagreed on none | median 6 ms per run (simulated); live 41836 ms in model calls | live 19584 in / 5160 out | $0.0908 (configured price x measured tokens) |
| `behavior_analytics` | complete | deterministic | 6 offline | DRIFTING x1, INCONCLUSIVE x1, POOR_RECOVERY x1, STABLE x3 | 5 verified, 1 rejected, 0 inconclusive | 0 | median 1 ms per run | none (no model call) | $0 (no model call) |
| `anomaly_detection` | complete | LLM | 14 offline + 3 live | ANOMALOUS x4, INCONCLUSIVE x4, INDETERMINATE x2, NORMAL x4 (simulated); live: normal=NORMAL, abnormal=ANOMALOUS, missing=INCONCLUSIVE | 10 verified, 3 rejected, 1 inconclusive; live 2/3 verified | 0; live: 0 call errors, disagreed on none | median 6 ms per run (simulated); live 70099 ms in model calls | live 25754 in / 10046 out | $0.1520 (configured price x measured tokens) |
| `interaction_discovery` | complete | deterministic | 7 offline | CONTAINED x6, NEW_ROUTE x1 | 7 verified, 0 rejected, 0 inconclusive | 0 | median 1 ms per run | none (no model call) | $0 (no model call) |
| `agent_lifecycle_management` | complete | deterministic | 3 offline | CHANGED x1, UNCHANGED x2 | 3 verified, 0 rejected, 0 inconclusive | 0 | median 1 ms per run | none (no model call) | $0 (no model call) |
| `policy_enforcement` | complete | deterministic | 8 offline | ALLOWED x2, UNCERTAIN x1, VIOLATION x5 | 8 verified, 0 rejected, 0 inconclusive | 0 | median 2 ms per run | none (no model call) | $0 (no model call) |
| `recovery_resilience` | complete | deterministic | 4 offline | FAILED_RECOVERY x1, INCONCLUSIVE x1, RECOVERED x2 | 3 verified, 0 rejected, 0 inconclusive | 0 | median 1 ms per run | none (no model call) | $0 (no model call) |
| `incident_response` | complete | LLM | 10 offline + 3 live | INCIDENT x6, INCONCLUSIVE x1, NO_INCIDENT x1, WATCH x2 (simulated); live: normal=NO_INCIDENT, abnormal=INCIDENT, missing=INCONCLUSIVE | 9 verified, 1 rejected, 0 inconclusive; live 2/3 verified | 0; live: 0 call errors, disagreed on none | median 7 ms per run (simulated); live 67718 ms in model calls | live 25281 in / 9652 out | $0.1471 (configured price x measured tokens) |
| `classification_tagging` | complete | LLM | 3 offline + 3 live | INCONCLUSIVE x1, INTEGRITY x1, UNCLASSIFIED x1 (simulated); live: normal=INCONCLUSIVE, abnormal=INTEGRITY, missing=INCONCLUSIVE | 2 verified, 0 rejected, 1 inconclusive; live 1/3 verified | 0; live: 0 call errors, disagreed on normal | median 6 ms per run (simulated); live 56049 ms in model calls | live 21448 in / 6981 out | $0.1127 (configured price x measured tokens) |
| `resource_management` | complete | deterministic | 6 offline | EXCESSIVE x1, INCONCLUSIVE x4, NORMAL x1 | 2 verified, 0 rejected, 0 inconclusive | 0 | median 1 ms per run | none (no model call) | $0 (no model call) |
| `risk_mitigation` | complete | LLM | 3 offline + 3 live | EMERGING x2, INCONCLUSIVE x1 (simulated); live: normal=INCONCLUSIVE, abnormal=EMERGING, missing=INCONCLUSIVE | 2 verified, 1 rejected, 0 inconclusive; live 1/3 verified | 0; live: 0 call errors, disagreed on normal | median 7 ms per run (simulated); live 63539 ms in model calls | live 23944 in / 8725 out | $0.1351 (configured price x measured tokens) |
| `governance_intelligence` | complete | LLM | 3 offline + 3 live | INCONCLUSIVE x1, NO_PATTERN x2 (simulated); live: normal=INCONCLUSIVE, abnormal=INCONCLUSIVE, missing=INCONCLUSIVE | 2 verified, 0 rejected, 1 inconclusive; live 0/3 verified | 0; live: 0 call errors, disagreed on normal, abnormal | median 11 ms per run (simulated); live 57800 ms in model calls | live 19830 in / 7358 out | $0.1132 (configured price x measured tokens) |
| `workload_optimization` | complete | LLM | 4 offline + 3 live | EFFICIENT x1, INCONCLUSIVE x3 (simulated); live: normal=EFFICIENT, abnormal=INCONCLUSIVE, missing=INCONCLUSIVE | 1 verified, 2 rejected, 1 inconclusive; live 1/3 verified | 0; live: 0 call errors, disagreed on abnormal | median 7 ms per run (simulated); live 62333 ms in model calls | live 21344 in / 8612 out | $0.1288 (configured price x measured tokens) |
| `audit_traceability` | complete | deterministic | 3 offline | COMPLETE x3 | 3 verified, 0 rejected, 0 inconclusive | 0 | median 1 ms per run | none (no model call) | $0 (no model call) |
| `knowledge_policy_management` | complete | LLM | 2 offline + 3 live | CURRENT x1, INCONCLUSIVE x1 (simulated); live: normal=CURRENT, abnormal=INCONCLUSIVE, missing=INCONCLUSIVE | 1 verified, 1 rejected, 0 inconclusive; live 1/3 verified | 0; live: 0 call errors, disagreed on abnormal | median 6 ms per run (simulated); live 41504 ms in model calls | live 18679 in / 5304 out | $0.0904 (configured price x measured tokens) |
| `governance_architecture` | complete | LLM | 4 offline + 3 live | ADEQUATE x3, GAP x1 (simulated); live: normal=GAP, abnormal=INCONCLUSIVE, missing=INCONCLUSIVE | 4 verified, 0 rejected, 0 inconclusive; live 1/3 verified | 0; live: 0 call errors, disagreed on normal, abnormal | median 8 ms per run (simulated); live 78004 ms in model calls | live 32792 in / 11437 out | $0.1800 (configured price x measured tokens) |

How to read it:

- For LLM COPs, the live columns come from the full live run: measured tokens, configured price × measured tokens, and time spent in model calls.
- Offline LLM findings come from scripted replies and are marked "(simulated)". They show the harness working, not a model's judgement.
- The failures column counts failed scenario expectations. It is 0 for every COP.

## 5. Other results

### Real LLM calls

**The full run (2026-11-02):** 30 cases on the chosen model with dry-run enforcement. That made 60 real calls with 0 errors, 232,423 input and 80,123 output tokens, and cost $1.27 at the configured price. All 60 records passed `verify_records`.

**Before the full run,** five single-case and two-case smoke runs cost about $0.43. Each exposed a gap, which was fixed and tested before going on:

1. The API accepted the wire schema, but the model asked for five tools when four are allowed. Dropped bounds such as `maxItems` were not stated to it. They are now written into the field descriptions (`test_dropped_bounds_are_stated_in_the_wire_descriptions`).
2. The model wrote values into the claim text but left `asserted_value` null. SC-01 reported this as a contradiction. It is now `claim_unchecked` (`test_sc01_rejects_a_value_claim_that_states_no_value`). Each claim field is described on the wire, and `asserted_value` travels as a required JSON-encoded string (`test_asserted_value_travels_as_a_required_json_string`). After that change, Anomaly Detection reached a verified ANOMALOUS on the spike case.
3. The model cited list items as `snapshots.0.ids`, a form the resolver did not accept (`test_result_field_paths_accept_both_list_index_forms`).

**After the full run,** two more path forms the model used were fixed: a `result.` prefix, and keys that contain dots such as `/ws/app.py` (`test_result_field_paths_from_the_live_run`). The recorded live replies were then **replayed** through the fixed verifiers at no cost (`FixtureProvider` over the recorded `structured_response`s, so the replay is simulated). The outcome is unchanged: 21 agree, 8 inconclusive, 1 false positive. The two affected findings fail on other claims as well, as listed below. The table above is the run as it happened.

**The first attempt** sent 30 requests, and the provider refused all of them for lack of credits. It cost $0. The record is kept in `live_attempt_2026-11-02.json`.

### Ground-truth agreement (live)

The expected labels are `live_eval.EXPECTED`, fixed in `503e632` before any live call and not changed since.

| Outcome | Count | Cases |
|---|---|---|
| agree | 21 | All 10 missing-evidence cases. Normal: workflow_orchestration, anomaly_detection, context_memory, incident_response, workload_optimization, knowledge_policy_management. Abnormal: anomaly_detection ANOMALOUS, context_memory CONTEXT_LOSS, incident_response INCIDENT, classification_tagging INTEGRITY, risk_mitigation EMERGING |
| inconclusive | 8 | See below |
| false positive | 1 | `governance_architecture:normal` |
| missed finding | 0 | |
| unsupported quiet label | 0 | |

### False positives, missed findings and inconclusive outcomes

**False positive: `governance_architecture:normal`.**
- The model read GAP (0.72), and the finding verified.
- Its claims are true: the coverage matrix lists `insights.no_oversight` and `insights.lockfile` as metrics no COP reads.
- The frozen expectation and the rule baseline both read ADEQUATE, because they look only at unowned observable types and fields.
- So the model found a real catalogue fact that the ground truth does not count as a gap. Whether a computed metric that no COP reads is a GAP is a spec question for you and Malhar. The expectation was not changed after the run.

**Inconclusive: 8.** In 6 of these the model's candidate was right, but a claim behind it failed verification.

| Case | Candidate | Why it was not published | Whose error |
|---|---|---|---|
| `workflow_orchestration:abnormal` | STALLED (right) | SC-01: claimed a longest run of 4, and the tool says 5 | model (misread) |
| `risk_mitigation:normal` | LOW (right) | SC-01: cited Resource Management's unverified finding as evidence | model |
| `governance_intelligence:normal` | NO_PATTERN (right) | SC-01: cited Recovery & Resilience's unverified finding | model |
| `governance_intelligence:abnormal` | RECURRING (right) | SC-01: same | model |
| `workload_optimization:abnormal` | REDUNDANT (right) | SC-01: a metric_value claim with no metric id. Its repeated-reads claim (7) was right but failed on the path form, which is fixed now | model, and a harness path gap |
| `governance_architecture:abnormal` | GAP (right) | SC-01: a metric claim with no metric id | model |
| `knowledge_policy_management:abnormal` | CURRENT (wrong) | DS-03: the rules read CONFLICT. Without DS-03 this would have been a published **missed finding** | model missed it; verifier caught it |
| `classification_tagging:normal` | none | The model chose inconclusive itself | model (cautious) |

Every rejection is fail-closed: the published label is INCONCLUSIVE, and the candidate is kept as `rejected_candidate`.

The most frequent cause, citing an upstream finding that is not verified, comes from a rule purvi and Malhar may want to revisit. Today one such claim rejects the whole finding. Dropping only that claim would let the 3 correct candidates through.

### Remaining blockers

1. **Decisions for you and Malhar:**
   - DS-03, which makes the rule baseline a veto over a quiet LLM reading.
   - Whether one unverified-upstream claim should reject the whole finding or only that claim.
   - Whether "a metric read by no COP" counts as a GAP.
   - The two judgement-call expectations: `classification_tagging:abnormal` and `governance_architecture:missing`.
2. **The pasted API keys** are in the chat history and should be rotated. The replacement belongs in Project settings, under Cloud environment, not in chat.
3. **Carried over:** Malhar's contracts and verifier ids are unread, the schema migration has not been applied, and there are no real authoritative artifacts (RUNTIME.md items 1, 2, 6 and 7).
4. **Not pushed.** GitHub still shows `8a45418`. A push triggers a Vercel preview deploy.

## 6. Commands to reproduce

All commands run from the repository root unless they start with `cd collector`.

```
pip install -r collector/requirements-cops-runtime.txt
pip install anthropic==2.3.0                         # needed for tests/test_runtime_live_path.py (skipped without it)

python3 -m pytest -p no:cacheprovider tests/ -q -rs  # 249 passed, 1 skipped
python3 -m pytest -p no:cacheprovider tests/test_runtime_live_path.py tests/test_runtime_live_eval.py -q

cd collector && python3 -m cops.runtime.evaluation           # 25 scenarios -> docs/cops/evaluation/RESULTS.md, results.json
cd collector && python3 -m cops.runtime.matrix               # implementation matrix -> matrix.json
cd collector && python3 -m cops.runtime.live_eval --offline  # 30 live cases on scripted replies -> LIVE_OFFLINE_CHECK_RESULTS.md
cd collector && python3 -m cops.runtime.fleet_report         # 18-row matrix -> FLEET_MATRIX.md, fleet_matrix.json

# coverage (optional)
pip install coverage && coverage run --branch --source=collector/cops/runtime -m pytest -p no:cacheprovider tests/ -q && coverage report

# live run: paid, refused unless every variable is set; stops before the total cap
cd collector && COPS_LLM_LIVE=1 COPS_LLM_DATA_APPROVED=1 COPS_MODEL=<model id> \
  COPS_PRICING='{"<model id>": [<input $/MTok>, <output $/MTok>]}' COPS_LIVE_MAX_TOTAL_USD=<cap> \
  python3 -m cops.runtime.live_eval                  # -> docs/cops/evaluation/live_results.json, LIVE_RESULTS.md
cd collector && python3 -m cops.runtime.fleet_report # refills the live columns from measured usage
```

The model id and its prices are configuration, not code. Use the provider's published list price for the chosen model, checked on the day of the run. `ANTHROPIC_API_KEY` comes from the environment and is never written to the repository.

## Limitations

- The model's judgement has been tested on one live run of 30 fictional cases with one model. That is not enough to measure run-to-run variance, and the offline scenarios still use scripted replies.
- Each live case runs the target COP with deterministic upstreams only. LLM upstreams are left out, so, for example, Incident Response runs without Anomaly Detection's finding. Their absence is recorded in the consumption log.
- Verifiers check that claims are true, not that a reading is complete. DS-03 narrows this gap but does not close it.
- The live run used `max_llm_calls=4`, yet every investigation finished in 2 calls (plan, then assess). The multi-step loop of requesting more evidence has not been exercised live.
- Coverage figures come from this sandbox.
