# PR #4: fleet completion report

Branch `feat/cops-langgraph-runtime` of `Purvi9399/signal-dashboard`, 2026-11-02. Nothing has been pushed. GitHub still shows `8a45418`. Two local commits sit on top of it: `3e53680`, the five acceptance fixes, and the commit that adds this report.

## Status

| Item | Result |
|---|---|
| Implementation | All 18 COPs pass every check in `fleet_report.implementation_status`: every node of the shared subgraph, a rule, registered tools, and for the 10 LLM COPs a prompt, a detail schema and tools. The split is 10 LLM and 8 deterministic, unchanged. |
| Offline tests | **243 passed, 0 failed, 1 skipped** (real pytest). The skipped test is the opt-in live smoke test. |
| Evaluation scenarios | **25 of 25 pass**, 121 of 121 expectations. LLM replies in them are scripted, so every LLM record is `simulated=True`. |
| Real LLM calls completed | **0.** No API key is configured, and the budget has not been approved. The live evaluation has **not** been run, and no live result is claimed. |
| Live harness | Built, and dry-checked offline on all 30 cases. Every record was verified as simulated, and no problems were found. |

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
243 passed, 1 skipped
SKIPPED tests/test_runtime_llm.py:169: opt-in live smoke test: needs COPS_LLM_LIVE=1, COPS_LLM_DATA_APPROVED=1, COPS_MODEL and credentials
```

That is 47 more than the 196 at `3e53680`. 28 are in `test_runtime_live_path.py`, 17 in `test_runtime_live_eval.py`, and 2 are DS-03 tests in `test_runtime_verifiers.py`.

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
| `workflow_orchestration` | complete | LLM | 5 offline + 3 live (not run) | INCONCLUSIVE x2, NORMAL x2, STALLED x1 (simulated) | 3 verified, 2 rejected, 0 inconclusive | 0 | median 9 ms per run (simulated) | 10 simulated calls, estimated only; live not run | live not run |
| `context_memory` | complete | LLM | 4 offline + 3 live (not run) | CONTEXT_LOSS x2, CONTEXT_OK x1, INCONCLUSIVE x1 (simulated) | 3 verified, 0 rejected, 1 inconclusive | 0 | median 6 ms per run (simulated) | 10 simulated calls, estimated only; live not run | live not run |
| `behavior_analytics` | complete | deterministic | 6 offline | DRIFTING x1, INCONCLUSIVE x1, POOR_RECOVERY x1, STABLE x3 | 5 verified, 1 rejected, 0 inconclusive | 0 | median 2 ms per run | none (no model call) | $0 (no model call) |
| `anomaly_detection` | complete | LLM | 14 offline + 3 live (not run) | ANOMALOUS x4, INCONCLUSIVE x4, INDETERMINATE x2, NORMAL x4 (simulated) | 10 verified, 3 rejected, 1 inconclusive | 0 | median 6 ms per run (simulated) | 31 simulated calls, estimated only; live not run | live not run |
| `interaction_discovery` | complete | deterministic | 7 offline | CONTAINED x6, NEW_ROUTE x1 | 7 verified, 0 rejected, 0 inconclusive | 0 | median 1 ms per run | none (no model call) | $0 (no model call) |
| `agent_lifecycle_management` | complete | deterministic | 3 offline | CHANGED x1, UNCHANGED x2 | 3 verified, 0 rejected, 0 inconclusive | 0 | median 1 ms per run | none (no model call) | $0 (no model call) |
| `policy_enforcement` | complete | deterministic | 8 offline | ALLOWED x2, UNCERTAIN x1, VIOLATION x5 | 8 verified, 0 rejected, 0 inconclusive | 0 | median 2 ms per run | none (no model call) | $0 (no model call) |
| `recovery_resilience` | complete | deterministic | 4 offline | FAILED_RECOVERY x1, INCONCLUSIVE x1, RECOVERED x2 | 3 verified, 0 rejected, 0 inconclusive | 0 | median 1 ms per run | none (no model call) | $0 (no model call) |
| `incident_response` | complete | LLM | 10 offline + 3 live (not run) | INCIDENT x6, INCONCLUSIVE x1, NO_INCIDENT x1, WATCH x2 (simulated) | 9 verified, 1 rejected, 0 inconclusive | 0 | median 9 ms per run (simulated) | 25 simulated calls, estimated only; live not run | live not run |
| `classification_tagging` | complete | LLM | 3 offline + 3 live (not run) | INCONCLUSIVE x1, INTEGRITY x1, UNCLASSIFIED x1 (simulated) | 2 verified, 0 rejected, 1 inconclusive | 0 | median 6 ms per run (simulated) | 6 simulated calls, estimated only; live not run | live not run |
| `resource_management` | complete | deterministic | 6 offline | EXCESSIVE x1, INCONCLUSIVE x4, NORMAL x1 | 2 verified, 0 rejected, 0 inconclusive | 0 | median 1 ms per run | none (no model call) | $0 (no model call) |
| `risk_mitigation` | complete | LLM | 3 offline + 3 live (not run) | EMERGING x2, INCONCLUSIVE x1 (simulated) | 2 verified, 1 rejected, 0 inconclusive | 0 | median 7 ms per run (simulated) | 6 simulated calls, estimated only; live not run | live not run |
| `governance_intelligence` | complete | LLM | 3 offline + 3 live (not run) | INCONCLUSIVE x1, NO_PATTERN x2 (simulated) | 2 verified, 0 rejected, 1 inconclusive | 0 | median 13 ms per run (simulated) | 6 simulated calls, estimated only; live not run | live not run |
| `workload_optimization` | complete | LLM | 4 offline + 3 live (not run) | EFFICIENT x1, INCONCLUSIVE x3 (simulated) | 1 verified, 2 rejected, 1 inconclusive | 0 | median 6 ms per run (simulated) | 8 simulated calls, estimated only; live not run | live not run |
| `audit_traceability` | complete | deterministic | 3 offline | COMPLETE x3 | 3 verified, 0 rejected, 0 inconclusive | 0 | median 1 ms per run | none (no model call) | $0 (no model call) |
| `knowledge_policy_management` | complete | LLM | 2 offline + 3 live (not run) | CURRENT x1, INCONCLUSIVE x1 (simulated) | 1 verified, 1 rejected, 0 inconclusive | 0 | median 8 ms per run (simulated) | 4 simulated calls, estimated only; live not run | live not run |
| `governance_architecture` | complete | LLM | 4 offline + 3 live (not run) | ADEQUATE x3, GAP x1 (simulated) | 4 verified, 0 rejected, 0 inconclusive | 0 | median 11 ms per run (simulated) | 8 simulated calls, estimated only; live not run | live not run |

How to read it:

- The token and cost columns for LLM COPs stay `live not run` until a live run writes `docs/cops/evaluation/live_results.json`. Then `python -m cops.runtime.fleet_report` fills them from measured usage.
- Offline LLM findings come from scripted replies. They show the harness working, not a model's judgement.
- The failures column counts failed scenario expectations. It is 0 for every COP.

## 5. Other results

### Real LLM calls

**0 completed.** Getting to a paid run needs three things:

1. `ANTHROPIC_API_KEY` set in a cloud environment for this project. Only sessions started after that see the key.
2. Your choice of model and total cap.
3. Running the command in section 6.

### Ground-truth agreement

- **Live:** not run.
- **Offline scenarios** (LLM replies scripted): 25 of 25 scenarios, 121 of 121 expectations.
- **Offline dry check of the 30 live cases** (scripted generic replies, not a model):
  - 19 agree
  - 10 inconclusive
  - 1 missed finding
  - 0 false positives
  - 0 unsupported quiet labels

  The scripted reply interprets no attention state, so its inconclusive abnormal cases are expected. These numbers test the cases and the verifiers, not a model.

| Case | Expected | Label | Status | Verified | Outcome | Calls | Tokens in/out | Latency ms | Cost |
|---|---|---|---|---|---|---|---|---|---|
| `workflow_orchestration:normal` | NORMAL | NORMAL | SUBSTANTIVE | True | agree | 2 | 2495/444 | 8 | unpriced |
| `workflow_orchestration:abnormal` | STALLED | INCONCLUSIVE | INCONCLUSIVE | False | inconclusive | 2 | 2561/152 | 3 | unpriced |
| `workflow_orchestration:missing` | INCONCLUSIVE | INCONCLUSIVE | REJECTED_BY_VERIFIER | False | agree | 2 | 2502/363 | 4 | unpriced |
| `anomaly_detection:normal` | NORMAL | NORMAL | SUBSTANTIVE | True | agree | 2 | 3679/524 | 3 | unpriced |
| `anomaly_detection:abnormal` | ANOMALOUS | INCONCLUSIVE | INCONCLUSIVE | False | inconclusive | 2 | 3768/159 | 4 | unpriced |
| `anomaly_detection:missing` | INDETERMINATE, INCONCLUSIVE | INCONCLUSIVE | REJECTED_BY_VERIFIER | False | agree | 2 | 3527/523 | 6 | unpriced |
| `context_memory:normal` | CONTEXT_OK | INCONCLUSIVE | INCONCLUSIVE | False | inconclusive | 2 | 1992/133 | 3 | unpriced |
| `context_memory:abnormal` | CONTEXT_LOSS | INCONCLUSIVE | INCONCLUSIVE | False | inconclusive | 2 | 2026/133 | 3 | unpriced |
| `context_memory:missing` | INCONCLUSIVE | INCONCLUSIVE | INCONCLUSIVE | False | agree | 2 | 2011/148 | 3 | unpriced |
| `incident_response:normal` | NO_INCIDENT | NO_INCIDENT | SUBSTANTIVE | True | agree | 2 | 2640/416 | 3 | unpriced |
| `incident_response:abnormal` | INCIDENT, WATCH | INCONCLUSIVE | INCONCLUSIVE | False | inconclusive | 2 | 2962/156 | 3 | unpriced |
| `incident_response:missing` | INCONCLUSIVE, WATCH | INCONCLUSIVE | REJECTED_BY_VERIFIER | False | agree | 2 | 2642/372 | 5 | unpriced |
| `classification_tagging:normal` | UNCLASSIFIED | UNCLASSIFIED | SUBSTANTIVE | True | agree | 2 | 2229/465 | 4 | unpriced |
| `classification_tagging:abnormal` | INTEGRITY | INCONCLUSIVE | INCONCLUSIVE | False | inconclusive | 2 | 2271/157 | 6 | unpriced |
| `classification_tagging:missing` | INCONCLUSIVE | INCONCLUSIVE | INCONCLUSIVE | False | agree | 2 | 2227/157 | 3 | unpriced |
| `risk_mitigation:normal` | LOW | LOW | SUBSTANTIVE | True | agree | 2 | 2599/512 | 3 | unpriced |
| `risk_mitigation:abnormal` | EMERGING, MATERIAL | INCONCLUSIVE | INCONCLUSIVE | False | inconclusive | 2 | 2643/157 | 3 | unpriced |
| `risk_mitigation:missing` | INCONCLUSIVE | INCONCLUSIVE | REJECTED_BY_VERIFIER | False | agree | 2 | 2577/458 | 5 | unpriced |
| `governance_intelligence:normal` | NO_PATTERN | NO_PATTERN | SUBSTANTIVE | True | agree | 2 | 2020/280 | 4 | unpriced |
| `governance_intelligence:abnormal` | RECURRING, CONCENTRATED | INCONCLUSIVE | REJECTED_BY_VERIFIER | False | inconclusive | 2 | 2021/280 | 3 | unpriced |
| `governance_intelligence:missing` | INCONCLUSIVE | INCONCLUSIVE | INCONCLUSIVE | False | agree | 2 | 1880/148 | 3 | unpriced |
| `workload_optimization:normal` | EFFICIENT | EFFICIENT | SUBSTANTIVE | True | agree | 2 | 2194/476 | 4 | unpriced |
| `workload_optimization:abnormal` | REDUNDANT | EFFICIENT | SUBSTANTIVE | True | missed_finding | 2 | 2207/425 | 3 | unpriced |
| `workload_optimization:missing` | INCONCLUSIVE | INCONCLUSIVE | REJECTED_BY_VERIFIER | False | agree | 2 | 2213/481 | 3 | unpriced |
| `knowledge_policy_management:normal` | CURRENT | CURRENT | SUBSTANTIVE | True | agree | 2 | 1903/287 | 3 | unpriced |
| `knowledge_policy_management:abnormal` | CONFLICT | INCONCLUSIVE | REJECTED_BY_VERIFIER | False | inconclusive | 2 | 1942/287 | 3 | unpriced |
| `knowledge_policy_management:missing` | INCONCLUSIVE | INCONCLUSIVE | INCONCLUSIVE | False | agree | 2 | 1911/150 | 3 | unpriced |
| `governance_architecture:normal` | ADEQUATE | ADEQUATE | SUBSTANTIVE | True | agree | 2 | 3815/340 | 3 | unpriced |
| `governance_architecture:abnormal` | GAP | INCONCLUSIVE | REJECTED_BY_VERIFIER | False | inconclusive | 2 | 3828/340 | 4 | unpriced |
| `governance_architecture:missing` | INCONCLUSIVE | INCONCLUSIVE | INCONCLUSIVE | False | agree | 2 | 3825/151 | 3 | unpriced |

### False positives, missed findings and inconclusive outcomes

- **False positives:** 0, offline. Live: not run.
- **Missed findings:** 1, offline. In `workload_optimization:abnormal`, the scripted reply said EFFICIENT and the rule baseline also reads EFFICIENT, so DS-03 cannot catch it. The case has six reads of one file and four identical lint runs, and those are visible only through `inspect_repeated_operations`. A real investigation is expected to request that tool. If it does not, the run will show it.
- **Inconclusive:** 10, offline.
  - 6 are abnormal cases where the generic script stops on attention states.
  - 1 is `context_memory:normal`, where the generic script cannot cite list fields.
  - 3 are abnormal cases that DS-03 rejected.

### Remaining blockers

1. **No API key** in this session's environment, so no live call can be made. It is added in Project settings, under Cloud environment.
2. **Budget and model** are waiting for your decision.
3. **Expected outcomes for two cases are judgement calls** and should be reviewed before the run:
   - `classification_tagging:abnormal` expects INTEGRITY, while the rule baseline reads BOUNDARY.
   - `governance_architecture:missing` expects INCONCLUSIVE on thin telemetry, while the baseline reads ADEQUATE.
4. **DS-03 is a semantic choice.** It makes the rule baseline a veto over a quiet model reading. Malhar should agree to it, alongside DS-01 (RUNTIME.md "Needs agreement", item 4).
5. **Carried over:** Malhar's contracts and verifier ids are unread, the schema migration has not been applied, and there are no real authoritative artifacts (RUNTIME.md items 1, 2, 6 and 7).
6. **Not pushed.** GitHub still shows `8a45418`. A push triggers a Vercel preview deploy.

## 6. Commands to reproduce

All commands run from the repository root unless they start with `cd collector`.

```
pip install -r collector/requirements-cops-runtime.txt
pip install anthropic==2.3.0                         # needed for tests/test_runtime_live_path.py (skipped without it)

python3 -m pytest -p no:cacheprovider tests/ -q -rs  # 243 passed, 1 skipped
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

- Model judgement is untested. Every finding attributed to an LLM COP comes from a scripted reply.
- Each live case runs the target COP with deterministic upstreams only. LLM upstreams are left out, so, for example, Incident Response runs without Anomaly Detection's finding. Their absence is recorded in the consumption log.
- Verifiers check that claims are true, not that a reading is complete. DS-03 narrows this gap but does not close it.
- The structured-output schema has been checked against the documented limits and a stub client, but not against the API itself. A rejection on the first live call would be recorded as `llm_unavailable` with the API's message.
- Coverage figures come from this sandbox.
