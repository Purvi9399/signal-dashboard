# COPS graded verification and triage

Branch `feat/cops-langgraph-runtime`, local commit, not pushed. Date: 2026-11-04.
Scope: Sundar's redesign, Phases 1–10. Every number below comes from a real run on this branch:
`python -m cops.runtime.test_bench` (offline: deterministic COPs plus replayed recorded model replies, **no paid calls**)
and `python -m pytest` (371 passed, 1 skipped). Results under "live" are the recorded replies from the approved earlier live run, replayed, not new calls.

> **Update, 2026-11-04 (later).** The 1–5 evidence-support score described below is now the *band* of the graded verification-support decision; it is no longer computed separately. That decision is made inside verification by check VS-01. The handling quotient, routing and taxonomy are unchanged. See [COPS_VERIFICATION_SUPPORT.md](COPS_VERIFICATION_SUPPORT.md).

## Summary

- Verification is now graded. Each finding carries four separate fields: the **outcome** (PASS / FAIL / INCONCLUSIVE), an **evidence-support score** (1–5, deterministic), a **confidence** (0–1, capped by the score) and a **perceived handling quotient** (0–1). Each field records its basis.
- Routing to autonomous / assisted / specialist / human is deterministic. It uses thresholds set per COP, and policy, authority and integrity rules always take precedence over the quotient.
- The independent verifiers, evidence contracts, tools, memory policy and authority checks are unchanged. Triage reads their results and never overrides them. No label, status or claim is changed by triage.
- Bench verdicts are unchanged at 48 PASS / 1 FAIL / 5 INCONCLUSIVE. Fleet results are unchanged: F1 PASS, F2 FAIL.
- What changed is **what happens next**:
  - The reflex WATCH on INCONCLUSIVE dropped from 19 cases to 0.
  - No action is taken on a PASS.
  - The final action changed in 23 of the 54 cases.
- The observed tier distribution is 61.1% autonomous, 33.3% assisted, 0% specialist and 5.6% human, against the 80 / 10 / 7 / 3 planning target. Nothing was assigned by quota. Section 10 explains why the observed split differs from the target.

## 1. Files changed

| File | Change |
|---|---|
| `collector/cops/runtime/triage.py` (new) | Contains: score (`evidence_support`), `contradictions`, `confidence_basis`, `outcome_of`, `handling_quotient`, the `THRESHOLDS` registry, `classify` (routing and action selection), the `SPECIALISTS` map, `ReferralContract` / `ReferralRouter`, and `distribution` |
| `collector/cops/authority.py` | Adds the typed action taxonomy (`ActionSpec`, `TAXONOMY`, `LEGACY_TO_TYPED`, `UNIVERSAL`, `GRANTS`, `typed`, `legacy`, `permitted`, `typed_permitted`, `needs_approval`, `typed_matrix` and `check_typed`). `check()` and the legacy matrix are unchanged |
| `collector/cops/runtime/graph.py` | New `grade_and_route` node between `independent_verify` / `plan_recovery` and `authority_check`. `authority_check` now authorises the triage-selected typed action. The receipt, decision and persisted finding carry `triage`. Execution feedback is recorded in `_feedback` |
| `collector/cops/runtime/contracts.py` | `CONTRACT_VERSION` is now `cops-runtime/2`. Adds `OUTCOMES`, `ROUTING_TIERS`, `CANDIDATE_VERIFICATION` and the dataclasses `EvidenceSupport`, `ConfidenceBasis`, `HandlingQuotient`, `Routing` and `TriageRecord` |
| `collector/cops/runtime/actions.py` | `DryRunAdapter.record` writes the typed action, the "would produce" result, the referral task and the human task. The lifecycle gains `final_typed_action` |
| `collector/cops/runtime/verifiers.py` | AU-01 and the action verifiers use typed permission. PE-01 accepts a fail-closed `HOLD_TOOL_CALL` when no policy is available, and also applies to typed `REQUEST_APPROVAL` |
| `collector/cops/runtime/evaluation.py` | C01 and C06 are restated by intent (see section 9) |
| `collector/cops/runtime/test_bench.py`, `bench_report.py` | Triage summary, before/after against the pre-triage snapshot, and the new dashboard section |
| `tests/test_runtime_triage.py` (new) | 34 tests: unit tests, scenarios s01–s12, and a contract round-trip test |
| `tests/test_runtime_{actions,graph,live_path,verifiers}.py` | Restated expectations (section 9) |
| `docs/cops/evaluation/test_bench_results_before_triage.json`, `COPS_TEST_BENCH_before_triage.html` (new) | Pre-change snapshot, kept for comparison. `live_results.json` and `*_before_fixes.json` are untouched |
| `docs/cops/evaluation/test_bench_results.json`, `COPS_TEST_BENCH.html` | Regenerated from this run |
| `docs/cops/RUNTIME.md` | Canonical node sequence, the triage contract and the taxonomy |

## 2. Formula and rationale

### Step sequence (Phase 7), as run by the graph

| # | Step | Where |
|---|---|---|
| 1 | Collect evidence and tool results | `load_inputs` … `validate_tool_result` (unchanged) |
| 2 | Independent verification | `independent_verify` (unchanged registry) |
| 3 | Bounded recovery | `plan_recovery` (unchanged) |
| 4 | Contradiction classification | `triage.contradictions` |
| 5 | Evidence-support score | `triage.evidence_support` |
| 6 | Outcome P/F/I | `triage.outcome_of` |
| 7 | Confidence | `triage.confidence_basis` |
| 8 | Handling quotient | `triage.handling_quotient` |
| 9 | Routing and action selection | `triage.classify` |
| 10 | Authority, policy and approval | `authority_check` → `check_typed`, `await_approval` (unchanged gates) |
| 11 | Execute (dry-run) and receipt | `execute_action` → `DryRunAdapter.record` |
| 12 | Outcome feedback and record | `_feedback`, `record_decision`, `validate_memory_update`, `persist_and_publish` |

Steps 4–9 run in the new node `grade_and_route`. A lower threshold can only change the tier. It can never skip step 10: every selected action still goes through `check_typed`, approval and policy.

### Score (1–5): `triage.evidence_support`

This is deterministic and computed from the verifier results and the evidence-sufficiency check. The model never sets it.

| Situation | Score |
|---|---|
| Integrity failure (EV-xx / ID-xx FAIL) | 1 |
| Factual contradiction (MC-01, DS-03, or a total SC-01 mismatch) | 1. A partial SC-01 mismatch scores 2 |
| State failure (SC-02 / DS-01 / DS-02) | 1–2 |
| No candidate, required evidence unavailable, or status MISSING_DEPENDENCY / FAILED | 1, else 2 |
| Verified candidate | Base 3. +1 if corroborated by at least 2 independent kinds (metric, record, upstream, memory). +1 if there is no material limitation, held at 4 if only minor limitations remain. −1 for an interpretive contradiction (RP-01 WARN). Clamped to 2–5 |

- **Material limitations:** a missing REQUIRED or METRIC item, a WARN verifier result, or an evidence ceiling below 0.9.
- **Minor limitations:** a missing upstream finding, or a gap the investigation names itself.
- A score of 3 or less is marked `provisional`. The finding stands, and its limitations are listed in `limiting`.

The rules from Phase 2 are enforced in code and tests:

- **A numerical contradiction is a factual contradiction.** It scores 1–2, never more (`test_a_value_contradiction_scores_1_or_2_never_more`).
- **A missing policy cannot be replaced by confidence.** Confidence is 0 without a definite verified reading, and the action becomes REQUEST_CONTEXT or HOLD_TOOL_CALL (s06).
- **A high score never overrides a failed policy, authority, provenance or integrity check.** Integrity scores 1. AU-01 and `check_typed` still run after triage (s10).
- **Contradictions affect both the score and the routing, without forcing INCONCLUSIVE.** An interpretive contradiction lowers the score by 1 and halves `contradiction_free`, and the outcome stays as verified (s03).

### Confidence (0–1): `triage.confidence_basis`

- The value is `min(raw, cap[score])`, with caps 1: 0.15, 2: 0.40, 3: 0.65, 4: 0.85, 5: 0.95. It is 0 when there is no definite verified reading.
- The raw value is the gated finding confidence.
- The model's own label and its self-reported confidence are stored separately in `llm_assessment` and do not enter the score or the quotient (`test_quotient_is_not_the_model_confidence`).
- Calibration is recorded as "uncalibrated: no validated outcome history". It must not be read as a probability until outcomes are validated.

## 3. The 18 threshold configurations (`triage.THRESHOLDS`, version `thresholds-1`)

The tier is set by the quotient, read against three bars per COP. A quotient at or above the autonomous bar routes to autonomous. At or above the assisted bar it routes to assisted. At or above the specialist bar it routes to the specialist. Below that, the band is reported as "human", but routing still sends the case to the specialist first (section 6).

How the bars were set:
- The bars rise with what a wrong call costs. COPs that intervene or escalate (Policy, Incident, Risk) have the highest bars.
- Observation-only COPs (Context Memory, Behavior, Governance Intelligence, and Classification, whose only permitted action is NONE) have the lowest bars.
- The "human regardless" column is applied before the quotient is considered.
- `test_every_cop_has_its_own_thresholds_and_a_justification` and `test_the_same_quotient_routes_differently_by_cop` cover this.

| COP | autonomous ≥ | assisted ≥ | specialist ≥ | human regardless of quotient | justification |
|---|---|---|---|---|---|
| `runtime_monitoring` | 0.8 | 0.6 | 0.35 | – | its reading caps every other COP's evidence ceiling; default bands |
| `workflow_orchestration` | 0.78 | 0.58 | 0.35 | – | guidance actions are reversible and approval-gated when supervised; slightly lower autonomous bar |
| `context_memory` | 0.75 | 0.55 | 0.3 | – | observational: WATCH is its strongest action |
| `interaction_discovery` | 0.8 | 0.6 | 0.35 | – | route findings feed policy and risk; default bands |
| `agent_lifecycle_management` | 0.82 | 0.62 | 0.38 | – | identity and configuration drift feed policy enforcement; a wrong reading propagates |
| `behavior_analytics` | 0.75 | 0.55 | 0.3 | – | pattern characterisation only; WATCH is its strongest action |
| `anomaly_detection` | 0.8 | 0.6 | 0.35 | – | ALERT on anomalies; default bands |
| `resource_management` | 0.8 | 0.6 | 0.35 | – | ALERT on entitlement breaches; default bands |
| `workload_optimization` | 0.78 | 0.58 | 0.35 | – | efficiency advice is reversible |
| `policy_enforcement` | 0.85 | 0.65 | 0.4 | VIOLATION | owns permission; its actions intervene. Highest bar |
| `classification_tagging` | 0.72 | 0.52 | 0.3 | – | tags only; its one permitted action is NONE |
| `recovery_resilience` | 0.8 | 0.6 | 0.35 | IRREVERSIBLE | restoration checks; irreversible outcomes go to a human |
| `incident_response` | 0.85 | 0.65 | 0.4 | INCIDENT | decides containment; a confirmed incident needs an accountable human |
| `risk_mitigation` | 0.85 | 0.65 | 0.4 | MATERIAL | residual-risk calls drive escalation |
| `audit_traceability` | 0.82 | 0.62 | 0.38 | – | attribution errors are hard to undo in an audit record |
| `governance_intelligence` | 0.75 | 0.55 | 0.3 | – | aggregate patterns; reviews, not interventions |
| `knowledge_policy_management` | 0.82 | 0.62 | 0.38 | – | policy precedence affects every enforcement |
| `governance_architecture` | 0.82 | 0.62 | 0.38 | STRUCTURAL_CHANGE_RECOMMENDED | structural changes to COP responsibilities need a human |


These values are **planning values**. They are set by reasoning about consequence, not fitted to outcomes (section 15).

## 4. Score derivation in this run

The rules are in section 2. Over the 54 cases the scores fall as follows:

| Score | Cases |
|---|---|
| 1 | 15 |
| 2 | 7 |
| 3 | 3 |
| 4 | 22 |
| 5 | 7 |

- Every INCONCLUSIVE case scores 1 or 2.
  - A score of 1 means required evidence is unavailable, or the candidate was disproved.
  - A score of 2 means a rejected but recoverable candidate, or missing upstream or telemetry support.
- PASS and FAIL cases score 3–5. A 5 needs corroboration from at least two independent evidence kinds and no limitation.
- The per-case score, confidence and quotient are listed in section 14. Every supporting and limiting item is in the case trace on the dashboard.

## 5. Quotient derivation

`perceived_handling_quotient = Σ weight × component`. Each component is between 0 and 1 and is read from checks that have already run. Nothing in it is self-reported by the COP. It measures whether the COP can handle this decision itself, which is separate from how risky the event is. Risk enters through human labels and approval rules, not through the quotient.

| Component | Weight | Value |
|---|---|---|
| evidence_strength | 0.25 | (score − 1) / 4 |
| completeness | 0.15 | Share of REQUIRED and METRIC items available |
| contradiction_free | 0.15 | none 1, interpretive 0.5, factual 0 |
| interpretability | 0.10 | Definite reading 1, indeterminate label 0.5, no reading 0.2 |
| tool_availability | 0.10 | Share of tools whose latest call returned OK |
| authority | 0.08 | Action permitted 1, permitted with approval 0.5, not permitted 0 |
| reversibility | 0.05 | Taxonomy: reversible 1, partly 0.5, irreversible 0 |
| upstream_reliability | 0.05 | Evidence ceiling × (0.5 + 0.5 × share of upstream verified) |
| validated_history | 0.03 | Validated memory agrees 1, disagrees 0.25, none 0.5 |
| expertise | 0.02 | 0.3 if a specialist trigger fired, else 1 |
| no_external_approval | 0.02 | 0 if approval is required, else 1 |

Rationale for the weights:

- Evidence quality (strength, completeness and contradiction-freedom) carries 0.55. A COP should handle a case alone mainly when the evidence is strong, complete and consistent.
- Operational ability (tools, authority and reversibility) carries 0.23.
- Context (upstream, history, expertise and approval) carries 0.12.
- History is weighted low on purpose, because validated memory is sparse today.

All weights are planning values set to be explainable. They are not fitted to outcomes (section 15). Every component's value, weight, contribution and basis are stored in the case record and shown on the dashboard.

## 6. Classifier and precedence: `triage.classify`

The rules are evaluated in order. The first rule that decides the tier wins, and every rule that fires is recorded in `routing.rules_fired`.

1. **R1 integrity.** An EV-02, ID-01 or ID-02 FAIL, or a prompt injection in a tool result, routes to the specialist.
2. **R2 PASS.** The tier is autonomous and the action is NONE. A proposed action is suppressed, and the suppression is recorded and audited (s09). Routine telemetry continues.
3. **R3 human.**
   - A FAIL routes to a human if it has a human label for that COP, or if the selected action needs approval or is irreversible.
   - An INCONCLUSIVE finding routes to a human if its selected action needs approval.
4. **R4 specialist trigger.** A FAIL with a specialist trigger, for example a repeated failure, routes to the specialist.
5. **R5 FAIL by quotient band.** Otherwise a FAIL is routed by the COP's quotient band. A FAIL in the specialist band or below is delegated to the specialist.
6. **R6 INCONCLUSIVE by cause.** RECHECK is autonomous, unless the quotient is below the specialist bar, in which case the action becomes DELEGATE. Information requests are assisted. `cop_error` goes to a human. `investigation_not_run` is assisted.

A low quotient or a low score is never a violation by itself. It changes only who handles the case.

No human escalation rests on the quotient alone. A FAIL whose quotient falls below the specialist bar goes to the specialist first, and the specialist escalates to a human on its own `human_when` conditions or when a referral limit is hit (`test_a_low_quotient_alone_never_routes_to_a_human`).

A human is reached in four ways:
- an R3 rule (human label, approval or irreversibility);
- `cop_error`;
- a referral that hits its cycle, hop, timeout or cost limit (s11);
- a failed or unconfirmed execution (s12).

### Outcome semantics: `triage.outcome_of`

- **PASS:** a verified quiet label, or a non-remedial label (Context Memory PRECEDENT_FOUND).
- **FAIL:** a verified attention label.
- **INCONCLUSIVE:**
  - an unverified or non-substantive finding;
  - the label INCONCLUSIVE;
  - an indeterminate label: Anomaly INDETERMINATE, Policy UNCERTAIN, Resource INDETERMINATE, Interaction PATH_PARTIAL.

Interpretation, recorded as an assumption: Sundar's rule ("PASS → no remedial action; FAIL or INCONCLUSIVE → decide whether and which action") is keyed on this outcome, so a verified VIOLATION counts as FAIL.

### Action selection

**FAIL.** The COP's proposal is kept if it is permitted and within the ceiling of `LABEL_ACTIONS[cop][label]`. Otherwise the label's default is used, or the first permitted of ALERT, WATCH and NONE. There is no automatic WATCH.

**INCONCLUSIVE.** The action is chosen by the first matching cause:

| Cause | Action |
|---|---|
| cop_error | NONE (human) |
| investigation_not_run | NONE (assisted) |
| telemetry_insufficient | REQUEST_COP_SUPPORT → Runtime Monitoring |
| recovery_exhausted | DELEGATE_TO_SPECIALIST |
| rejected_recoverable | RECHECK |
| candidate_disproved | NONE (s04) |
| missing_retrievable | COLLECT_EVIDENCE |
| missing_authoritative | HOLD_TOOL_CALL if a policy is missing, an operation is pending and the session is live. Otherwise REQUEST_CONTEXT |
| missing_telemetry | REQUEST_COP_SUPPORT |
| missing_upstream | REQUEST_COP_SUPPORT → that upstream COP |
| rejected_unrecoverable | DELEGATE_TO_SPECIALIST |
| otherwise | NONE |

If an unverified REJECTED memory record from another case exists for the same COP (a repeated failure), RECHECK, COLLECT_EVIDENCE and NONE become DELEGATE_TO_SPECIALIST (s07).

## 7. Taxonomy and authority matrix

- **Legacy names are kept.** `proposed_action.action` keeps the legacy name. `typed_action` and `triage_selected` are added alongside it.
- **Mapping:** `LEGACY_TO_TYPED` maps PASS_TO_COP → REQUEST_COP_SUPPORT, PAUSE → PAUSE_SESSION and BLOCK → BLOCK_OPERATION. The other names are identical.
- **Universal grants.** Every COP may use NONE, COLLECT_EVIDENCE, RECHECK, VERIFY_RECOVERY, REQUEST_CONTEXT, REQUEST_COP_SUPPORT and DELEGATE_TO_SPECIALIST. These actions never touch the agent (`test_universal_actions_never_change_what_an_agent_may_do`).
- **Stronger powers need an explicit grant (`GRANTS`):**
  - Policy Enforcement: HOLD_TOOL_CALL, RELEASE_TOOL_CALL, RESTRICT_PERMISSION.
  - Incident Response: HOLD, RELEASE, STOP, RESTART, ROLLBACK, OPEN_INCIDENT.
  - Recovery & Resilience: ROLLBACK.
  These grants are a **spec change** to the canonical decision contract.
- **Substitution is unchanged.** `check_typed` substitutes the same way as `check()`: an action that is not permitted becomes WATCH or NONE, and INTERVENE in dry-run or a non-live session becomes ALERT or NONE.
- No real external adapter is connected. Every execution is a dry-run receipt.

## 8. Specialist map (`triage.SPECIALISTS`)

Every COP has a specialist pathway. A specialist is a bounded automated investigation, not a human (`is_human: False`).

Each referral is a `ReferralContract` with:
- the initiator, receiver, kind and chain;
- `max_hops` 2, `timeout_s` 900 and `cost_budget_usd` 0.10;
- a status of PENDING, RETURNED or ESCALATED_TO_HUMAN.

`ReferralRouter.open` rejects cycles and hop overruns. `dispatch` enforces the timeout and the cost bound. Every rejection escalates to a human (s11).

The extra tools are existing registered tool names. The specialist may use only tools that already pass the tool gate, so no new tool authority is created. In this branch the graph opens the contract and records it as a dry-run task. No specialist handler is wired to run yet (section 15).

| COP | receiver | why needed | transfers | extra tools | can decide | cannot authorise | returns | human when |
|---|---|---|---|---|---|---|---|---|
| `workflow_orchestration` | `specialist:workflow_plan_reconstruction` | complex task-plan reconstruction and repeated-failure analysis | finding and rejected candidate, verifier results, triage record, evidence sufficiency, tool call ids and results, upstream findings used | get_task_milestones, retrieve_trace_window, compare_workflow_failures | whether the task is drifting, stalled or on an unexpected branch, from the full plan | instruct the agent or change its plan without approval | a verified finding to workflow_orchestration as an upstream input; workflow_orchestration re-runs grade_and_route. The specialist writes no VALIDATED memory (record_feedback stays the only route) | the specialist cannot verify a reading within its budget, the decision needs approval, the referral hits a cycle, hop limit, timeout or cost limit |
| `runtime_monitoring` | `specialist:telemetry_integrity` | collector disagreement, source reconstruction and telemetry integrity diagnostics | finding and rejected candidate, verifier results, triage record, evidence sufficiency, tool call ids and results, upstream findings used | get_collector_breakdown, check_collector_expectations | which collector is right when sources disagree; whether telemetry is SUFFICIENT | alter collector configuration or discard events | a verified finding to runtime_monitoring as an upstream input; runtime_monitoring re-runs grade_and_route. The specialist writes no VALIDATED memory (record_feedback stays the only route) | the specialist cannot verify a reading within its budget, the decision needs approval, the referral hits a cycle, hop limit, timeout or cost limit |
| `anomaly_detection` | `specialist:baseline_investigation` | unusual behaviours needing deeper baseline or contextual investigation | finding and rejected candidate, verifier results, triage record, evidence sufficiency, tool call ids and results, upstream findings used | compare_versioned_baseline, find_similar_validated_cases, retrieve_trace_window | ANOMALOUS or NORMAL against a wider baseline | block or pause the agent | a verified finding to anomaly_detection as an upstream input; anomaly_detection re-runs grade_and_route. The specialist writes no VALIDATED memory (record_feedback stays the only route) | the specialist cannot verify a reading within its budget, the decision needs approval, the referral hits a cycle, hop limit, timeout or cost limit |
| `context_memory` | `specialist:context_provenance` | unresolved context provenance, compaction or semantic contradiction | finding and rejected candidate, verifier results, triage record, evidence sufficiency, tool call ids and results, upstream findings used | verify_context_provenance, compare_instruction_versions, get_context_snapshots | which context version is authoritative | rewrite the agent's context or memory | a verified finding to context_memory as an upstream input; context_memory re-runs grade_and_route. The specialist writes no VALIDATED memory (record_feedback stays the only route) | the specialist cannot verify a reading within its budget, the decision needs approval, the referral hits a cycle, hop limit, timeout or cost limit |
| `interaction_discovery` | `specialist:route_reconstruction` | complex agent-to-agent or MCP route reconstruction | finding and rejected candidate, verifier results, triage record, evidence sufficiency, tool call ids and results, upstream findings used | build_interaction_graph, compare_known_routes | the full interaction path and whether a route is new | block a route | a verified finding to interaction_discovery as an upstream input; interaction_discovery re-runs grade_and_route. The specialist writes no VALIDATED memory (record_feedback stays the only route) | the specialist cannot verify a reading within its budget, the decision needs approval, the referral hits a cycle, hop limit, timeout or cost limit |
| `incident_response` | `specialist:incident_correlation` | multi-agent incident correlation and containment planning | finding and rejected candidate, verifier results, triage record, evidence sufficiency, tool call ids and results, upstream findings used | get_incident_evidence, get_verified_findings, check_exposure | whether findings form one incident and a containment plan to propose | pause, block or stop a session: those need a human approval | a verified finding to incident_response as an upstream input; incident_response re-runs grade_and_route. The specialist writes no VALIDATED memory (record_feedback stays the only route) | the specialist cannot verify a reading within its budget, the decision needs approval, the referral hits a cycle, hop limit, timeout or cost limit |
| `classification_tagging` | `specialist:taxonomy_interpretation` | difficult taxonomy interpretation and ambiguous event categorisation | finding and rejected candidate, verifier results, triage record, evidence sufficiency, tool call ids and results, upstream findings used | query_taxonomy, compare_validated_examples | the governance tag for an ambiguous event | change the taxonomy itself | a verified finding to classification_tagging as an upstream input; classification_tagging re-runs grade_and_route. The specialist writes no VALIDATED memory (record_feedback stays the only route) | the specialist cannot verify a reading within its budget, the decision needs approval, the referral hits a cycle, hop limit, timeout or cost limit |
| `policy_enforcement` | `specialist:policy_resolution` | conflicting permission rules, entitlement ambiguity and approval resolution | finding and rejected candidate, verifier results, triage record, evidence sufficiency, tool call ids and results, upstream findings used | evaluate_session_operations, check_entitlements | which rule governs and whether approval is required | grant an exception or block an operation without approval | a verified finding to policy_enforcement as an upstream input; policy_enforcement re-runs grade_and_route. The specialist writes no VALIDATED memory (record_feedback stays the only route) | the specialist cannot verify a reading within its budget, the decision needs approval, the referral hits a cycle, hop limit, timeout or cost limit |
| `risk_mitigation` | `specialist:risk_assessment` | multi-dimensional risk assessment and mitigation trade-offs | finding and rejected candidate, verifier results, triage record, evidence sufficiency, tool call ids and results, upstream findings used | calculate_risk_components, evaluate_control_prerequisites, read_risk_register | residual risk and the recommended mitigation | accept residual risk on behalf of the owner | a verified finding to risk_mitigation as an upstream input; risk_mitigation re-runs grade_and_route. The specialist writes no VALIDATED memory (record_feedback stays the only route) | the specialist cannot verify a reading within its budget, the decision needs approval, the referral hits a cycle, hop limit, timeout or cost limit |
| `resource_management` | `specialist:quota_resolution` | complex budget, quota and resource allocation conflicts | finding and rejected candidate, verifier results, triage record, evidence sufficiency, tool call ids and results, upstream findings used | compute_consumption, get_quota | which entitlement applies when quotas conflict | raise a quota or budget | a verified finding to resource_management as an upstream input; resource_management re-runs grade_and_route. The specialist writes no VALIDATED memory (record_feedback stays the only route) | the specialist cannot verify a reading within its budget, the decision needs approval, the referral hits a cycle, hop limit, timeout or cost limit |
| `governance_intelligence` | `specialist:systemic_trends` | cross-session systemic trends and recurring governance failures | finding and rejected candidate, verifier results, triage record, evidence sufficiency, tool call ids and results, upstream findings used | query_aggregate_report, compare_time_windows, get_confirmed_incidents | whether a pattern is systemic | change COP responsibilities | a verified finding to governance_intelligence as an upstream input; governance_intelligence re-runs grade_and_route. The specialist writes no VALIDATED memory (record_feedback stays the only route) | the specialist cannot verify a reading within its budget, the decision needs approval, the referral hits a cycle, hop limit, timeout or cost limit |
| `behavior_analytics` | `specialist:trajectory_analysis` | complex behavioural trajectories and recovery-pattern anomalies | finding and rejected candidate, verifier results, triage record, evidence sufficiency, tool call ids and results, upstream findings used | compute_sequence_statistics, compare_behaviour_history | the behavioural pattern over a longer window | intervene on the agent | a verified finding to behavior_analytics as an upstream input; behavior_analytics re-runs grade_and_route. The specialist writes no VALIDATED memory (record_feedback stays the only route) | the specialist cannot verify a reading within its budget, the decision needs approval, the referral hits a cycle, hop limit, timeout or cost limit |
| `recovery_resilience` | `specialist:restoration_analysis` | failed or uncertain restoration and rollback analysis | finding and rejected candidate, verifier results, triage record, evidence sufficiency, tool call ids and results, upstream findings used | get_recovery_records, validate_checkpoints | whether a viable state was restored and which checkpoint to use | perform a rollback (REQUEST_ROLLBACK needs approval) | a verified finding to recovery_resilience as an upstream input; recovery_resilience re-runs grade_and_route. The specialist writes no VALIDATED memory (record_feedback stays the only route) | the specialist cannot verify a reading within its budget, the decision needs approval, the referral hits a cycle, hop limit, timeout or cost limit |
| `workload_optimization` | `specialist:workload_comparison` | justified versus wasteful resource usage across task types | finding and rejected candidate, verifier results, triage record, evidence sufficiency, tool call ids and results, upstream findings used | compare_task_classes, calculate_usage_rates, evaluate_previous_changes | whether usage is justified for the task class | restrict models or tools | a verified finding to workload_optimization as an upstream input; workload_optimization re-runs grade_and_route. The specialist writes no VALIDATED memory (record_feedback stays the only route) | the specialist cannot verify a reading within its budget, the decision needs approval, the referral hits a cycle, hop limit, timeout or cost limit |
| `audit_traceability` | `specialist:attribution_reconstruction` | broken provenance chains and attribution reconstruction | finding and rejected candidate, verifier results, triage record, evidence sufficiency, tool call ids and results, upstream findings used | reconstruct_trace, check_decision_linkage | who acted and whether the chain can be rebuilt | amend audit records | a verified finding to audit_traceability as an upstream input; audit_traceability re-runs grade_and_route. The specialist writes no VALIDATED memory (record_feedback stays the only route) | the specialist cannot verify a reading within its budget, the decision needs approval, the referral hits a cycle, hop limit, timeout or cost limit |
| `knowledge_policy_management` | `specialist:policy_precedence` | policy precedence and semantic conflicts | finding and rejected candidate, verifier results, triage record, evidence sufficiency, tool call ids and results, upstream findings used | get_policy_versions, compare_policy_documents, inspect_rule_dependencies | which policy version takes precedence | approve or publish a policy | a verified finding to knowledge_policy_management as an upstream input; knowledge_policy_management re-runs grade_and_route. The specialist writes no VALIDATED memory (record_feedback stays the only route) | the specialist cannot verify a reading within its budget, the decision needs approval, the referral hits a cycle, hop limit, timeout or cost limit |
| `agent_lifecycle_management` | `specialist:configuration_validation` | configuration, identity and integration drift needing deeper validation | finding and rejected candidate, verifier results, triage record, evidence sufficiency, tool call ids and results, upstream findings used | get_agent_registry, detect_configuration_changes | whether a change is authorised drift or an unregistered agent | retire or block an agent | a verified finding to agent_lifecycle_management as an upstream input; agent_lifecycle_management re-runs grade_and_route. The specialist writes no VALIDATED memory (record_feedback stays the only route) | the specialist cannot verify a reading within its budget, the decision needs approval, the referral hits a cycle, hop limit, timeout or cost limit |
| `governance_architecture` | `specialist:architecture_review` | structural coverage gaps and changes to COP responsibilities | finding and rejected candidate, verifier results, triage record, evidence sufficiency, tool call ids and results, upstream findings used | query_coverage_matrix, validate_cop_relationships, inspect_cop_catalogue | whether a coverage gap is genuine and which COP should own it | change COP responsibilities: that is a human architecture decision | a verified finding to governance_architecture as an upstream input; governance_architecture re-runs grade_and_route. The specialist writes no VALIDATED memory (record_feedback stays the only route) | the specialist cannot verify a reading within its budget, the decision needs approval, the referral hits a cycle, hop limit, timeout or cost limit |


## 9. P/F/I before and after

| | PASS | FAIL | INCONCLUSIVE |
|---|---|---|---|
| Bench verdicts before triage (`test_bench_results_before_triage.json`) | 48 | 1 | 5 |
| Bench verdicts now | 48 | 1 | 5 |
| Finding outcomes before (derived with `outcome_of`) | 16 | 16 | 22 |
| Finding outcomes now | 16 | 16 | 22 |

- Fleet results are unchanged: F1 PASS, F2 FAIL. F2 fails because Risk Mitigation is INCONCLUSIVE. That was recorded before reply recording, so it needs a live rerun.
- Labels did not change, because triage reads the verifiers and never rewrites a label. INCONCLUSIVE was not removed.
- What changed:
  - WATCH on an INCONCLUSIVE finding: 19 cases before, 0 now.
  - Actions taken on a PASS: none before or after. `risk_mitigation:normal` proposed WATCH on a PASS, and that proposal is now suppressed and audited.
  - The final action changed in 23 of 54 cases. A 24th, `interaction_discovery:abnormal`, is only the rename PASS_TO_COP → REQUEST_COP_SUPPORT.

### Changed test expectations

These were restated by intent, not forced. Each is a spec change.

| Test | Before | Now | Why |
|---|---|---|---|
| `evaluation.c01` | Final action is NONE or WATCH | No remedial action: NONE, WATCH, or an evidence or coordination request | INCONCLUSIVE now asks for what it lacks |
| `evaluation.c06` | "No intervention" listed by name | Stated by tier: NO_REMEDY plus ALERT | Typed names |
| `test_runtime_actions` (lifecycle) | Substitute WATCH | Substitute None, final typed NONE, cause `candidate_disproved` | A disproved claim does nothing to the agent (s04) |
| `test_runtime_graph` (UNCERTAIN) | Final WATCH | Outcome INCONCLUSIVE, typed action in the evidence/coordination set | Sundar's rule: no automatic WATCH |
| `test_runtime_live_path` (GRANT_ADMIN) | Never interrupts | May interrupt for approval, and nothing is applied | Approval gate on typed actions |
| `test_runtime_verifiers` (2 AU-01 tests) | Corrected by "authority substitute" | Corrected by "triage action selection", final NONE on PASS | Triage now selects first. AU-01 still fails on the original proposal |

Other changes to the canonical spec and contracts:
- the UNIVERSAL and GRANTS additions (section 7);
- `CONTRACT_VERSION` `cops-runtime/2`;
- the PE-01 fail-closed HOLD;
- the new `grade_and_route` node in the canonical node sequence (`docs/cops/RUNTIME.md`).

## 10. Tier percentages (observed, 54 cases)

| Tier | Cases | Observed | Planning target |
|---|---|---|---|
| autonomous | 33 | 61.1% | 80% |
| assisted | 18 | 33.3% | 10% |
| specialist | 0 | 0.0% | 7% |
| human | 3 | 5.6% | 3% |

By outcome:

| Outcome | Autonomous | Assisted | Specialist | Human |
|---|---|---|---|---|
| PASS | 16 | 0 | 0 | 0 |
| FAIL | 12 | 1 | 0 | 3 |
| INCONCLUSIVE | 5 | 17 | 0 | 0 |

The split is reported, not adjusted (`test_bench_routing_is_reported_not_forced`). Why it differs from 80/10/7/3:

- **The bench is not a production mix.** By design one case in three is "missing evidence" and one in three is "abnormal". A production stream is mostly normal events, and every normal PASS here routes autonomous (16 of 16).
- **Assisted at 33.3%.** 17 of the 18 assisted cases are deliberately missing-evidence cases that now ask for the evidence they lack.
- **Specialist at 0%.** No bench case met a specialist trigger: repeated failure, exhausted re-assessment, integrity failure, or a quotient in the specialist band. The pathway is exercised by s07, s11 and the low-quotient test. The four RECHECK cases (section 15) are the likeliest to reach the specialist if their live re-assessment is rejected again.
- **Human at 5.6%,** which is above the 5% aim. The three cases are below. Reaching under 5% would mean removing human accountability for a confirmed violation, a confirmed incident or an approval-gated instruction. That is not safely achievable, so it was not done. On a production mix, where these are rare events, the share would be far lower. That is an expectation, not a measurement.

## 11. Human cases and why

| Case | Rule | Action | Why |
|---|---|---|---|
| `policy_enforcement:abnormal` | R3 human label | BLOCK_OPERATION proposed → ALERT after authority | A verified VIOLATION needs an accountable human. In dry-run, BLOCK is substituted by ALERT (`check_typed`) |
| `incident_response:abnormal` | R3 human label | ALERT | A confirmed INCIDENT needs an accountable human for containment |
| `risk_mitigation:abnormal` | R3 approval | INSTRUCT | INSTRUCT requires approval in this mode, so a human task of kind `approval` is created and nothing is applied |

## 12. Relaxed-verification admissions

Nothing was relaxed in the verifiers. Every verifier from PR #4 runs unchanged, and triage cannot turn a FAIL check into a PASS. The admissions this design does make are listed here so they can be reviewed.

1. **Provisional findings are acted on (score ≤ 3).** Three verified FAILs stand as provisional findings with their limitations listed:

   | Case | Score | Final action | Tier |
   |---|---|---|---|
   | `runtime_monitoring:abnormal` | 3 | WATCH | autonomous |
   | `classification_tagging:abnormal` | 3 | NONE | autonomous |
   | `governance_architecture:normal` | 3 | WATCH | assisted |

   `governance_architecture:normal` is also the one bench FAIL (it reads GAP where ADEQUATE is expected). None of the three received an intervention.
2. **PE-01 accepts a fail-closed `HOLD_TOOL_CALL` with no policy.** Holding is the conservative choice. It needs a live session and a pending operation, and it creates a human task (s06). No bench case triggered it.
3. **RECHECK is autonomous below the assisted bar.** It is autonomous while the quotient is at or above the specialist bar, because a bounded re-assessment changes nothing outside the COP. An example is `knowledge_policy_management:abnormal` at 0.489, against an assisted bar of 0.62.

## 13. Added cost and latency

- **Model cost added: $0.** Triage makes no model calls. RECHECK is the only new action that would call a model. In replay it is recorded as needing a live call, and nothing is spent.
- **Latency.** `triage.classify` was timed over the full bench (250 COP runs, measured with `time.perf_counter` in this session): median 0.09 ms, p95 0.16 ms, max 0.43 ms. Each run also records the node's own time in `node_timings["grade_and_route"]`.
- **Budget per referral, when wired:** at most $0.10, 900 s and 2 hops, enforced by `ReferralRouter.dispatch`.
- **Storage.** Each finding now carries a `triage` record. `test_bench_results.json` grew by about 19.6k lines.

## 14. Action selection per case

"Before" is the final action from the pre-triage snapshot. "Now" reads proposed → triage-selected → final after `authority_check`.

| case | outcome | score | conf | quotient | tier | before final | proposed → selected → final | cause | reason |
|---|---|---|---|---|---|---|---|---|---|
| runtime_monitoring:normal | PASS | 4 | 0.85 | 0.917 | autonomous | NONE | NONE → NONE → NONE | – | PASS: verified reading needs no remedy |
| runtime_monitoring:abnormal | FAIL | 3 | 0.65 | 0.818 | autonomous | WATCH | WATCH → WATCH → WATCH | – | quotient 0.818 in the autonomous band of runtime_monitoring (autonomous >= 0.8, assisted >= 0.6, specialist >= 0.35) |
| runtime_monitoring:missing | FAIL | 5 | 0.9 | 0.98 | autonomous | ALERT | ALERT → ALERT → ALERT | – | quotient 0.98 in the autonomous band of runtime_monitoring (autonomous >= 0.8, assisted >= 0.6, specialist >= 0.35) |
| workflow_orchestration:normal | PASS | 4 | 0.6 | 0.917 | autonomous | NONE | NONE → NONE → NONE | – | PASS: verified reading needs no remedy |
| workflow_orchestration:abnormal | INCONCLUSIVE | 2 | 0.0 | 0.562 | autonomous | WATCH | WATCH → RECHECK → RECHECK | rejected_recoverable | INCONCLUSIVE (rejected_recoverable): candidate rejected for a recoverable reason; one bounded re-assessment with the verifier's feedback (replay of recorded replies: there is no recorded reply for a re-assessment, so it needs a live call) |
| workflow_orchestration:missing | INCONCLUSIVE | 1 | 0.0 | 0.579 | assisted | WATCH | WATCH → REQUEST_CONTEXT → REQUEST_CONTEXT | missing_authoritative | INCONCLUSIVE (missing_authoritative): required artifact unavailable: artifact:task_plan |
| context_memory:normal | PASS | 4 | 0.85 | 0.917 | autonomous | NONE | NONE → NONE → NONE | – | PASS: verified reading needs no remedy |
| context_memory:abnormal | FAIL | 4 | 0.85 | 0.917 | autonomous | WATCH | WATCH → WATCH → WATCH | – | quotient 0.917 in the autonomous band of context_memory (autonomous >= 0.75, assisted >= 0.55, specialist >= 0.3) |
| context_memory:missing | INCONCLUSIVE | 1 | 0.0 | 0.4 | assisted | WATCH | WATCH → REQUEST_CONTEXT → REQUEST_CONTEXT | missing_authoritative | INCONCLUSIVE (missing_authoritative): required artifact unavailable: artifact:context_snapshots |
| behavior_analytics:normal | PASS | 4 | 0.75 | 0.902 | autonomous | NONE | NONE → NONE → NONE | – | PASS: verified reading needs no remedy |
| behavior_analytics:abnormal | FAIL | 4 | 0.8 | 0.902 | autonomous | WATCH | WATCH → WATCH → WATCH | – | quotient 0.902 in the autonomous band of behavior_analytics (autonomous >= 0.75, assisted >= 0.55, specialist >= 0.3) |
| behavior_analytics:missing | INCONCLUSIVE | 2 | 0.0 | 0.603 | assisted | WATCH | NONE → REQUEST_COP_SUPPORT → REQUEST_COP_SUPPORT | telemetry_insufficient | INCONCLUSIVE (telemetry_insufficient): telemetry verified INSUFFICIENT by Runtime Monitoring |
| anomaly_detection:normal | PASS | 4 | 0.8 | 0.917 | autonomous | NONE | NONE → NONE → NONE | – | PASS: verified reading needs no remedy |
| anomaly_detection:abnormal | FAIL | 4 | 0.75 | 0.917 | autonomous | ALERT | ALERT → ALERT → ALERT | – | quotient 0.917 in the autonomous band of anomaly_detection (autonomous >= 0.8, assisted >= 0.6, specialist >= 0.35) |
| anomaly_detection:missing | INCONCLUSIVE | 1 | 0.0 | 0.516 | assisted | WATCH | WATCH → REQUEST_COP_SUPPORT → REQUEST_COP_SUPPORT | telemetry_insufficient | INCONCLUSIVE (telemetry_insufficient): telemetry verified INSUFFICIENT by Runtime Monitoring |
| interaction_discovery:normal | PASS | 4 | 0.8 | 0.917 | autonomous | NONE | NONE → NONE → NONE | – | PASS: verified reading needs no remedy |
| interaction_discovery:abnormal | FAIL | 5 | 0.8 | 0.98 | autonomous | PASS_TO_COP | REQUEST_COP_SUPPORT → REQUEST_COP_SUPPORT → REQUEST_COP_SUPPORT | – | quotient 0.98 in the autonomous band of interaction_discovery (autonomous >= 0.8, assisted >= 0.6, specialist >= 0.35) |
| interaction_discovery:missing | INCONCLUSIVE | 1 | 0.0 | 0.432 | assisted | WATCH | NONE → REQUEST_COP_SUPPORT → REQUEST_COP_SUPPORT | telemetry_insufficient | INCONCLUSIVE (telemetry_insufficient): telemetry verified INSUFFICIENT by Runtime Monitoring |
| agent_lifecycle_management:normal | PASS | 5 | 0.85 | 0.98 | autonomous | NONE | NONE → NONE → NONE | – | PASS: verified reading needs no remedy |
| agent_lifecycle_management:abnormal | FAIL | 5 | 0.85 | 0.98 | autonomous | ALERT | ALERT → ALERT → ALERT | – | quotient 0.98 in the autonomous band of agent_lifecycle_management (autonomous >= 0.82, assisted >= 0.62, specialist >= 0.38) |
| agent_lifecycle_management:missing | INCONCLUSIVE | 1 | 0.0 | 0.525 | assisted | WATCH | WATCH → REQUEST_CONTEXT → REQUEST_CONTEXT | missing_authoritative | INCONCLUSIVE (missing_authoritative): required artifact unavailable: artifact:agent_registry |
| policy_enforcement:normal | PASS | 4 | 0.85 | 0.856 | autonomous | NONE | NONE → NONE → NONE | – | PASS: verified reading needs no remedy |
| policy_enforcement:abnormal | FAIL | 4 | 0.85 | 0.771 | human | ALERT | BLOCK_OPERATION → BLOCK_OPERATION → ALERT | – | label VIOLATION needs an accountable human |
| policy_enforcement:missing | INCONCLUSIVE | 1 | 0.0 | 0.434 | assisted | WATCH | NONE → REQUEST_CONTEXT → REQUEST_CONTEXT | missing_authoritative | INCONCLUSIVE (missing_authoritative): required artifact unavailable: artifact:policy_store; no live operation is pending, so nothing to hold |
| recovery_resilience:normal | PASS | 5 | 0.85 | 0.98 | autonomous | NONE | NONE → NONE → NONE | – | PASS: verified reading needs no remedy |
| recovery_resilience:abnormal | FAIL | 4 | 0.85 | 0.917 | autonomous | ALERT | ALERT → ALERT → ALERT | – | quotient 0.917 in the autonomous band of recovery_resilience (autonomous >= 0.8, assisted >= 0.6, specialist >= 0.35) |
| recovery_resilience:missing | INCONCLUSIVE | 1 | 0.0 | 0.512 | assisted | NONE | NONE → REQUEST_CONTEXT → REQUEST_CONTEXT | missing_authoritative | INCONCLUSIVE (missing_authoritative): required artifact unavailable: artifact:recovery_records |
| incident_response:normal | PASS | 4 | 0.85 | 0.906 | autonomous | NONE | NONE → NONE → NONE | – | PASS: verified reading needs no remedy |
| incident_response:abnormal | FAIL | 4 | 0.6 | 0.906 | human | ALERT | ALERT → ALERT → ALERT | – | label INCIDENT needs an accountable human |
| incident_response:missing | INCONCLUSIVE | 2 | 0.0 | 0.451 | assisted | WATCH | WATCH → REQUEST_COP_SUPPORT → REQUEST_COP_SUPPORT | telemetry_insufficient | INCONCLUSIVE (telemetry_insufficient): telemetry verified INSUFFICIENT by Runtime Monitoring |
| classification_tagging:normal | INCONCLUSIVE | 2 | 0.0 | 0.615 | assisted | NONE | NONE → REQUEST_COP_SUPPORT → REQUEST_COP_SUPPORT | missing_upstream | INCONCLUSIVE (missing_upstream): upstream finding(s) missing or unverified: finding:anomaly_detection, finding:agent_lifecycle_management |
| classification_tagging:abnormal | FAIL | 3 | 0.65 | 0.758 | autonomous | NONE | NONE → NONE → NONE | – | quotient 0.758 in the autonomous band of classification_tagging (autonomous >= 0.72, assisted >= 0.52, specialist >= 0.3) |
| classification_tagging:missing | INCONCLUSIVE | 1 | 0.0 | 0.547 | assisted | NONE | NONE → REQUEST_CONTEXT → REQUEST_CONTEXT | missing_authoritative | INCONCLUSIVE (missing_authoritative): required artifact unavailable: artifact:taxonomy |
| resource_management:normal | PASS | 5 | 0.8 | 0.98 | autonomous | NONE | NONE → NONE → NONE | – | PASS: verified reading needs no remedy |
| resource_management:abnormal | FAIL | 5 | 0.7 | 0.98 | autonomous | ALERT | ALERT → ALERT → ALERT | – | quotient 0.98 in the autonomous band of resource_management (autonomous >= 0.8, assisted >= 0.6, specialist >= 0.35) |
| resource_management:missing | INCONCLUSIVE | 1 | 0.0 | 0.495 | assisted | WATCH | NONE → REQUEST_CONTEXT → REQUEST_CONTEXT | missing_authoritative | INCONCLUSIVE (missing_authoritative): required artifact unavailable: artifact:budget_or_quota |
| risk_mitigation:normal | PASS | 4 | 0.7 | 0.904 | autonomous | WATCH | WATCH → NONE → NONE | – | PASS: verified reading needs no remedy |
| risk_mitigation:abnormal | FAIL | 4 | 0.6 | 0.844 | human | INSTRUCT | INSTRUCT → INSTRUCT → INSTRUCT | – | INSTRUCT requires approval |
| risk_mitigation:missing | INCONCLUSIVE | 1 | 0.0 | 0.561 | assisted | WATCH | WATCH → REQUEST_CONTEXT → REQUEST_CONTEXT | missing_authoritative | INCONCLUSIVE (missing_authoritative): required artifact unavailable: artifact:risk_register |
| governance_intelligence:normal | PASS | 4 | 0.4 | 0.895 | autonomous | NONE | NONE → NONE → NONE | – | PASS: verified reading needs no remedy |
| governance_intelligence:abnormal | FAIL | 4 | 0.5 | 0.895 | autonomous | WATCH | WATCH → WATCH → WATCH | – | quotient 0.895 in the autonomous band of governance_intelligence (autonomous >= 0.75, assisted >= 0.55, specialist >= 0.3) |
| governance_intelligence:missing | INCONCLUSIVE | 1 | 0.0 | 0.411 | assisted | WATCH | WATCH → REQUEST_CONTEXT → REQUEST_CONTEXT | missing_authoritative | INCONCLUSIVE (missing_authoritative): required artifact unavailable: artifact:decision_store |
| workload_optimization:normal | PASS | 4 | 0.8 | 0.906 | autonomous | NONE | NONE → NONE → NONE | – | PASS: verified reading needs no remedy |
| workload_optimization:abnormal | INCONCLUSIVE | 2 | 0.0 | 0.521 | autonomous | WATCH | WATCH → RECHECK → RECHECK | rejected_recoverable | INCONCLUSIVE (rejected_recoverable): candidate rejected for a recoverable reason; one bounded re-assessment with the verifier's feedback (replay of recorded replies: there is no recorded reply for a re-assessment, so it needs a live call) |
| workload_optimization:missing | INCONCLUSIVE | 1 | 0.0 | 0.509 | assisted | WATCH | NONE → REQUEST_CONTEXT → REQUEST_CONTEXT | missing_authoritative | INCONCLUSIVE (missing_authoritative): required artifact unavailable: artifact:task_classes |
| audit_traceability:normal | PASS | 4 | 0.85 | 0.91 | autonomous | NONE | NONE → NONE → NONE | – | PASS: verified reading needs no remedy |
| audit_traceability:abnormal | FAIL | 4 | 0.75 | 0.91 | autonomous | ALERT | ALERT → ALERT → ALERT | – | quotient 0.91 in the autonomous band of audit_traceability (autonomous >= 0.82, assisted >= 0.62, specialist >= 0.38) |
| audit_traceability:missing | INCONCLUSIVE | 1 | 0.0 | 0.465 | assisted | WATCH | NONE → REQUEST_COP_SUPPORT → REQUEST_COP_SUPPORT | telemetry_insufficient | INCONCLUSIVE (telemetry_insufficient): telemetry verified INSUFFICIENT by Runtime Monitoring |
| knowledge_policy_management:normal | PASS | 4 | 0.8 | 0.906 | autonomous | NONE | NONE → NONE → NONE | – | PASS: verified reading needs no remedy |
| knowledge_policy_management:abnormal | INCONCLUSIVE | 1 | 0.0 | 0.489 | autonomous | WATCH | WATCH → RECHECK → RECHECK | rejected_recoverable | INCONCLUSIVE (rejected_recoverable): candidate rejected for a recoverable reason; one bounded re-assessment with the verifier's feedback (replay of recorded replies: there is no recorded reply for a re-assessment, so it needs a live call) |
| knowledge_policy_management:missing | INCONCLUSIVE | 1 | 0.0 | 0.378 | assisted | WATCH | WATCH → REQUEST_CONTEXT → REQUEST_CONTEXT | missing_authoritative | INCONCLUSIVE (missing_authoritative): required artifact unavailable: artifact:policy_store; no live operation is pending, so nothing to hold |
| governance_architecture:normal | FAIL | 3 | 0.65 | 0.765 | assisted | WATCH | WATCH → WATCH → WATCH | – | quotient 0.765 in the assisted band of governance_architecture (autonomous >= 0.82, assisted >= 0.62, specialist >= 0.38) |
| governance_architecture:abnormal | INCONCLUSIVE | 2 | 0.0 | 0.547 | autonomous | WATCH | WATCH → RECHECK → RECHECK | rejected_recoverable | INCONCLUSIVE (rejected_recoverable): candidate rejected for a recoverable reason; one bounded re-assessment with the verifier's feedback (replay of recorded replies: there is no recorded reply for a re-assessment, so it needs a live call) |
| governance_architecture:missing | INCONCLUSIVE | 2 | 0.0 | 0.54 | autonomous | WATCH | WATCH → RECHECK → RECHECK | rejected_recoverable | INCONCLUSIVE (rejected_recoverable): candidate rejected for a recoverable reason; one bounded re-assessment with the verifier's feedback (replay of recorded replies: there is no recorded reply for a re-assessment, so it needs a live call) |

INCONCLUSIVE cases by cause:

| Cause | Action | Cases |
|---|---|---|
| missing_authoritative | REQUEST_CONTEXT | 11 |
| telemetry_insufficient | REQUEST_COP_SUPPORT → Runtime Monitoring | 5 |
| rejected_recoverable | RECHECK | 5 |
| missing_upstream | REQUEST_COP_SUPPORT → Anomaly Detection and ALM | 1 |

The 5 rejected_recoverable cases are workflow abnormal, workload abnormal, KPM abnormal, GA abnormal and GA missing.

## 15. What needs live validation

1. **The five RECHECK cases.** Their bounded re-assessment needs a model call, because replay has no recorded reply for it.
   - Four of them (workflow abnormal, workload abnormal, KPM abnormal, GA abnormal) are among the already-approved targeted rerun's 6 cases (6 cases + F2, $0.75 cap).
   - `governance_architecture:missing` is not in that scope, and would need approval.
   - The rerun is still blocked on `ANTHROPIC_API_KEY` being set in the project's cloud environment settings.
2. **Fleet F2.** It was recorded before reply recording, so it needs the same rerun.
3. **Thresholds, weights and confidence caps.** These are planning values. They need validated outcome history, from reviewer feedback on memory records or from the 20-user simulation, to be calibrated. Until then, `confidence.calibration` reads "uncalibrated".
4. **Specialist handlers.** Contracts are opened and recorded as dry-run tasks. No specialist investigation runs yet, and the return path and the `human_when` conditions are specified but not executed.
5. **Real intervention adapters.** None are connected. HOLD, RELEASE, BLOCK, RESTRICT, STOP, ROLLBACK and the other interventions are dry-run receipts only, and connecting any of them needs approval.
6. **Tier distribution on a production-like mix.** It needs the enterprise simulation. This bench's 1/3 / 1/3 / 1/3 design cannot measure the 80/10/7/3 targets.
7. **Known defect, unchanged.** Re-running a case overwrites reviewer feedback on memory records. That matters for item 3 and was not fixed here.

## Appendix: typed action taxonomy (`authority.TAXONOMY`)

| action | group | tier | legacy | reversibility | approval | execution | observable result | verified by |
|---|---|---|---|---|---|---|---|---|
| `NONE` | none | OBSERVE | NONE | reversible | none | auto | decision record only | record exists (persistence) |
| `WATCH` | observation | RECORD | WATCH | reversible | none | auto | watch entry against the session | watch entry present in the decision record |
| `REQUEST_CONTEXT` | observation | REQUEST | REQUEST_CONTEXT | reversible | none | auto | a context request naming the missing artifact and who can supply it | the artifact appears in a later case (requirements.assess_requirements) |
| `COLLECT_EVIDENCE` | observation | OBSERVE | NONE | reversible | none | auto | a bounded retrieval with the named tool | evidence_precheck reads the item RETRIEVED_AND_VALIDATED |
| `RECHECK` | observation | OBSERVE | NONE | reversible | none | auto | a re-assessment verified by the same independent checks | independent_verify on the revised candidate |
| `SCHEDULE_REVIEW` | observation | RECORD | WATCH | reversible | none | auto | a review entry with a due date | review entry present; outcome recorded by the reviewing COP |
| `INSTRUCT` | guidance | RECOMMEND | INSTRUCT | reversible | required_supervised | propose_only | an instruction delivered to the agent | a later turn shows the instructed change |
| `REQUEST_CORRECTION` | guidance | RECOMMEND | INSTRUCT | reversible | required_supervised | propose_only | a correction request to the agent | the drifting metric returns to NORMAL in a later case |
| `RETRY_WITH_CONSTRAINTS` | guidance | RECOMMEND | INSTRUCT | reversible | required_supervised | propose_only | a retry bounded by explicit limits | repetition metric below threshold after the retry |
| `CHANGE_EXECUTION_PLAN` | guidance | RECOMMEND | INSTRUCT | reversible | required_supervised | propose_only | a revised plan delivered to the agent | milestones progress in a later case |
| `ALERT` | coordination | ALERT | ALERT | reversible | none | auto | an alert to the reviewing team | alert recorded with the finding id |
| `REQUEST_COP_SUPPORT` | coordination | NOTIFY | PASS_TO_COP | reversible | none | auto | a referral contract to the named COP | the receiving COP publishes a verified finding |
| `DELEGATE_TO_SPECIALIST` | coordination | NOTIFY | PASS_TO_COP | reversible | none | auto | a specialist task with a referral contract | the specialist's verified result returns to the initiator |
| `ESCALATE` | coordination | INITIATE | ESCALATE | reversible | required_supervised | propose_only | an escalation opened | acknowledgement recorded |
| `OPEN_INCIDENT` | coordination | INITIATE | ESCALATE | reversible | required_supervised | propose_only | an incident record with the evidence chain | incident outcome recorded (get_incident_outcomes) |
| `REQUEST_APPROVAL` | permission | REQUEST | REQUEST_APPROVAL | reversible | required | propose_only | an approval request | AU-02 records the decision |
| `HOLD_TOOL_CALL` | permission | INTERVENE | BLOCK | reversible | required | propose_only | the pending tool call is held | EX-01 observes the hold; RELEASE_HOLD ends it |
| `PAUSE_SESSION` | permission | INTERVENE | PAUSE | reversible | required | propose_only | session paused | EX-01 observes the pause |
| `BLOCK_OPERATION` | permission | INTERVENE | BLOCK | partly_reversible | required | propose_only | the operation is refused | EX-01 observes the refusal |
| `RESTRICT_CAPABILITY` | permission | INTERVENE | BLOCK | partly_reversible | required | propose_only | a capability removed | EX-01 observes the restricted capability |
| `STOP_SESSION` | permission | INTERVENE | PAUSE | irreversible | required | propose_only | session terminated | EX-01 observes the session ended |
| `REQUEST_ROLLBACK` | recovery | INITIATE | ESCALATE | partly_reversible | required | propose_only | a rollback request to the operator | VERIFY_RECOVERY on the restored state |
| `RESTART_ISOLATED_SESSION` | recovery | INTERVENE | PAUSE | partly_reversible | required | propose_only | a new isolated session | EX-01 observes the new session |
| `VERIFY_RECOVERY` | recovery | OBSERVE | NONE | reversible | none | auto | a recovery check result | recovery_resilience reads RECOVERED |
| `RELEASE_HOLD` | recovery | INTERVENE | BLOCK | reversible | required | propose_only | the held tool call proceeds | EX-01 observes the release |

