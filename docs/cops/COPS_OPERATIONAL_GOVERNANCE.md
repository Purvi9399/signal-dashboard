# COPS operational governance: referrals, sandbox actions, feedback, scenario, panel

Branch `feat/cops-langgraph-runtime`, local only. Eight commits on top of `2334ad0` (VS-01), none pushed or merged.
No paid model call was made. Every model reply in this work is scripted (labelled simulated), and every action
goes to a local test target.

## 1. Where the work started

- Latest local commit before this work: `2334ad0` (VS-01, graded verification support) on `4ae496e` (graded triage), on `main` at `acd767d`. Neither commit is on the remote, which still has the branch at `c7a062c`. The November 4 bench was not the latest state.
- `triage.ReferralRouter` opened referral contracts but nothing executed them. There was no action gateway beyond `graph.MockEnforcementGateway`. Re-running a case overwrote reviewer feedback in memory.

## 2. Results at a glance

| Variant | Referrals (root) | VS-01 support, initial → revised | Resolution | Final route | Action | Gateway outcome | Review | Eligible |
|---|---|---|---|---|---|---|---|---|
| successful | RM answered, BA answered | 0.6909 → 0.4727 (SUPPORTED both) | CONFIRMED | autonomous | ALERT | delivered and verified | CONFIRMED | yes; memory VALIDATED |
| contradictory | RM answered, BA no finding | 0.4389 → 0.0857 (NORMAL provisional → INCONCLUSIVE) | REVISED | assisted | review task | task row verified; nothing delivered | REFUTED | no (finding not verified) |
| missing_evidence | RM answered, BA answered | 0 → 0 (unknown 1.0 → 1.0) | UNRESOLVED | assisted | review task | task row verified | CONFIRMED | no (finding not verified) |
| timeout | RM timed out, BA answered | 0.6909 → 0.6909 (late answer discarded) | UNCHANGED | human | ALERT after approval | approved → delivered → verified | CONFIRMED | yes; memory VALIDATED |
| denied_approval | RM answered, BA answered | 0.6909 → 0.4727 | CONFIRMED | human | ALERT (approval required) | approval denied; nothing delivered | REFUTED | yes; memory REJECTED |

All five pass in `tests/test_runtime_scenario.py`. Full suite: **445 passed, 1 skipped** (`python3 -m pytest tests/`).
Bench re-run offline with the recorded replies: unchanged at **48 PASS / 1 FAIL / 5 INCONCLUSIVE**, fleet F1 PASS / F2 FAIL, tiers 33 / 18 / 0 / 3.

## 3. Files changed (`git diff --stat 2334ad0..HEAD`)

| File | Change | Milestone |
|---|---|---|
| `collector/cops/runtime/referrals.py` | new: typed referral request and response, the investigation graph, RS-01..RS-05, limits | 1 |
| `collector/cops/runtime/support.py` | `assess()` now names the assumptions it makes (`_assumptions`: telemetry reliability when RM is not verified). No numeric change | 1 |
| `collector/cops/runtime/fixtures.py` | `Script(cycle=True)`: a re-run COP replays its scripted steps | 1 |
| `collector/cops/runtime/gateway.py` | new: `SandboxGateway`, `GatewayStore`, `TestAlertEndpoint`, `MockToolRunner`, `open_review_task` | 2, 4 |
| `collector/cops/runtime/graph.py` | `await_approval` calls the gateway's approval hook; `execute_action` hands authorised ALERTs to the gateway outside dry-run; new `resume_cop`; `EXECUTED_SANDBOX` status | 2 |
| `collector/cops/runtime/contracts.py`, `actions.py` | `EXECUTED_SANDBOX` and its lifecycle stage | 2 |
| `collector/cops/runtime/memory.py` | a re-observed record is kept, not replaced; `independent_reviewer()`; `record_feedback` refuses non-independent reviewers | 3 |
| `collector/cops/runtime/outcomes.py` | new: `OutcomeLedger` (insert-once execution outcomes and reviews) | 3 |
| `collector/cops/runtime/scenario.py` | new: the integrated coding-agent scenario and its trace writer | 4 |
| `docs/cops/evaluation/scenario/*.json` | the five traces, written by `python3 -m cops.runtime.scenario` | 4, 5 |
| `src/pages/CopInvestigations.jsx`, `src/App.jsx` | new dashboard page reading those traces | 5 |
| `tests/test_runtime_referrals.py` (14), `test_runtime_gateway.py` (12), `test_runtime_outcomes.py` (10), `test_runtime_scenario.py` (12) | new tests | 1-5 |

## 4. What executes and what is simulated

| Stage | Status |
|---|---|
| Coding agent | **Simulated.** `scenario.agent_events` is a synthetic event list; no agent runs |
| Telemetry | Executes: `synthetic.Session` builds observable rows from those events; the contradictory variant drops the 5 failing calls at ingestion |
| COP runs | Execute: every run goes through the LangGraph COP subgraph (`graph.run_cop`) |
| Anomaly Detection and Workflow Orchestration model replies | **Simulated**: scripted `FixtureProvider` steps. Each step reads only the payload the gateway sent it |
| Runtime Monitoring and Behaviour Analytics | Execute for real (deterministic COPs) |
| Tools, independent verification (VS-01, SC/DS/AU checks), triage, authority | Execute |
| Referral graph, RS-01..RS-05, limits | Execute (`referrals.investigation_graph`) |
| ALERT delivery | Executes against a **loopback** HTTP server started for the run (`TestAlertEndpoint`); `_http` refuses any non-loopback host |
| Approvals, review tasks, holds | Execute against a local SQLite file (`GatewayStore`) and a mock tool runner (`MockToolRunner`) |
| Reviewer | **Simulated**: a named test identity answering in code |
| Timeout | The clock is **injected**: RM's run is made to read 1000 s. Nothing actually waits |
| Production systems, external enforcement, credentials | None connected |


## 5. One complete end-to-end trace (variant `successful`)

Source: `docs/cops/evaluation/scenario/successful.json`, written by the scenario code and checked by
`test_saved_traces_are_these_runs`. Times are UTC on 2026-11-11. Investigation stages carry the timestamps of the
investigation's own event log. Decision ids are deterministic per run.

| # | time (UTC) | stage | detail |
|---|---|---|---|
| 1 | 10:40:11.036 | agent_event | event=session_start, session_id=ca1 |
| 2 | 10:40:11.036 | agent_event | event=user_prompt, objective=make tests/test_api.py pass |
| 3 | 10:40:11.036 | agent_event | event=model_usage, total_tokens=40000 |
| 4 | 10:40:11.036 | agent_event | event=tool_call, tool=Read |
| 5 | 10:40:11.036 | agent_event | event=tool_call, tool=Edit |
| 6 | 10:40:11.036 | agent_event | event=tool_call, tool=Bash, command=pytest tests/test_api.py, tool_use_id=ca1-b0, exit_code=1, success=False, duration_ms=900 |
| 7 | 10:40:11.036 | agent_event | event=tool_call, tool=Bash, command=pytest tests/test_api.py, tool_use_id=ca1-b1, exit_code=1, success=False, duration_ms=900 |
| 8 | 10:40:11.036 | agent_event | event=tool_call, tool=Bash, command=pytest tests/test_api.py, tool_use_id=ca1-b2, exit_code=1, success=False, duration_ms=900 |
| 9 | 10:40:11.036 | agent_event | event=tool_call, tool=Bash, command=pytest tests/test_api.py, tool_use_id=ca1-b3, exit_code=1, success=False, duration_ms=900 |
| 10 | 10:40:11.036 | agent_event | event=tool_call, tool=Bash, command=pytest tests/test_api.py, tool_use_id=ca1-b4, exit_code=1, success=False, duration_ms=900 |
| 11 | 10:40:11.036 | agent_event | event=session_end |
| 12 | 10:40:11.036 | telemetry_ingested | collectors ["otel"]; rows_for_session 10; baseline_sessions 7; tokens_reported 40000 |
| 13 | 10:40:11.139 | initial_finding | cop "anomaly_detection"; label "ANOMALOUS"; status "SUBSTANTIVE"; verified true; decision_id "9fc313ee-1c27-5308-aefc-f86b47410b89" |
| 14 | 10:40:11.139 | vs01_initial | decision "SUPPORTED"; support 0.6909; against 0; unknown 0.3091; telemetry_reliability 0.9; assumptions ["telemetry_reliability"] |
| 15 | 10:40:11.139 | handling_quotient_initial | quotient 0.895; tier "autonomous"; selected_action "ALERT" |
| 16 | 10:40:11.140 | request_for_support | anomaly_detection → runtime_monitoring; reason telemetry_reliability assumed; depth 1; round 1 |
| 17 | 10:40:11.140 | request_for_support | anomaly_detection → behavior_analytics; reason upstream:behavior_analytics unknown; depth 1; round 1 |
| 18 | 10:40:11.141 | receiver_investigating | anomaly_detection → runtime_monitoring; depth 1 |
| 19 | 10:40:11.180 | receiver_answered | runtime_monitoring; status ANSWERED_VERIFIED; elapsed_s 0.039; delivered_as evidence; RS-01:PASS RS-02:PASS RS-03:PASS RS-04:PASS RS-05:PASS |
| 20 | 10:40:11.180 | referral_response | receiver "runtime_monitoring"; status "ANSWERED_VERIFIED"; admissible true; delivered_as "evidence"; label "INSUFFICIENT"; receiver_decision_id "699e2714-ce63-5d2c-87fd-71d5c99387ed" |
| 21 | 10:40:11.180 | receiver_investigating | anomaly_detection → behavior_analytics; depth 1 |
| 22 | 10:40:11.214 | request_for_support | behavior_analytics → workflow_orchestration; reason upstream:workflow_orchestration unknown; depth 2; round 1 |
| 23 | 10:40:11.214 | referral_not_opened | behavior_analytics → context_memory; reason context_memory is outside the referral scope enabled for milestone 1 |
| 24 | 10:40:11.214 | receiver_investigating | behavior_analytics → workflow_orchestration; depth 2 |
| 25 | 10:40:11.268 | receiver_answered | workflow_orchestration; status ANSWERED_VERIFIED; elapsed_s 0.053; delivered_as evidence; RS-01:PASS RS-02:PASS RS-03:PASS RS-04:PASS RS-05:PASS |
| 26 | 10:40:11.268 | reassessment_started | behavior_analytics; round 1 |
| 27 | 10:40:11.305 | receiver_answered | behavior_analytics; status ANSWERED_VERIFIED; elapsed_s 0.125; delivered_as evidence; RS-01:PASS RS-02:PASS RS-03:PASS RS-04:PASS RS-05:PASS |
| 28 | 10:40:11.305 | referral_response | receiver "behavior_analytics"; status "ANSWERED_VERIFIED"; admissible true; delivered_as "evidence"; label "POOR_RECOVERY"; receiver_decision_id "1bacfea1-9667-54c2-b2f9-0c227559d9a6" |
| 29 | 10:40:11.306 | reassessment_started | anomaly_detection; round 1 |
| 30 | 10:40:11.364 | revised_evidence_score | round 1; decision "SUPPORTED"; support 0.4727; against 0; unknown 0.5273; telemetry_reliability 0.3; assumptions []; quotient 0.887; tier "autonomous"; selected_action "ALERT"; resolution "CONFIRMED" |
| 31 | 10:40:11.366 | investigation_final | tier "autonomous"; selected_action "ALERT"; resolution "CONFIRMED"; human_task null |
| 32 | 10:40:11.492 | final_action | typed_action "ALERT"; requires_approval false; exec_decision_id "fbccbcf0-b2f1-5439-b173-0676692764df"; verification_support "SUPPORTED"; support 0.4727 |
| 33 | 10:40:11.492 | authority_check | authorised true; execution_status "NOT_EXECUTED_NO_ACTION"; AU-01 PASS, PE-01 NOT_RUN |
| 34 | 10:40:11.494 | action_execution | execution_status "EXECUTED_SANDBOX"; gateway_action_id "532942f8-fe97-5fd3-b63d-e4625c2474f4"; gateway_history ["PROPOSED", "AUTHORISED", "DELIVERED", "APPLIED", "VERIFIED"]; endpoint_received 1 |
| 35 | 10:40:11.495 | outcome_confirmation | gateway_state "VERIFIED"; observed "endpoint returned 200 for ack df53fde9-1cac-45a8-9447-241131f784d0"; alerts_at_endpoint ["532942f8-fe97-5fd3-b63d-e4625c2474f4"] |
| 36 | 10:40:11.500 | feedback_persisted | review_entry {"feedback_id": "2a330232-ffd1-5f15-8967-dd259d09ebe1", "outcome": "CONFIRMED", "source": "reviewer@tenant-a.example", "independent": true, "eligible_for_precedent": true, "eligible_for_scoring": true, "applied_to_memory": true}; memory_record_id "59c7af4a-64cf-55f0-b7fe-db454bd89813" |
| 37 | 10:40:11.502 | restart_check | ledger [["execution_outcome", "VERIFIED"], ["review", "CONFIRMED"]]; gateway_history ["PROPOSED", "AUTHORISED", "DELIVERED", "APPLIED", "VERIFIED"]; memory_validation_status "VALIDATED" |

Two things to read correctly in this trace:
- Row 33 shows `execution_status NOT_EXECUTED_NO_ACTION` at authority time. That is because ALERT is informational in the authority table, so the graph's `execute_action` hands it to the sandbox gateway. Row 34 is the gateway carrying it out.
- RM was not asked again by BA (row 22 onward). Its verified answer was already in the tree and was passed to BA as upstream, not re-run.

## 6. Verification support before and after

The revised score comes from a second run of the same COP with the admitted answers as upstream. Nothing is copied from the first run; `grade_and_route` recomputes the quotient and route.

| Variant | Component | Initial | Revised | Why |
|---|---|---|---|---|
| successful | deviation_metrics | supports 0.3273 | supports 0.1091 | RM verified telemetry INSUFFICIENT: reliability 0.9 (assumed) → 0.3 (observed) scales the metric evidence |
| successful | upstream:behavior_analytics | unknown | neutral | BA's POOR_RECOVERY shares the `insights.retry` lineage with AD: same signal, not corroboration |
| successful | totals | support 0.6909 / against 0 / unknown 0.3091 | 0.4727 / 0 / 0.5273 | still SUPPORTED; quotient 0.895 → 0.887 |
| contradictory | deviation_metrics | supports 0.2571 | supports 0.0857 | same reliability change |
| contradictory | rule_baseline | supports 0.1818 | unknown 0 | the rule baseline has no definite reading once telemetry is INSUFFICIENT |
| contradictory | totals | 0.4389 / 0 / 0.5611 NORMAL provisional | 0.0857 / 0 / 0.9143 INCONCLUSIVE | quotient 0.807 → 0.504, autonomous → assisted |
| missing_evidence | all | unknown | unknown | no comparable sessions; support 0, unknown 1.0 before and after |
| timeout | all but BA | unchanged | unchanged | RM's answer arrived after the 900 s limit and was not delivered |

## 7. How a COP handoff works

1. **Need.** After the initiator's run, `referrals.needs()` reads its VS-01 record. It collects unknown upstream components, assumptions (for example `telemetry_reliability` assumed when RM is absent) and the triage referral. A SUPPORTED finding with a quiet action is not referred at the root.
2. **Request.** `build_request()` makes a typed `ReferralRequest`. It holds the question, the original finding with its support/against/unknown, the missing evidence, trace ids (finding, decision, tool calls, model invocations) and the permitted scope. The scope covers receivers from `CONSUMES`, the receiver's tool allowlist, `mode dry_run`, `may_execute_actions False`, depth, the remaining budget, the timeout and the memory policy. Its `referral_id` is `stable_id(investigation, initiator, receiver, round)`.
3. **Checks before running, in order:** cycle (receiver already in the chain), scope, reuse of a verified answer, reuse of an earlier failure, depth (`MAX_DEPTH 2`), cost (`COST_LIMIT_USD 0.10` for the tree), and runs (`MAX_RUNS 12`).
4. **Execution.** `dispatch_one()` calls `investigate()` for the receiver. That is the same LangGraph investigation graph one level deeper, so a receiver can refer onward on the initiator's behalf, within depth. Each COP run is `graph.run_cop` with its own `run_id` and `thread_id` and a budget capped at what is left. A crash is retried once (`MAX_RETRIES 1`); a spent budget or a timeout is not.
5. **Verifying the response** (`verify_response`):
   - RS-01: the identity matches.
   - RS-02: the answer is verified and SUBSTANTIVE.
   - RS-03: every evidence ref resolves in the receiver's run.
   - RS-04: tools were within the allowlist, the run was dry-run, and nothing was executed.
   - RS-05: time and cost were within limits.

   Only an answer that passes all five is delivered as `evidence`. Otherwise it is `context_only` or `not_delivered`. `_deliverable()` never raises `verified`, so an unverified COP output can never become authoritative evidence.
6. **Reassess.** If something verified was delivered, the initiator runs again with it as upstream. The run logs initial and revised support/against/unknown, quotient, tier and action (`reassessment_completed`). At most `MAX_ROUNDS 2`.
7. **Resolution** (`resolution()`):
   - REVISED: the label or decision changed.
   - CONFIRMED: support stands and the unknown shrank or an assumption was replaced by an observation.
   - UNCHANGED.
   - UNRESOLVED: no admissible answer came back, or answers came back but the reading is still INCONCLUSIVE or INSUFFICIENT.

   A limit hit sends the case to a human (`referral_limit`). Uncertainty that remains after referrals goes to assisted, or stays human, with an `unresolved_uncertainty` task. Nothing is retried after that.

## 8. How each action is delivered and confirmed

| Action | Path | States | Confirmation |
|---|---|---|---|
| ALERT, no approval | `execute_action` → `gateway.submit` (re-checks authority with `check_typed`, needs a verified finding) → `carry_out` → `deliver_alert` | PROPOSED → AUTHORISED → DELIVERED → APPLIED → VERIFIED | `verify()` GETs the alert back from the endpoint by ack id and checks finding and action ids |
| ALERT with approval | `await_approval` calls `on_approval_requested`, which writes a persistent PENDING row and interrupts. A named reviewer calls `decide()`, then `resume_cop`. `execute_action` calls `gateway.execute` → `proceed_after_approval` | … → AWAITING_APPROVAL → AUTHORISED → DELIVERED → APPLIED → VERIFIED, or → APPROVAL_DENIED | same read-back. Denial: nothing delivered, and the endpoint log is empty |
| REQUEST_APPROVAL | persistent row in `gateway_approvals` | → DELIVERED → APPLIED → VERIFIED | the row is read on a fresh SQLite connection |
| HOLD_TOOL_CALL | `apply_hold` writes a HELD row. The mock runner asks `gate()` before running a call | → DELIVERED → APPLIED → VERIFIED, then RELEASED or CANCELLED | the runner reports the call HELD and not executed. Release runs it; denial cancels it. A hold on a call that already ran is FAILED |
| Review task (new) | `open_review_task`: the runtime's escalation when a finding stays uncertain. It is not a COP action and uses no COP authority | PROPOSED → AUTHORISED → DELIVERED → APPLIED → VERIFIED | fresh-connection read of the PENDING task row |

Authority is still decided in the graph (`authority_check`, AU-01 and PE-01) and re-checked by the gateway. Approvals, holds and feedback share one reviewer rule (`memory.independent_reviewer`): a COP (by `cop:` prefix or bare name), the gateway, the system, the runtime or an automated identity cannot approve, end a hold or validate memory. An approval that expires is a denial. HOLD_TOOL_CALL is shown in the gateway tests (via Policy Enforcement), not in the scenario, because Anomaly Detection has no hold authority. Expiry is checked only when `decide()` or `expire()` is called; there is no background timer.

## 9. Feedback and memory (milestone 3)

- **The overwrite defect is fixed.** `MemoryGateway` now keeps an existing record when a case is re-run: provenance gets `re_observed` and the log says `KEPT`. VALIDATED and REJECTED survive re-runs (`test_rerunning_a_case_keeps_reviewer_feedback`, `..._does_not_resurrect_a_rejected_record`).
- **Ledger entries are written once.** `OutcomeLedger` sits in the same SQLite file, and each entry's id is a content id. Execution outcomes are never eligible for precedent or scoring.
- **Eligibility rule.** A review is eligible only when the reviewer is independent and the finding verified. Only eligible reviews reach `record_feedback`, the single route to VALIDATED or REJECTED. The contradictory and missing-evidence variants show a review that was kept but not eligible.
- **Restart check.** Each scenario reopens both stores at the end. The milestone 3 test also shows that a later, separate investigation retrieves the validated record as established precedent.

## 10. The dashboard panel (milestone 5)

`src/pages/CopInvestigations.jsx`, under the new **COP investigations** nav item. It shows:
- the handoff graph and table;
- initial and revised VS-01 bars with component changes;
- the quotient and route (initial, revised, final);
- the action, authority, approval and gateway state strip;
- the outcome and feedback;
- the event timeline.

Every value is read from `docs/cops/evaluation/scenario/*.json` with `import.meta.glob`. Those files are written by the scenario code. A test fails if their summaries or stage order differ from a fresh run. `vite build` passes. I checked the page in a local preview only, since a push would trigger a Vercel deploy.

## 11. Tests

| File | Result |
|---|---|
| `tests/test_runtime_referrals.py` | 14 passed |
| `tests/test_runtime_gateway.py` | 12 passed |
| `tests/test_runtime_outcomes.py` | 10 passed |
| `tests/test_runtime_scenario.py` | 12 passed |
| whole suite | 445 passed, 1 skipped, 0 failed |
| bench (`python3 -m cops.runtime.test_bench`, recorded replies, no calls) | 48 / 1 / 5, unchanged. The 1 FAIL (`governance_architecture:normal`) and fleet F2 FAIL predate this work |

## 12. Remaining architecture and security gaps

1. **The case graph still publishes findings without `verification_support`.** In `run_case`, a downstream COP therefore always reads upstream reliability as 0.9. The referral path delivers it, so the two paths can score the same evidence differently. This needs a fix in `persist_and_publish`.
2. **The timeout is checked when a receiver returns, not enforced pre-emptively.** A hung receiver would block. A real deployment needs a worker with a hard deadline.
3. **The cost limit can overshoot by one model call.** The budget is checked between calls.
4. **RS-03 accepts `memory:` refs without resolving them** against the store.
5. **`cop_outcome_feedback` and the gateway tables are SQLite only.** They are not in the Supabase schema (`persistence.TABLES`), so a production store would lose them.
6. **HOLD fails closed while its approval is pending.** The call waits (`gate()` returns `waiting`). That is my choice and needs your agreement.
7. **Referral scope covers RM, BA and WO only** (`SCOPE_RECEIVERS`). Context Memory and the other COPs are logged as `referral_not_opened`.
8. **Reviewer identity is a string.** Nothing authenticates a reviewer; `independent_reviewer()` only rejects COP, system and gateway names. Real use needs signed reviewer identities and a separation-of-duties check against who raised the finding.
9. **Approval expiry has no scheduler.** It is enforced only when `decide()` or `expire()` runs.
10. **The successful variant still ALERTs autonomously on INSUFFICIENT telemetry** (support 0.47, SUPPORTED). That follows the current VS-01 thresholds. Whether incomplete telemetry should cap the route at assisted is a policy question for you.
11. **The scenario's act phase is a second COP run.** It is deterministic here because replies are scripted. With a live model, the act-phase run could disagree with the investigation's last reassessment. It should then reuse that decision rather than re-ask the model.
12. **All model behaviour in the scenario is scripted.** A live run of the scenario needs approval and a key in the environment settings.
