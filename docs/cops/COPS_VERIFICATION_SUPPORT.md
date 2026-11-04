# COPS verification support: graded verification of each finding

Branch: `feat/cops-langgraph-runtime`. Status: local commit, not pushed. Date: 2026-11-04.

This builds on the triage change ([COPS_GRADED_VERIFICATION_AND_TRIAGE.md](COPS_GRADED_VERIFICATION_AND_TRIAGE.md)).

All results come from real runs on this branch:
- `python -m cops.runtime.test_bench` runs offline. It uses the deterministic COPs plus model replies recorded during the earlier approved live run, replayed. No paid call was made.
- `python -m pytest` gives 397 passed and 1 skipped.
- No real external intervention exists. Every action is a dry-run receipt.

## Summary

- **Verification now has two layers.**
  - *Hard checks* are pass/fail validity conditions: references resolve, provenance holds, the asserted facts match the data, and the mandatory policy and authority conditions on actions hold.
  - The *graded layer* (new, `collector/cops/runtime/support.py`, verifier **VS-01**) then measures how far valid evidence supports the candidate finding. It uses explicit evidence components for each COP.
- **Every candidate gets three numbers that sum to 1.**
  - **V** (the verification-support score, 0–1) is the share of the COP's evidence that supports the finding.
  - **against** is the share that supports the opposite.
  - **unknown** is the share that is missing, unreliable or uninformative.
  - Missing evidence is always counted as unknown, never as against.
- **The decision is one of four:**
  - SUPPORTED;
  - PROVISIONALLY_SUPPORTED, with its limitations listed;
  - REJECTED, because a hard check failed or the evidence supports the opposite;
  - INSUFFICIENT_EVIDENCE.

  A finding that is not supported or provisionally supported is withheld: VS-01 fails, and the label becomes INCONCLUSIVE.
- **The handling quotient is unchanged and kept separate.** It still decides autonomous, assisted, specialist or human. It reads the support decision only through the 1–5 band.
- **Action choice uses all three:** the support decision, the quotient and the operational context.
  - A provisionally supported finding may notify or coordinate.
  - It may not take a permission or recovery action (hold, block, restrict, stop or roll back). Those need SUPPORTED evidence.
  - The authority gates (`check_typed`, PE-01, AU-01, AU-02) are unchanged and still run afterwards.
- **Bench results.** The bench verdicts are unchanged at 48 PASS, 1 FAIL and 5 INCONCLUSIVE. The tier split is unchanged at 61.1% autonomous, 33.3% assisted, 0% specialist and 5.6% human.
  - Over the 54 cases, 24 findings are SUPPORTED, 9 PROVISIONALLY_SUPPORTED, 5 REJECTED and 16 INSUFFICIENT_EVIDENCE.
  - VS-01 withheld no bench finding. Every bench finding that passes the hard checks also reaches its COP's provisional minimum, and the "missing" cases were already inconclusive.
  - The new behaviour is exercised by 24 new tests (section 9).
- **I made four semantic corrections after the first bench run.** They are disclosed in section 10: the first run withheld two findings that were correct. No threshold was changed at any point.

## 1. Hard checks vs graded evidential assessment

| Layer | Checks | Effect when it fails |
|---|---|---|
| Hard, on the finding | EV-01 (well formed), EV-02 (references resolve), ID-01 / ID-02 (provenance and scope), MC-01 (values recompute from raw observables), SC-01 (each claim matches the data it cites) | REJECTED, whatever the other evidence says |
| Hard, on the action | PE-01 (policy basis), AU-01 (authority), AU-02 (approval), EX-01 (outcome observed) | The action is substituted or blocked at `authority_check` (unchanged) |
| Graded floors | SC-02 (independent support), DS-01 (label permitted by the evidence state), DS-02 (no correlated upstream counted twice) | These still block, as before. In the graded layer they are minimum requirements, so failing one means INSUFFICIENT_EVIDENCE, not "the opposite is true" |
| Graded consistency | DS-03 (rule baseline does not contradict a quiet model reading), RP-01 (diagnostic agreement) | Enter as the `rule_baseline` component's direction |
| **Graded support (new)** | **VS-01**: V, against and unknown from the COP's evidence components | Withholds a candidate that passed every other check but is not at least provisionally supported |

The partition is enforced by `test_hard_and_graded_checks_are_separate_and_cover_every_finding_check`. The registry is `support.HARD_CHECKS`, `ACTION_HARD_CHECKS`, `GRADED_FLOOR_CHECKS` and `GRADED_CONSISTENCY_CHECKS`. VS-01 is registered in `verifiers.CHECKS` (category `VS`, BLOCKS_FINDING). It is computed in `graph.independent_verify` right after the registry runs.

## 2. The exact formula

For candidate label L, the COP's components are listed in `support.profile(cop)`. Each component c that applies to the case is evaluated from real evidence and returns four things:

- **direction** d_c: supports L, opposes L, neutral, or unknown;
- **strength** s_c, from 0 to 1;
- **reliability** r_c, from 0 to 1;
- **weight** w_c: its relevance, where direct = 1.0, supporting = 0.5 and contextual = 0.25.

```
W        = Σ w_c                                  (applicable components)
V        = Σ w_c · r_c · s_c  [d_c = supports] / W     verification-support score
against  = Σ w_c · r_c · s_c  [d_c = opposes]  / W
unknown  = 1 − V − against
```

The five graded dimensions are recorded for every assessment:

| Dimension | Definition |
|---|---|
| relevance | w_c, fixed per component. Raised to direct when the candidate cites that upstream finding as its basis and SC-01 checked it. The record shows this as "raised to direct" |
| reliability | r_c. For metrics it is the telemetry level from a verified Runtime Monitoring verdict (`interpret.EVIDENCE_LEVEL`: 0.9 sufficient, 0.6 degraded, 0.3 insufficient; 0.9 when absent, the runtime's existing default). It is 1.0 for authoritative records read through a permitted tool. For an upstream finding it is 1.0 if SUPPORTED, 0.6 if PROVISIONALLY_SUPPORTED and 0.9 without a support record |
| completeness | Σ w over components that are known, divided by W |
| consistency | V / (V + against) |
| corroboration | The number of distinct independent sources among the supporting components. The rule baseline is excluded, because it reads the same data |

### How strength is set (no new constants)

- **Metric in an attention state, against a quiet label.** The strength is absolute, taken from the existing severity ranks (`interpret.RANK` over `SEV`): WARNING 1/3, EXCEPTION 2/3, CRITICAL 1.
- **Metric in an attention state, for an attention label.** The strength is relative to the severity the candidate claims: min(1, rank(state) / rank(claimed severity)). A WARNING therefore fully supports a LOW-severity finding such as DEGRADED, but gives only a third of the support a HIGH-severity claim needs.
- **Metrics observed quiet, for a quiet label.** The strength is the share of the COP's metrics observed within threshold. A NOT_APPLICABLE metric counts half: "the condition did not arise" is weaker than "measured within threshold".
- **Records and recomputations.** The strength is 1 when the record shows the proposition, and 0.5 where the record bears on it only partly. Unreached milestones are one example: unfinished and stalled look the same from counts alone. A record that was read with nothing in it cited against a quiet reading is another.

### Direction rules that keep missing evidence and the opposite apart

- Anything not observed, not read, unavailable or unverified is **unknown**. It never opposes.
- A quiet label ("nothing to see") needs every one of its COP's metrics observed quiet. An attention state anywhere opposes it.
- An attention label usually has metrics that are *sufficient but not necessary* for it. Such metrics can support the label, but when quiet they are neutral. Only components marked `defines_attention` can oppose an attention label: Anomaly Detection's deviation metrics, and Runtime Monitoring's telemetry measures. So low support for ANOMALOUS is not support for NORMAL. NORMAL needs its own support, from quiet metrics and a baseline within range (`test_low_support_for_anomalous_does_not_prove_normal`, `test_normal_needs_its_own_support_not_the_absence_of_anomaly`).
- Another COP's quiet reading does not disprove this COP's attention reading, because it answers a different question. Another COP's attention reading does weigh against a quiet reading here.

### Decision (`support.assess`), first match wins

1. A hard check failed → **REJECTED**.
2. The label is INCONCLUSIVE or states uncertainty (INDETERMINATE, UNCERTAIN, PATH_PARTIAL) → **INSUFFICIENT_EVIDENCE** (no proposition to support).
3. against ≥ reject_min and against > V → **REJECTED** (the evidence supports the opposite).
4. A component required for L is unknown → **INSUFFICIENT_EVIDENCE**.
5. A graded floor check (SC-02, DS-01 or DS-02) failed → **INSUFFICIENT_EVIDENCE**.
6. No direct component supports L → **INSUFFICIENT_EVIDENCE**.
7. V ≥ supported_min, corroboration ≥ 2 and against ≤ 0.1 → **SUPPORTED**.
8. V ≥ provisional_min and against < V → **PROVISIONALLY_SUPPORTED**, with its limitations listed.
9. Otherwise → **INSUFFICIENT_EVIDENCE**: undecided, not disproved.

Rules 4 to 6 are why a qualified or provisional conclusion must still meet its COP's minimum evidential requirements.

VS-01 maps the decision to a verifier result:

| Decision | VS-01 result | Effect |
|---|---|---|
| SUPPORTED | PASS | The finding stands |
| PROVISIONALLY_SUPPORTED | WARN | The finding stands, with `provisional: true` and a qualification |
| REJECTED | FAIL `CONTRARY_EVIDENCE` | The candidate is withheld through the existing rejection path and kept as `rejected_candidate` |
| INSUFFICIENT_EVIDENCE | FAIL `INSUFFICIENT_SUPPORT` | Same as REJECTED |

When another check already rejected the candidate, VS-01 is NOT_RUN. The candidate's assessment is still recorded for audit, and the existing recovery logic decides what happens next. For example, a recoverable SC-01 rejection still gets RECHECK.

## 3. How the thresholds were justified

The thresholds are **derived** from each COP's own component list (`support.thresholds`), using two existing constants:

- **ρ_S = 0.9.** This is the runtime's reliability for SUFFICIENT telemetry (`EVIDENCE_LEVEL["SUFFICIENT"]`).
- **ρ_P = 0.6.** This is the reliability for DEGRADED telemetry.

The formulas:

```
supported_min        = ρ_S × (required weight + smallest corroborating weight) / W
provisional_min      = ρ_P × required weight / W        (primary direct weight if the label class has no required component)
reject_min           = ρ_P × primary direct weight / W
max_against_supported = 1 − ρ_S = 0.1
min corroboration for SUPPORTED = 2 independent sources
```

What each one means:

- **SUPPORTED** means at least the support you get from the required evidence plus one corroborating component, at sufficient-telemetry reliability and full strength, from two independent sources. The contrary evidence must be no larger than the unreliability that sufficient telemetry allows.
- **PROVISIONALLY_SUPPORTED** means the required evidence alone, at degraded reliability or better. Below that, the evidence is too thin to stand on even with its limitations stated.
- **REJECTED for contrary evidence** means the primary evidence points the other way at degraded reliability or better, and outweighs the support.

Why this is not tuning:

- No threshold is a free parameter. Changing one means changing the COP's evidence specification (which components exist, and which are required), which is reviewable on its own terms.
- `test_every_cop_has_explicit_components_and_derived_thresholds` asserts that every threshold equals its formula.
- The thresholds were not changed at any point during this work. Section 10 lists what *was* changed after the first bench run, and why.

Consequence (how much a wrong call would cost) is deliberately **not** in these thresholds. Support thresholds say whether the evidence is enough. Consequence is handled by the quotient's COP-specific bands, the human-required labels, the provisional cap on permission and recovery actions, and the authority gates.

## 4. Difference between the verification-support score and the handling quotient

| | Verification support V (new) | Handling quotient (unchanged) |
|---|---|---|
| Question | How far does valid evidence support this finding? | Can this COP handle this decision itself? |
| When | Inside verification (VS-01), before the finding is accepted | After verification, in `grade_and_route` |
| Inputs | Evidence only: metric rows, records, verified upstream findings, rule baseline, validated memory | Evidence strength (the 1–5 band of V's decision), completeness, interpretability, contradictions, tool availability, authority, reversibility, upstream reliability, history, expertise, approval |
| Effect | Decides whether the finding stands: SUPPORTED, PROVISIONAL, REJECTED or INSUFFICIENT | Decides who handles it: autonomous, assisted, specialist or human |
| Missing evidence | Unknown, never against | Lowers completeness, so less autonomy |
| Model confidence | Not an input (`test_self_reported_confidence_is_not_an_input`) | Not an input |
| Thresholds | Derived per COP from its components (section 3) | Set per COP by consequence (`triage.THRESHOLDS`, unchanged) |

The same evidence can be handled differently. In `test_the_quotient_reads_support_but_is_a_separate_measure`, a SUPPORTED VIOLATION gets the same evidence strength whether the action is ALERT or an approval-gated BLOCK. Its quotient differs, because authority, reversibility and approval differ. A strong finding can still go to a human, and a weak one can never be acted on autonomously by an intervening action.

## 5. Evidence components, requirements and derived thresholds for all 18 COPs

Components marked "req" must be known for that label class. Weights: direct 1.0, supporting 0.5, contextual 0.25.

How the profiles were built:
- **Six COPs have hand-specified evaluators:** Anomaly Detection, Policy Enforcement, Workflow Orchestration, Risk Mitigation, Runtime Monitoring and Recovery & Resilience. Each reads the specific record or measurement that bears on its labels.
- **The other twelve use profiles built from their own specification:**
  - their metrics (`specs.metric_keys`);
  - their required artifacts (`requirements.REQUIREMENTS`), which are required for the quiet label, or for every label where `GATES_ALL_LABELS` says so;
  - their rule tools (`rules.DETERMINISTIC_TOOLS`);
  - their consumed upstream findings (`specs.CONSUMES`);
  - the rule baseline (LLM COPs only), and validated memory.

  Their record components count as support when SC-01 checked a cited observation. For a quiet label, a record that was read with nothing in it cited against the reading counts at half strength. That is weaker than the six hand-specified COPs, and it is listed in section 11.

The thresholds below are the static defaults, with every component applicable. At run time they are recomputed over the components that apply to the case: for example, entitlements apply only when an MCP server was used.

| COP | Components: name (relevance w, required for) | Quiet: provisional / supported / reject | Attention: provisional / supported / reject |
|---|---|---|---|
| `workflow_orchestration` | `task_milestones` (direct 1.0, req quiet); `execution_transitions` (direct 1.0); `progress_metrics` (direct 1.0); `rule_baseline` (supporting 0.5); `validated_memory` (contextual 0.25) | 0.16 / 0.3 / 0.16 | 0.16 / 0.3 / 0.16 |
| `runtime_monitoring` | `telemetry_measures` (direct 1.0, req all); `tool:get_collector_breakdown` (direct 1.0); `tool:check_collector_expectations` (direct 1.0); `validated_memory` (contextual 0.25) | 0.1846 / 0.3462 / 0.1846 | 0.1846 / 0.3462 / 0.1846 |
| `anomaly_detection` | `deviation_metrics` (direct 1.0, req all); `versioned_baseline` (supporting 0.5); `upstream:behavior_analytics` (supporting 0.5); `rule_baseline` (supporting 0.5); `validated_memory` (contextual 0.25) | 0.2182 / 0.4091 / 0.2182 | 0.2182 / 0.4091 / 0.2182 |
| `context_memory` | `record:context_snapshots` (direct 1.0, req quiet); `rule_baseline` (supporting 0.5); `validated_memory` (contextual 0.25) | 0.3429 / 0.6429 / 0.3429 | 0.3429 / 0.6429 / 0.3429 |
| `interaction_discovery` | `signal_metrics` (direct 1.0, req quiet); `tool:build_interaction_graph` (direct 1.0); `tool:compare_known_routes` (direct 1.0); `validated_memory` (contextual 0.25) | 0.1846 / 0.3462 / 0.1846 | 0.1846 / 0.3462 / 0.1846 |
| `incident_response` | `signal_metrics` (direct 1.0, req quiet); `record:policy_store` (supporting 0.5); `tool:check_exposure` (direct 1.0); `upstream:anomaly_detection` (supporting 0.5); `upstream:policy_enforcement` (supporting 0.5); `upstream:interaction_discovery` (supporting 0.5); `upstream:workflow_orchestration` (supporting 0.5); `upstream:recovery_resilience` (supporting 0.5); `rule_baseline` (supporting 0.5); `validated_memory` (contextual 0.25) | 0.1043 / 0.1957 / 0.1043 | 0.1043 / 0.1957 / 0.1043 |
| `classification_tagging` | `signal_metrics` (direct 1.0, req quiet); `record:taxonomy` (direct 1.0, req all); `tool:inspect_event_metadata` (direct 1.0); `upstream:anomaly_detection` (supporting 0.5); `upstream:agent_lifecycle_management` (supporting 0.5); `rule_baseline` (supporting 0.5); `validated_memory` (contextual 0.25) | 0.2526 / 0.4263 / 0.1263 | 0.1263 / 0.2368 / 0.1263 |
| `policy_enforcement` | `policy_evaluation` (direct 1.0, req all); `entitlements` (direct 1.0, req quiet); `policy_signal_metrics` (supporting 0.5); `upstream:interaction_discovery` (contextual 0.25); `upstream:agent_lifecycle_management` (contextual 0.25); `upstream:anomaly_detection` (contextual 0.25); `validated_memory` (contextual 0.25) | 0.3429 / 0.5786 / 0.1714 | 0.1714 / 0.3214 / 0.1714 |
| `risk_mitigation` | `register_score` (direct 1.0, req all); `exposure_metrics` (supporting 0.5); `upstream:anomaly_detection` (supporting 0.5); `upstream:interaction_discovery` (supporting 0.5); `upstream:policy_enforcement` (supporting 0.5); `upstream:resource_management` (supporting 0.5); `upstream:incident_response` (supporting 0.5); `rule_baseline` (supporting 0.5); `validated_memory` (contextual 0.25) | 0.1263 / 0.2368 / 0.1263 | 0.1263 / 0.2368 / 0.1263 |
| `resource_management` | `signal_metrics` (direct 1.0, req quiet); `record:budget_or_quota` (direct 1.0, req quiet); `tool:compute_consumption` (direct 1.0); `validated_memory` (contextual 0.25) | 0.3692 / 0.6231 / 0.1846 | 0.1846 / 0.3462 / 0.1846 |
| `governance_intelligence` | `record:decision_store` (direct 1.0, req quiet); `upstream:classification_tagging` (supporting 0.5); `upstream:recovery_resilience` (supporting 0.5); `upstream:incident_response` (supporting 0.5); `rule_baseline` (supporting 0.5); `validated_memory` (contextual 0.25) | 0.1846 / 0.3462 / 0.1846 | 0.1846 / 0.3462 / 0.1846 |
| `behavior_analytics` | `signal_metrics` (direct 1.0, req quiet); `tool:compute_sequence_statistics` (direct 1.0); `tool:compare_behaviour_history` (direct 1.0); `upstream:context_memory` (supporting 0.5); `upstream:workflow_orchestration` (supporting 0.5); `validated_memory` (contextual 0.25) | 0.1412 / 0.2647 / 0.1412 | 0.1412 / 0.2647 / 0.1412 |
| `recovery_resilience` | `failure_metrics` (contextual 0.25); `record:recovery_records` (direct 1.0, req quiet); `upstream:behavior_analytics` (supporting 0.5); `validated_memory` (contextual 0.25) | 0.3 / 0.5625 / 0.3 | 0.3 / 0.5625 / 0.3 |
| `workload_optimization` | `signal_metrics` (direct 1.0, req quiet); `record:task_classes` (direct 1.0, req quiet); `tool:calculate_usage_rates` (direct 1.0); `tool:inspect_repeated_operations` (direct 1.0); `upstream:resource_management` (supporting 0.5); `rule_baseline` (supporting 0.5); `validated_memory` (contextual 0.25) | 0.2286 / 0.3857 / 0.1143 | 0.1143 / 0.2143 / 0.1143 |
| `audit_traceability` | `signal_metrics` (direct 1.0, req quiet); `tool:reconstruct_trace` (direct 1.0); `tool:check_decision_linkage` (direct 1.0); `upstream:interaction_discovery` (supporting 0.5); `upstream:policy_enforcement` (supporting 0.5); `validated_memory` (contextual 0.25) | 0.1412 / 0.2647 / 0.1412 | 0.1412 / 0.2647 / 0.1412 |
| `knowledge_policy_management` | `record:policy_store` (direct 1.0, req quiet); `upstream:governance_intelligence` (supporting 0.5); `upstream:policy_enforcement` (supporting 0.5); `rule_baseline` (supporting 0.5); `validated_memory` (contextual 0.25) | 0.2182 / 0.4091 / 0.2182 | 0.2182 / 0.4091 / 0.2182 |
| `agent_lifecycle_management` | `signal_metrics` (direct 1.0, req quiet); `record:agent_registry` (direct 1.0, req quiet); `tool:detect_configuration_changes` (direct 1.0); `upstream:interaction_discovery` (supporting 0.5); `validated_memory` (contextual 0.25) | 0.32 / 0.54 / 0.16 | 0.16 / 0.3 / 0.16 |
| `governance_architecture` | `signal_metrics` (direct 1.0, req quiet); `tool:query_coverage_matrix` (direct 1.0); `tool:validate_cop_relationships` (direct 1.0); `upstream:governance_intelligence` (supporting 0.5); `upstream:knowledge_policy_management` (supporting 0.5); `upstream:audit_traceability` (supporting 0.5); `rule_baseline` (supporting 0.5); `validated_memory` (contextual 0.25) | 0.1143 / 0.2143 / 0.1143 | 0.1143 / 0.2143 / 0.1143 |

## 6. Complete calculations: Anomaly Detection, Policy Enforcement, Workflow Orchestration, Risk Mitigation

Each calculation is taken from this run's bench output (`docs/cops/evaluation/test_bench_results.json`, field `trace.verification_support`). Every case also shows it step by step on the dashboard.

Each case lists:
- the exact metrics in the case;
- every component, with its weight, direction, strength, reliability and contribution;
- the threshold comparisons;
- the hard and graded verifier outcomes;
- the decision;
- the triage tier and the action, as proposed, as selected and as finally authorised.

In the "missing" cases the COP's own finding is already INCONCLUSIVE, so there is no proposition and every component is recorded as unknown. That is the intended reading: the evidence is not there, which is different from evidence that the conclusion is false.

### Anomaly Detection

##### `anomaly_detection:abnormal`: candidate **ANOMALOUS** → SUPPORTED

Metrics in the case: `insights.token_outlier`=CRITICAL (40.0), `insights.duration_outlier`=NORMAL, `path.transition_novelty`=EXCEPTION (0.545), `activity.action_rate_ratio`=NORMAL (1.14), `access.first_seen_mcp`=NORMAL, `insights.retry`=NOT_APPLICABLE, `insights.repetition`=NORMAL.

| Component | Relevance (w) | Required for | Direction | s | r | w·r·s / W | Evidence |
|---|---|---|---|---|---|---|---|
| `deviation_metrics` | direct 1.0 | all | supports | 1.0 | 0.9 | 0.3273 | attention state observed (claimed severity NONE): insights.token_outlier=CRITICAL (40.0), path.transition_novelty=EXCEPTION (0.545) |
| `versioned_baseline` | supporting 0.5 | – | supports | 1.0 | 1.0 | 0.1818 | outside every value in the versioned baseline: insights.token_outlier=40.0 vs [1.0, 1.0] over 7 sessions, path.transition_novelty=0.545 vs [0.0, 0.0] over 7 sessions |
| `upstream:behavior_analytics` | supporting 0.5 | – | neutral | 0.0 | 1.0 | 0.0 | behavior_analytics STABLE shares ['insights.repetition'] with this COP's metrics: the same signal, not corroboration |
| `rule_baseline` | supporting 0.5 | – | supports | 1.0 | 1.0 | 0.1818 | rule baseline reads ANOMALOUS too |
| `validated_memory` | contextual 0.25 | – | unknown | 0.0 | 1.0 | 0.0 | no validated precedent |

- W = 2.75; required weight 1.0; primary 1.0; smallest corroborating 0.25.
- **V = 0.6909**, against = 0, unknown = 0.3091.
- Thresholds: provisional_min = 0.6 × 1.0 / 2.75 = 0.2182; supported_min = 0.9 × (1.0 + 0.25) / 2.75 = 0.4091; reject_min = 0.6 × 1.0 / 2.75 = 0.2182.
- Dimensions: relevance 0.8, reliability 0.96, completeness 0.9091, consistency 1.0, corroboration 2 (baseline_history, telemetry).
- Hard checks: EV-01 PASS, EV-02 PASS, ID-01 PASS, ID-02 PASS, MC-01 PASS, SC-01 PASS. Graded checks: SC-02 PASS, DS-01 PASS, DS-02 NOT_RUN, DS-03 NOT_RUN, RP-01 PASS.
- Decision: **SUPPORTED**: support 0.6909 >= supported_min 0.4091, corroborated by 2 sources, against 0 <= 0.1. VS-01: PASS SUPPORTED.
- Triage: outcome FAIL, score band 4, quotient 0.917 against anomaly_detection bars 0.8 / 0.6 / 0.35 → tier **autonomous** (quotient 0.917 in the autonomous band of anomaly_detection (autonomous >= 0.8, assisted >= 0.6, specialist >= 0.35)).
- Action: proposed ALERT → selected **ALERT** → final after authority **ALERT**. the COP's proposal ALERT is permitted and within the NOTIFY ceiling for ANOMALOUS

##### `anomaly_detection:normal`: candidate **NORMAL** → SUPPORTED

Metrics in the case: `insights.token_outlier`=NORMAL (1.0), `insights.duration_outlier`=NORMAL, `path.transition_novelty`=NORMAL, `activity.action_rate_ratio`=NORMAL (1.0), `access.first_seen_mcp`=NORMAL, `insights.retry`=NOT_APPLICABLE, `insights.repetition`=NORMAL.

| Component | Relevance (w) | Required for | Direction | s | r | w·r·s / W | Evidence |
|---|---|---|---|---|---|---|---|
| `deviation_metrics` | direct 1.0 | all | supports | 0.9286 | 0.9 | 0.3039 | 6 of 7 metrics observed within threshold, 1 not applicable (half weight) |
| `versioned_baseline` | supporting 0.5 | – | supports | 1.0 | 1.0 | 0.1818 | within the versioned baseline range: insights.token_outlier=1.0 vs [1.0, 1.0] over 7 sessions |
| `upstream:behavior_analytics` | supporting 0.5 | – | neutral | 0.0 | 1.0 | 0.0 | behavior_analytics STABLE shares ['insights.repetition'] with this COP's metrics: the same signal, not corroboration |
| `rule_baseline` | supporting 0.5 | – | supports | 1.0 | 1.0 | 0.1818 | rule baseline reads NORMAL too |
| `validated_memory` | contextual 0.25 | – | unknown | 0.0 | 1.0 | 0.0 | no validated precedent |

- W = 2.75; required weight 1.0; primary 1.0; smallest corroborating 0.25.
- **V = 0.6675**, against = 0, unknown = 0.3325.
- Thresholds: provisional_min = 0.6 × 1.0 / 2.75 = 0.2182; supported_min = 0.9 × (1.0 + 0.25) / 2.75 = 0.4091; reject_min = 0.6 × 1.0 / 2.75 = 0.2182.
- Dimensions: relevance 0.8, reliability 0.96, completeness 0.9091, consistency 1.0, corroboration 2 (baseline_history, telemetry).
- Hard checks: EV-01 PASS, EV-02 PASS, ID-01 PASS, ID-02 PASS, MC-01 PASS, SC-01 PASS. Graded checks: SC-02 NOT_RUN, DS-01 PASS, DS-02 NOT_RUN, DS-03 PASS, RP-01 PASS.
- Decision: **SUPPORTED**: support 0.6675 >= supported_min 0.4091, corroborated by 2 sources, against 0 <= 0.1. VS-01: PASS SUPPORTED.
- Triage: outcome PASS, score band 4, quotient 0.917 against anomaly_detection bars 0.8 / 0.6 / 0.35 → tier **autonomous** (PASS: verified reading needs no remedy).
- Action: proposed NONE → selected **NONE** → final after authority **NONE**. PASS: no remedial action (routine telemetry and audit continue)

##### `anomaly_detection:missing`: candidate **INCONCLUSIVE** → INSUFFICIENT_EVIDENCE

Metrics in the case: `insights.token_outlier`=NORMAL (1.0), `insights.duration_outlier`=NORMAL, `path.transition_novelty`=NORMAL (0.143), `activity.action_rate_ratio`=NORMAL (0.71), `access.first_seen_mcp`=UNKNOWN, `insights.retry`=NOT_APPLICABLE, `insights.repetition`=NORMAL.

| Component | Relevance (w) | Required for | Direction | s | r | w·r·s / W | Evidence |
|---|---|---|---|---|---|---|---|
| `deviation_metrics` | direct 1.0 | all | unknown | 0.0 | 1.0 | 0.0 | no proposition to assess |
| `versioned_baseline` | supporting 0.5 | – | unknown | 0.0 | 1.0 | 0.0 | no proposition to assess |
| `upstream:behavior_analytics` | supporting 0.5 | – | unknown | 0.0 | 1.0 | 0.0 | no proposition to assess |
| `rule_baseline` | supporting 0.5 | – | unknown | 0.0 | 1.0 | 0.0 | no proposition to assess |
| `validated_memory` | contextual 0.25 | – | unknown | 0.0 | 1.0 | 0.0 | no proposition to assess |

- W = 2.75; required weight 1.0; primary 1.0; smallest corroborating 0.25.
- **V = 0**, against = 0, unknown = 1.0.
- Thresholds: provisional_min = 0.6 × 1.0 / 2.75 = 0.2182; supported_min = 0.9 × (1.0 + 0.25) / 2.75 = 0.4091; reject_min = 0.6 × 1.0 / 2.75 = 0.2182.
- Dimensions: relevance None, reliability None, completeness 0.0, consistency None, corroboration 0 (none).
- Hard checks: EV-01 PASS, EV-02 PASS, ID-01 PASS, ID-02 PASS, MC-01 NOT_RUN, SC-01 NOT_RUN. Graded checks: SC-02 NOT_RUN, DS-01 PASS, DS-02 NOT_RUN, DS-03 NOT_RUN, RP-01 WARN.
- Decision: **INSUFFICIENT_EVIDENCE**: no definite candidate label. VS-01: NOT_RUN NOT_SUBSTANTIVE.
- Triage: outcome INCONCLUSIVE, score band 1, quotient 0.516 against anomaly_detection bars 0.8 / 0.6 / 0.35 → tier **assisted** (INCONCLUSIVE (telemetry_insufficient): telemetry verified INSUFFICIENT by Runtime Monitoring).
- Action: proposed WATCH → selected **REQUEST_COP_SUPPORT** → final after authority **REQUEST_COP_SUPPORT**. telemetry verified INSUFFICIENT by Runtime Monitoring

### Policy Enforcement

##### `policy_enforcement:abnormal`: candidate **VIOLATION** → SUPPORTED

Metrics in the case: `insights.blocked`=NORMAL, `insights.sensitive_unasked`=EXCEPTION (1), `insights.outside`=NORMAL, `insights.secrets`=EXCEPTION (1), `oversight.declined_inferred`=NOT_APPLICABLE.

| Component | Relevance (w) | Required for | Direction | s | r | w·r·s / W | Evidence |
|---|---|---|---|---|---|---|---|
| `policy_evaluation` | direct 1.0 | all | supports | 1.0 | 1.0 | 0.4 | policy pol-2026-10-01: 1 operation(s) match VIOLATION: rules P-write-prod |
| `entitlements` | direct 1.0 | quiet | n/a | – | – | – | no MCP server used: entitlements do not apply |
| `policy_signal_metrics` | supporting 0.5 | – | supports | 0.6667 | 0.9 | 0.12 | attention state observed (claimed severity HIGH): insights.sensitive_unasked=EXCEPTION (1), insights.secrets=EXCEPTION (1) |
| `upstream:interaction_discovery` | contextual 0.25 | – | neutral | 0.0 | 1.0 | 0.0 | verified interaction_discovery reads CONTAINED: a quiet upstream reading does not disprove VIOLATION |
| `upstream:agent_lifecycle_management` | contextual 0.25 | – | unknown | 0.0 | 1.0 | 0.0 | agent_lifecycle_management finding present but not verified (INCONCLUSIVE): supports nothing |
| `upstream:anomaly_detection` | contextual 0.25 | – | unknown | 0.0 | 1.0 | 0.0 | anomaly_detection finding absent |
| `validated_memory` | contextual 0.25 | – | unknown | 0.0 | 1.0 | 0.0 | no validated precedent |

- W = 2.5; required weight 1.0; primary 1.0; smallest corroborating 0.25.
- **V = 0.52**, against = 0, unknown = 0.48.
- Thresholds: provisional_min = 0.6 × 1.0 / 2.5 = 0.24; supported_min = 0.9 × (1.0 + 0.25) / 2.5 = 0.45; reject_min = 0.6 × 1.0 / 2.5 = 0.24.
- Dimensions: relevance 0.8571, reliability 0.9714, completeness 0.7, consistency 1.0, corroboration 2 (authoritative_record, telemetry).
- Hard checks: EV-01 PASS, EV-02 PASS, ID-01 PASS, ID-02 PASS, MC-01 NOT_RUN, SC-01 PASS. Graded checks: SC-02 PASS, DS-01 PASS, DS-02 NOT_RUN, DS-03 NOT_RUN, RP-01 PASS.
- Decision: **SUPPORTED**: support 0.52 >= supported_min 0.45, corroborated by 2 sources, against 0 <= 0.1. VS-01: PASS SUPPORTED.
- Triage: outcome FAIL, score band 4, quotient 0.771 against policy_enforcement bars 0.85 / 0.65 / 0.4 → tier **human** (label VIOLATION needs an accountable human).
- Action: proposed BLOCK_OPERATION → selected **BLOCK_OPERATION** → final after authority **ALERT**. the COP's proposal BLOCK_OPERATION is permitted and within the INTERVENE ceiling for VIOLATION

##### `policy_enforcement:normal`: candidate **ALLOWED** → SUPPORTED

Metrics in the case: `insights.blocked`=NORMAL, `insights.sensitive_unasked`=NORMAL, `insights.outside`=NORMAL, `insights.secrets`=NORMAL, `oversight.declined_inferred`=NOT_APPLICABLE.

| Component | Relevance (w) | Required for | Direction | s | r | w·r·s / W | Evidence |
|---|---|---|---|---|---|---|---|
| `policy_evaluation` | direct 1.0 | all | supports | 1.0 | 1.0 | 0.4 | policy pol-2026-10-01: all 8 observed operations ALLOW, none denied, none needing approval, none unmatched |
| `entitlements` | direct 1.0 | quiet | n/a | – | – | – | no MCP server used: entitlements do not apply |
| `policy_signal_metrics` | supporting 0.5 | – | supports | 0.9 | 0.9 | 0.162 | 4 of 5 metrics observed within threshold, 1 not applicable (half weight) |
| `upstream:interaction_discovery` | contextual 0.25 | – | supports | 1.0 | 0.9 | 0.09 | verified interaction_discovery reads CONTAINED (no support record) |
| `upstream:agent_lifecycle_management` | contextual 0.25 | – | unknown | 0.0 | 1.0 | 0.0 | agent_lifecycle_management finding present but not verified (INCONCLUSIVE): supports nothing |
| `upstream:anomaly_detection` | contextual 0.25 | – | unknown | 0.0 | 1.0 | 0.0 | anomaly_detection finding absent |
| `validated_memory` | contextual 0.25 | – | unknown | 0.0 | 1.0 | 0.0 | no validated precedent |

- W = 2.5; required weight 1.0; primary 1.0; smallest corroborating 0.25.
- **V = 0.652**, against = 0, unknown = 0.348.
- Thresholds: provisional_min = 0.6 × 1.0 / 2.5 = 0.24; supported_min = 0.9 × (1.0 + 0.25) / 2.5 = 0.45; reject_min = 0.6 × 1.0 / 2.5 = 0.24.
- Dimensions: relevance 1.0, reliability 0.9571, completeness 0.7, consistency 1.0, corroboration 3 (authoritative_record, telemetry, upstream:interaction_discovery).
- Hard checks: EV-01 PASS, EV-02 PASS, ID-01 PASS, ID-02 PASS, MC-01 NOT_RUN, SC-01 PASS. Graded checks: SC-02 NOT_RUN, DS-01 PASS, DS-02 NOT_RUN, DS-03 NOT_RUN, RP-01 PASS.
- Decision: **SUPPORTED**: support 0.652 >= supported_min 0.45, corroborated by 3 sources, against 0 <= 0.1. VS-01: PASS SUPPORTED.
- Triage: outcome PASS, score band 4, quotient 0.856 against policy_enforcement bars 0.85 / 0.65 / 0.4 → tier **autonomous** (PASS: verified reading needs no remedy).
- Action: proposed NONE → selected **NONE** → final after authority **NONE**. PASS: no remedial action (routine telemetry and audit continue)

##### `policy_enforcement:missing`: candidate **INCONCLUSIVE** → INSUFFICIENT_EVIDENCE

Metrics in the case: `insights.blocked`=NORMAL, `insights.sensitive_unasked`=NORMAL, `insights.outside`=NORMAL, `insights.secrets`=NORMAL, `oversight.declined_inferred`=NOT_APPLICABLE.

| Component | Relevance (w) | Required for | Direction | s | r | w·r·s / W | Evidence |
|---|---|---|---|---|---|---|---|
| `policy_evaluation` | direct 1.0 | all | unknown | 0.0 | 1.0 | 0.0 | no proposition to assess |
| `entitlements` | direct 1.0 | quiet | unknown | 0.0 | 1.0 | 0.0 | no proposition to assess |
| `policy_signal_metrics` | supporting 0.5 | – | unknown | 0.0 | 1.0 | 0.0 | no proposition to assess |
| `upstream:interaction_discovery` | contextual 0.25 | – | unknown | 0.0 | 1.0 | 0.0 | no proposition to assess |
| `upstream:agent_lifecycle_management` | contextual 0.25 | – | unknown | 0.0 | 1.0 | 0.0 | no proposition to assess |
| `upstream:anomaly_detection` | contextual 0.25 | – | unknown | 0.0 | 1.0 | 0.0 | no proposition to assess |
| `validated_memory` | contextual 0.25 | – | unknown | 0.0 | 1.0 | 0.0 | no proposition to assess |

- W = 3.5; required weight 1.0; primary 1.0; smallest corroborating 0.25.
- **V = 0**, against = 0, unknown = 1.0.
- Thresholds: provisional_min = 0.6 × 1.0 / 3.5 = 0.1714; supported_min = 0.9 × (1.0 + 0.25) / 3.5 = 0.3214; reject_min = 0.6 × 1.0 / 3.5 = 0.1714.
- Dimensions: relevance None, reliability None, completeness 0.0, consistency None, corroboration 0 (none).
- Hard checks: EV-01 PASS, EV-02 PASS, ID-01 PASS, ID-02 PASS, MC-01 NOT_RUN, SC-01 NOT_RUN. Graded checks: SC-02 NOT_RUN, DS-01 PASS, DS-02 NOT_RUN, DS-03 NOT_RUN, RP-01 WARN.
- Decision: **INSUFFICIENT_EVIDENCE**: no definite candidate label. VS-01: NOT_RUN NOT_SUBSTANTIVE.
- Triage: outcome INCONCLUSIVE, score band 1, quotient 0.434 against policy_enforcement bars 0.85 / 0.65 / 0.4 → tier **assisted** (INCONCLUSIVE (missing_authoritative): required artifact unavailable: artifact:policy_store; no live operation is pending, so nothing to hold).
- Action: proposed NONE → selected **REQUEST_CONTEXT** → final after authority **REQUEST_CONTEXT**. required artifact unavailable: artifact:policy_store; no live operation is pending, so nothing to hold

### Workflow Orchestration

##### `workflow_orchestration:abnormal`: candidate **STALLED** → REJECTED

Metrics in the case: `insights.stalled`=NORMAL, `insights.repetition`=EXCEPTION (4), `oversight.declined_inferred`=NOT_APPLICABLE, `interaction.subagent_lanes`=NORMAL.

| Component | Relevance (w) | Required for | Direction | s | r | w·r·s / W | Evidence |
|---|---|---|---|---|---|---|---|
| `task_milestones` | direct 1.0 | quiet | supports | 0.5 | 1.0 | 0.1333 | 0 of 1 milestones reached |
| `execution_transitions` | direct 1.0 | – | neutral | 0.0 | 1.0 | 0.0 | no stalled lane; stall detection is sufficient, not necessary, for STALLED, so its absence does not oppose it |
| `progress_metrics` | direct 1.0 | – | supports | 0.6667 | 0.9 | 0.16 | attention state observed (claimed severity NONE): insights.repetition=EXCEPTION (4) |
| `rule_baseline` | supporting 0.5 | – | unknown | 0.0 | 1.0 | 0.0 | rule baseline has no definite reading (INCONCLUSIVE) |
| `validated_memory` | contextual 0.25 | – | unknown | 0.0 | 1.0 | 0.0 | no validated precedent |

- W = 3.75; required weight 1.0; primary 1.0; smallest corroborating 0.25.
- **V = 0.2933**, against = 0, unknown = 0.7067.
- Thresholds: provisional_min = 0.6 × 1.0 / 3.75 = 0.16; supported_min = 0.9 × (1.0 + 0.25) / 3.75 = 0.3; reject_min = 0.6 × 1.0 / 3.75 = 0.16.
- Dimensions: relevance 0.6667, reliability 0.9667, completeness 0.8, consistency 1.0, corroboration 2 (authoritative_record, telemetry).
- Hard checks: EV-01 PASS, EV-02 PASS, ID-01 PASS, ID-02 PASS, MC-01 NOT_RUN, SC-01 FAIL. Graded checks: SC-02 PASS, DS-01 PASS, DS-02 NOT_RUN, DS-03 NOT_RUN, RP-01 WARN.
- Decision: **REJECTED**: hard check failed: SC-01 claim_contradicted. VS-01: NOT_RUN CANDIDATE_ALREADY_REJECTED.
- Triage: outcome INCONCLUSIVE, score band 1, quotient 0.5 against workflow_orchestration bars 0.78 / 0.58 / 0.35 → tier **autonomous** (INCONCLUSIVE (rejected_recoverable): candidate rejected for a recoverable reason; one bounded re-assessment with the verifier's feedback (replay of recorded replies: there is no recorded reply for a re-assessment, so it needs a live call)).
- Action: proposed WATCH → selected **RECHECK** → final after authority **RECHECK**. candidate rejected for a recoverable reason; one bounded re-assessment with the verifier's feedback (replay of recorded replies: there is no recorded reply for a re-assessment, so it needs a live call)

##### `workflow_orchestration:normal`: candidate **NORMAL** → SUPPORTED

Metrics in the case: `insights.stalled`=NORMAL, `insights.repetition`=NORMAL, `oversight.declined_inferred`=NOT_APPLICABLE, `interaction.subagent_lanes`=NORMAL.

| Component | Relevance (w) | Required for | Direction | s | r | w·r·s / W | Evidence |
|---|---|---|---|---|---|---|---|
| `task_milestones` | direct 1.0 | quiet | supports | 1.0 | 1.0 | 0.2667 | 1 of 1 milestones reached |
| `execution_transitions` | direct 1.0 | – | supports | 0.5 | 1.0 | 0.1333 | no stalled lane in the execution trace (half strength: absence of a stall is not proof of progress) |
| `progress_metrics` | direct 1.0 | – | supports | 0.875 | 0.9 | 0.21 | 3 of 4 metrics observed within threshold, 1 not applicable (half weight) |
| `rule_baseline` | supporting 0.5 | – | supports | 1.0 | 1.0 | 0.1333 | rule baseline reads NORMAL too |
| `validated_memory` | contextual 0.25 | – | unknown | 0.0 | 1.0 | 0.0 | no validated precedent |

- W = 3.75; required weight 1.0; primary 1.0; smallest corroborating 0.25.
- **V = 0.7433**, against = 0, unknown = 0.2567.
- Thresholds: provisional_min = 0.6 × 1.0 / 3.75 = 0.16; supported_min = 0.9 × (1.0 + 0.25) / 3.75 = 0.3; reject_min = 0.6 × 1.0 / 3.75 = 0.16.
- Dimensions: relevance 1.0, reliability 0.9714, completeness 0.9333, consistency 1.0, corroboration 3 (authoritative_record, execution_trace, telemetry).
- Hard checks: EV-01 PASS, EV-02 PASS, ID-01 PASS, ID-02 PASS, MC-01 NOT_RUN, SC-01 PASS. Graded checks: SC-02 NOT_RUN, DS-01 PASS, DS-02 NOT_RUN, DS-03 PASS, RP-01 PASS.
- Decision: **SUPPORTED**: support 0.7433 >= supported_min 0.3, corroborated by 3 sources, against 0 <= 0.1. VS-01: PASS SUPPORTED.
- Triage: outcome PASS, score band 4, quotient 0.917 against workflow_orchestration bars 0.78 / 0.58 / 0.35 → tier **autonomous** (PASS: verified reading needs no remedy).
- Action: proposed NONE → selected **NONE** → final after authority **NONE**. PASS: no remedial action (routine telemetry and audit continue)

##### `workflow_orchestration:missing`: candidate **INCONCLUSIVE** → INSUFFICIENT_EVIDENCE

Metrics in the case: `insights.stalled`=NORMAL, `insights.repetition`=NORMAL, `oversight.declined_inferred`=NOT_APPLICABLE, `interaction.subagent_lanes`=NORMAL.

| Component | Relevance (w) | Required for | Direction | s | r | w·r·s / W | Evidence |
|---|---|---|---|---|---|---|---|
| `task_milestones` | direct 1.0 | quiet | unknown | 0.0 | 1.0 | 0.0 | no proposition to assess |
| `execution_transitions` | direct 1.0 | – | unknown | 0.0 | 1.0 | 0.0 | no proposition to assess |
| `progress_metrics` | direct 1.0 | – | unknown | 0.0 | 1.0 | 0.0 | no proposition to assess |
| `rule_baseline` | supporting 0.5 | – | unknown | 0.0 | 1.0 | 0.0 | no proposition to assess |
| `validated_memory` | contextual 0.25 | – | unknown | 0.0 | 1.0 | 0.0 | no proposition to assess |

- W = 3.75; required weight 1.0; primary 1.0; smallest corroborating 0.25.
- **V = 0**, against = 0, unknown = 1.0.
- Thresholds: provisional_min = 0.6 × 1.0 / 3.75 = 0.16; supported_min = 0.9 × (1.0 + 0.25) / 3.75 = 0.3; reject_min = 0.6 × 1.0 / 3.75 = 0.16.
- Dimensions: relevance None, reliability None, completeness 0.0, consistency None, corroboration 0 (none).
- Hard checks: EV-01 PASS, EV-02 PASS, ID-01 PASS, ID-02 PASS, MC-01 NOT_RUN, SC-01 NOT_RUN. Graded checks: SC-02 NOT_RUN, DS-01 PASS, DS-02 NOT_RUN, DS-03 NOT_RUN, RP-01 PASS.
- Decision: **INSUFFICIENT_EVIDENCE**: no definite candidate label. VS-01: NOT_RUN NOT_SUBSTANTIVE.
- Triage: outcome INCONCLUSIVE, score band 1, quotient 0.579 against workflow_orchestration bars 0.78 / 0.58 / 0.35 → tier **assisted** (INCONCLUSIVE (missing_authoritative): required artifact unavailable: artifact:task_plan).
- Action: proposed WATCH → selected **REQUEST_CONTEXT** → final after authority **REQUEST_CONTEXT**. required artifact unavailable: artifact:task_plan

### Risk Mitigation

##### `risk_mitigation:abnormal`: candidate **EMERGING** → SUPPORTED

Metrics in the case: `insights.secrets`=EXCEPTION (1), `insights.sensitive_unasked`=EXCEPTION (1), `insights.outside`=NORMAL, `insights.reverted`=NORMAL, `insights.token_outlier`=NORMAL (1.2).

| Component | Relevance (w) | Required for | Direction | s | r | w·r·s / W | Evidence |
|---|---|---|---|---|---|---|---|
| `register_score` | direct 1.0 | all | supports | 1.0 | 1.0 | 0.1905 | register risk-2026-09: impact 3 x (independent signals 1 + irreversible 0) = 3; thresholds EMERGING 2, MATERIAL 6 -> EMERGING |
| `exposure_metrics` | supporting 0.5 | – | supports | 0.6667 | 0.9 | 0.0571 | attention state observed (claimed severity NONE): insights.secrets=EXCEPTION (1), insights.sensitive_unasked=EXCEPTION (1) |
| `upstream:anomaly_detection` | supporting 0.5 | – | unknown | 0.0 | 1.0 | 0.0 | anomaly_detection finding absent |
| `upstream:interaction_discovery` | supporting 0.5 | – | neutral | 0.0 | 1.0 | 0.0 | verified interaction_discovery reads CONTAINED: a quiet upstream reading does not disprove EMERGING |
| `upstream:policy_enforcement` | direct 1.0 (raised: cited as basis) | – | supports | 1.0 | 0.9 | 0.1714 | cited as the basis and checked by SC-01: verified policy_enforcement reads VIOLATION (no support record) |
| `upstream:resource_management` | supporting 0.5 | – | unknown | 0.0 | 1.0 | 0.0 | resource_management finding present but not verified (INCONCLUSIVE): supports nothing |
| `upstream:incident_response` | supporting 0.5 | – | unknown | 0.0 | 1.0 | 0.0 | incident_response finding absent |
| `rule_baseline` | supporting 0.5 | – | supports | 1.0 | 1.0 | 0.0952 | rule baseline reads EMERGING too |
| `validated_memory` | contextual 0.25 | – | unknown | 0.0 | 1.0 | 0.0 | no validated precedent |

- W = 5.25; required weight 1.0; primary 1.0; smallest corroborating 0.25.
- **V = 0.5142**, against = 0, unknown = 0.4858.
- Thresholds: provisional_min = 0.6 × 1.0 / 5.25 = 0.1143; supported_min = 0.9 × (1.0 + 0.25) / 5.25 = 0.2143; reject_min = 0.6 × 1.0 / 5.25 = 0.1143.
- Dimensions: relevance 0.8571, reliability 0.9571, completeness 0.6667, consistency 1.0, corroboration 3 (authoritative_record, telemetry, upstream:policy_enforcement).
- Hard checks: EV-01 PASS, EV-02 PASS, ID-01 PASS, ID-02 PASS, MC-01 PASS, SC-01 PASS. Graded checks: SC-02 PASS, DS-01 PASS, DS-02 NOT_RUN, DS-03 NOT_RUN, RP-01 PASS.
- Decision: **SUPPORTED**: support 0.5142 >= supported_min 0.2143, corroborated by 3 sources, against 0 <= 0.1. VS-01: PASS SUPPORTED.
- Triage: outcome FAIL, score band 4, quotient 0.844 against risk_mitigation bars 0.85 / 0.65 / 0.4 → tier **human** (INSTRUCT requires approval).
- Action: proposed INSTRUCT → selected **INSTRUCT** → final after authority **INSTRUCT**. the COP's proposal INSTRUCT is permitted and within the RECOMMEND ceiling for EMERGING

##### `risk_mitigation:normal`: candidate **LOW** → SUPPORTED

Metrics in the case: `insights.secrets`=NORMAL, `insights.sensitive_unasked`=NORMAL, `insights.outside`=NORMAL, `insights.reverted`=NORMAL, `insights.token_outlier`=NORMAL (1.0).

| Component | Relevance (w) | Required for | Direction | s | r | w·r·s / W | Evidence |
|---|---|---|---|---|---|---|---|
| `register_score` | direct 1.0 | all | supports | 1.0 | 1.0 | 0.1739 | register risk-2026-09: impact 1 x (independent signals 1 + irreversible 0) = 1; thresholds EMERGING 2, MATERIAL 6 -> LOW |
| `exposure_metrics` | supporting 0.5 | – | supports | 1.0 | 0.9 | 0.0783 | 5 of 5 metrics observed within threshold, 0 not applicable (half weight) |
| `upstream:anomaly_detection` | supporting 0.5 | – | unknown | 0.0 | 1.0 | 0.0 | anomaly_detection finding absent |
| `upstream:interaction_discovery` | direct 1.0 (raised: cited as basis) | – | supports | 1.0 | 0.9 | 0.1565 | cited as the basis and checked by SC-01: verified interaction_discovery reads CONTAINED (no support record) |
| `upstream:policy_enforcement` | direct 1.0 (raised: cited as basis) | – | supports | 1.0 | 0.9 | 0.1565 | cited as the basis and checked by SC-01: verified policy_enforcement reads ALLOWED (no support record) |
| `upstream:resource_management` | supporting 0.5 | – | unknown | 0.0 | 1.0 | 0.0 | resource_management finding present but not verified (INCONCLUSIVE): supports nothing |
| `upstream:incident_response` | supporting 0.5 | – | unknown | 0.0 | 1.0 | 0.0 | incident_response finding absent |
| `rule_baseline` | supporting 0.5 | – | supports | 1.0 | 1.0 | 0.087 | rule baseline reads LOW too |
| `validated_memory` | contextual 0.25 | – | unknown | 0.0 | 1.0 | 0.0 | no validated precedent |

- W = 5.75; required weight 1.0; primary 1.0; smallest corroborating 0.25.
- **V = 0.6522**, against = 0, unknown = 0.3478.
- Thresholds: provisional_min = 0.6 × 1.0 / 5.75 = 0.1043; supported_min = 0.9 × (1.0 + 0.25) / 5.75 = 0.1957; reject_min = 0.6 × 1.0 / 5.75 = 0.1043.
- Dimensions: relevance 1.0, reliability 0.9375, completeness 0.6957, consistency 1.0, corroboration 4 (authoritative_record, telemetry, upstream:interaction_discovery, upstream:policy_enforcement).
- Hard checks: EV-01 PASS, EV-02 PASS, ID-01 PASS, ID-02 PASS, MC-01 PASS, SC-01 PASS. Graded checks: SC-02 NOT_RUN, DS-01 PASS, DS-02 NOT_RUN, DS-03 PASS, RP-01 PASS.
- Decision: **SUPPORTED**: support 0.6522 >= supported_min 0.1957, corroborated by 4 sources, against 0 <= 0.1. VS-01: PASS SUPPORTED.
- Triage: outcome PASS, score band 4, quotient 0.904 against risk_mitigation bars 0.85 / 0.65 / 0.4 → tier **autonomous** (PASS: verified reading needs no remedy).
- Action: proposed WATCH → selected **NONE** → final after authority **NONE**. PASS: no remedial action (routine telemetry and audit continue)

##### `risk_mitigation:missing`: candidate **INCONCLUSIVE** → INSUFFICIENT_EVIDENCE

Metrics in the case: `insights.secrets`=NORMAL, `insights.sensitive_unasked`=NORMAL, `insights.outside`=NORMAL, `insights.reverted`=NORMAL, `insights.token_outlier`=NORMAL (1.0).

| Component | Relevance (w) | Required for | Direction | s | r | w·r·s / W | Evidence |
|---|---|---|---|---|---|---|---|
| `register_score` | direct 1.0 | all | unknown | 0.0 | 1.0 | 0.0 | no proposition to assess |
| `exposure_metrics` | supporting 0.5 | – | unknown | 0.0 | 1.0 | 0.0 | no proposition to assess |
| `upstream:anomaly_detection` | supporting 0.5 | – | unknown | 0.0 | 1.0 | 0.0 | no proposition to assess |
| `upstream:interaction_discovery` | supporting 0.5 | – | unknown | 0.0 | 1.0 | 0.0 | no proposition to assess |
| `upstream:policy_enforcement` | supporting 0.5 | – | unknown | 0.0 | 1.0 | 0.0 | no proposition to assess |
| `upstream:resource_management` | supporting 0.5 | – | unknown | 0.0 | 1.0 | 0.0 | no proposition to assess |
| `upstream:incident_response` | supporting 0.5 | – | unknown | 0.0 | 1.0 | 0.0 | no proposition to assess |
| `rule_baseline` | supporting 0.5 | – | unknown | 0.0 | 1.0 | 0.0 | no proposition to assess |
| `validated_memory` | contextual 0.25 | – | unknown | 0.0 | 1.0 | 0.0 | no proposition to assess |

- W = 4.75; required weight 1.0; primary 1.0; smallest corroborating 0.25.
- **V = 0**, against = 0, unknown = 1.0.
- Thresholds: provisional_min = 0.6 × 1.0 / 4.75 = 0.1263; supported_min = 0.9 × (1.0 + 0.25) / 4.75 = 0.2368; reject_min = 0.6 × 1.0 / 4.75 = 0.1263.
- Dimensions: relevance None, reliability None, completeness 0.0, consistency None, corroboration 0 (none).
- Hard checks: EV-01 PASS, EV-02 PASS, ID-01 PASS, ID-02 PASS, MC-01 NOT_RUN, SC-01 NOT_RUN. Graded checks: SC-02 NOT_RUN, DS-01 PASS, DS-02 NOT_RUN, DS-03 NOT_RUN, RP-01 PASS.
- Decision: **INSUFFICIENT_EVIDENCE**: no definite candidate label. VS-01: NOT_RUN NOT_SUBSTANTIVE.
- Triage: outcome INCONCLUSIVE, score band 1, quotient 0.561 against risk_mitigation bars 0.85 / 0.65 / 0.4 → tier **assisted** (INCONCLUSIVE (missing_authoritative): required artifact unavailable: artifact:risk_register).
- Action: proposed WATCH → selected **REQUEST_CONTEXT** → final after authority **REQUEST_CONTEXT**. required artifact unavailable: artifact:risk_register


## 7. Bench results

| | Before (`test_bench_results_before_support.json`) | Now |
|---|---|---|
| Verdicts | 48 PASS / 1 FAIL / 5 INCONCLUSIVE | 48 PASS / 1 FAIL / 5 INCONCLUSIVE |
| Fleet | F1 PASS, F2 FAIL | F1 PASS, F2 FAIL (unchanged: needs the live rerun) |
| Tiers | 33 / 18 / 0 / 3 | 33 / 18 / 0 / 3 |
| Support decisions | – | 24 SUPPORTED, 9 PROVISIONALLY_SUPPORTED, 5 REJECTED, 16 INSUFFICIENT_EVIDENCE |
| Findings withheld by VS-01 | – | 0 |
| Provisional findings whose action was capped | – | 0 (none proposed a permission or recovery action) |

What the decisions mean:
- **The 5 REJECTED** are candidates the hard check SC-01 had already rejected: workflow abnormal, workload abnormal, GA abnormal, GA missing and incident missing. Their assessments are recorded for audit, and their recovery path (RECHECK) is unchanged.
- **The 16 INSUFFICIENT_EVIDENCE** are findings that were already INCONCLUSIVE. In 15 of them there was no definite candidate to assess. In the sixteenth, `workload_optimization:missing`, an earlier check had already rejected the candidate, and the required task-class record is unknown.
- **The 9 provisional findings** each rest on a single independent source:
  - Runtime Monitoring DEGRADED;
  - Context Memory CONTEXT_OK and CONTEXT_LOSS;
  - Recovery RECOVERED and FAILED_RECOVERY;
  - Governance Intelligence NO_PATTERN and RECURRING;
  - Governance Architecture normal (GAP, which is also the bench's one FAIL);
  - the KPM candidate, which SC-01 had already rejected.

Per case:

| Case | Candidate | Decision | V | Against | Unknown | Provisional ≥ | Supported ≥ | Sources | Final action |
|---|---|---|---|---|---|---|---|---|---|
| runtime_monitoring:normal | SUFFICIENT | SUPPORTED | 0.5568 | 0 | 0.4432 | 0.1846 | 0.3462 | 2 | NONE |
| runtime_monitoring:abnormal | DEGRADED | PROVISIONALLY_SUPPORTED | 0.2769 | 0 | 0.7231 | 0.1846 | 0.3462 | 1 | WATCH |
| runtime_monitoring:missing | INSUFFICIENT | SUPPORTED | 0.5846 | 0 | 0.4154 | 0.1846 | 0.3462 | 2 | ALERT |
| workflow_orchestration:normal | NORMAL | SUPPORTED | 0.7433 | 0 | 0.2567 | 0.16 | 0.3 | 3 | NONE |
| workflow_orchestration:abnormal | STALLED | REJECTED | 0.2933 | 0 | 0.7067 | 0.16 | 0.3 | 2 | RECHECK |
| workflow_orchestration:missing | INCONCLUSIVE | INSUFFICIENT_EVIDENCE | 0 | 0 | 1.0 | 0.16 | 0.3 | 0 | REQUEST_CONTEXT |
| context_memory:normal | CONTEXT_OK | PROVISIONALLY_SUPPORTED | 0.8571 | 0 | 0.1429 | 0.3429 | 0.6429 | 1 | NONE |
| context_memory:abnormal | CONTEXT_LOSS | PROVISIONALLY_SUPPORTED | 0.8571 | 0 | 0.1429 | 0.3429 | 0.6429 | 1 | WATCH |
| context_memory:missing | INCONCLUSIVE | INSUFFICIENT_EVIDENCE | 0 | 0 | 1.0 | 0.3429 | 0.6429 | 0 | REQUEST_CONTEXT |
| behavior_analytics:normal | STABLE | SUPPORTED | 0.4046 | 0 | 0.5954 | 0.1412 | 0.2647 | 2 | NONE |
| behavior_analytics:abnormal | POOR_RECOVERY | SUPPORTED | 0.4471 | 0 | 0.5529 | 0.1412 | 0.2647 | 2 | WATCH |
| behavior_analytics:missing | INCONCLUSIVE | INSUFFICIENT_EVIDENCE | 0 | 0 | 1.0 | 0.1412 | 0.2647 | 0 | REQUEST_COP_SUPPORT |
| anomaly_detection:normal | NORMAL | SUPPORTED | 0.6675 | 0 | 0.3325 | 0.2182 | 0.4091 | 2 | NONE |
| anomaly_detection:abnormal | ANOMALOUS | SUPPORTED | 0.6909 | 0 | 0.3091 | 0.2182 | 0.4091 | 2 | ALERT |
| anomaly_detection:missing | INCONCLUSIVE | INSUFFICIENT_EVIDENCE | 0 | 0 | 1.0 | 0.2182 | 0.4091 | 0 | REQUEST_COP_SUPPORT |
| interaction_discovery:normal | CONTAINED | SUPPORTED | 0.5568 | 0 | 0.4432 | 0.1846 | 0.3462 | 2 | NONE |
| interaction_discovery:abnormal | NEW_ROUTE | SUPPORTED | 0.5846 | 0 | 0.4154 | 0.1846 | 0.3462 | 2 | REQUEST_COP_SUPPORT |
| interaction_discovery:missing | INCONCLUSIVE | INSUFFICIENT_EVIDENCE | 0 | 0 | 1.0 | 0.1846 | 0.3462 | 0 | REQUEST_COP_SUPPORT |
| agent_lifecycle_management:normal | UNCHANGED | SUPPORTED | 0.6266 | 0 | 0.3734 | 0.32 | 0.54 | 4 | NONE |
| agent_lifecycle_management:abnormal | DRIFTED | SUPPORTED | 0.5067 | 0 | 0.4933 | 0.16 | 0.3 | 2 | ALERT |
| agent_lifecycle_management:missing | INCONCLUSIVE | INSUFFICIENT_EVIDENCE | 0 | 0 | 1.0 | 0.16 | 0.3 | 0 | REQUEST_CONTEXT |
| policy_enforcement:normal | ALLOWED | SUPPORTED | 0.652 | 0 | 0.348 | 0.24 | 0.45 | 3 | NONE |
| policy_enforcement:abnormal | VIOLATION | SUPPORTED | 0.52 | 0 | 0.48 | 0.24 | 0.45 | 2 | ALERT |
| policy_enforcement:missing | INCONCLUSIVE | INSUFFICIENT_EVIDENCE | 0 | 0 | 1.0 | 0.1714 | 0.3214 | 0 | REQUEST_CONTEXT |
| recovery_resilience:normal | RECOVERED | PROVISIONALLY_SUPPORTED | 0.5 | 0 | 0.5 | 0.3 | 0.5625 | 1 | NONE |
| recovery_resilience:abnormal | FAILED_RECOVERY | PROVISIONALLY_SUPPORTED | 0.5 | 0 | 0.5 | 0.3 | 0.5625 | 1 | ALERT |
| recovery_resilience:missing | INCONCLUSIVE | INSUFFICIENT_EVIDENCE | 0 | 0 | 1.0 | 0.3 | 0.5625 | 0 | REQUEST_CONTEXT |
| incident_response:normal | NO_INCIDENT | SUPPORTED | 0.6962 | 0 | 0.3038 | 0.0889 | 0.1667 | 5 | NONE |
| incident_response:abnormal | INCIDENT | SUPPORTED | 0.6518 | 0 | 0.3482 | 0.0889 | 0.1667 | 5 | ALERT |
| incident_response:missing | WATCH | REJECTED | 0.087 | 0 | 0.913 | 0.1043 | 0.1957 | 0 | REQUEST_COP_SUPPORT |
| classification_tagging:normal | INCONCLUSIVE | INSUFFICIENT_EVIDENCE | 0 | 0 | 1.0 | 0.1263 | 0.2368 | 0 | REQUEST_COP_SUPPORT |
| classification_tagging:abnormal | INTEGRITY | SUPPORTED | 0.5999 | 0 | 0.4001 | 0.1263 | 0.2368 | 3 | NONE |
| classification_tagging:missing | INCONCLUSIVE | INSUFFICIENT_EVIDENCE | 0 | 0 | 1.0 | 0.1263 | 0.2368 | 0 | REQUEST_CONTEXT |
| resource_management:normal | NORMAL | SUPPORTED | 0.7384 | 0 | 0.2616 | 0.3692 | 0.6231 | 3 | NONE |
| resource_management:abnormal | UNAUTHORIZED | SUPPORTED | 0.5846 | 0 | 0.4154 | 0.1846 | 0.3462 | 2 | ALERT |
| resource_management:missing | INCONCLUSIVE | INSUFFICIENT_EVIDENCE | 0 | 0 | 1.0 | 0.1846 | 0.3462 | 0 | REQUEST_CONTEXT |
| risk_mitigation:normal | LOW | SUPPORTED | 0.6522 | 0 | 0.3478 | 0.1043 | 0.1957 | 4 | NONE |
| risk_mitigation:abnormal | EMERGING | SUPPORTED | 0.5142 | 0 | 0.4858 | 0.1143 | 0.2143 | 3 | INSTRUCT |
| risk_mitigation:missing | INCONCLUSIVE | INSUFFICIENT_EVIDENCE | 0 | 0 | 1.0 | 0.1263 | 0.2368 | 0 | REQUEST_CONTEXT |
| governance_intelligence:normal | NO_PATTERN | PROVISIONALLY_SUPPORTED | 0.4615 | 0 | 0.5385 | 0.1846 | 0.3462 | 1 | NONE |
| governance_intelligence:abnormal | RECURRING | PROVISIONALLY_SUPPORTED | 0.4615 | 0 | 0.5385 | 0.1846 | 0.3462 | 1 | WATCH |
| governance_intelligence:missing | INCONCLUSIVE | INSUFFICIENT_EVIDENCE | 0 | 0 | 1.0 | 0.1846 | 0.3462 | 0 | REQUEST_CONTEXT |
| workload_optimization:normal | EFFICIENT | SUPPORTED | 0.8381 | 0 | 0.1619 | 0.2286 | 0.3857 | 3 | NONE |
| workload_optimization:abnormal | REDUNDANT | REJECTED | 0.0952 | 0 | 0.9048 | 0.1143 | 0.2143 | 0 | RECHECK |
| workload_optimization:missing | EFFICIENT | INSUFFICIENT_EVIDENCE | 0.5524 | 0 | 0.4476 | 0.2286 | 0.3857 | 2 | REQUEST_CONTEXT |
| audit_traceability:normal | COMPLETE | SUPPORTED | 0.5264 | 0 | 0.4736 | 0.1412 | 0.2647 | 3 | NONE |
| audit_traceability:abnormal | BROKEN_TRACE | SUPPORTED | 0.4471 | 0 | 0.5529 | 0.1412 | 0.2647 | 2 | ALERT |
| audit_traceability:missing | INCONCLUSIVE | INSUFFICIENT_EVIDENCE | 0 | 0 | 1.0 | 0.1412 | 0.2647 | 0 | REQUEST_COP_SUPPORT |
| knowledge_policy_management:normal | CURRENT | SUPPORTED | 0.709 | 0 | 0.291 | 0.2182 | 0.4091 | 2 | NONE |
| knowledge_policy_management:abnormal | CURRENT | PROVISIONALLY_SUPPORTED | 0.5846 | 0.1538 | 0.2616 | 0.1846 | 0.3462 | 2 | RECHECK |
| knowledge_policy_management:missing | INCONCLUSIVE | INSUFFICIENT_EVIDENCE | 0 | 0 | 1.0 | 0.2182 | 0.4091 | 0 | REQUEST_CONTEXT |
| governance_architecture:normal | GAP | PROVISIONALLY_SUPPORTED | 0.381 | 0.0952 | 0.5238 | 0.1143 | 0.2143 | 1 | WATCH |
| governance_architecture:abnormal | GAP | REJECTED | 0.0952 | 0 | 0.9048 | 0.1143 | 0.2143 | 0 | RECHECK |
| governance_architecture:missing | GAP | REJECTED | 0.0571 | 0.0952 | 0.8477 | 0.1143 | 0.2143 | 1 | RECHECK |


## 8. How the action is chosen now

Triage (`triage.classify`) uses three inputs:
- the support decision, through the 1–5 band (`band_from_verification_support`): REJECTED → 1; INSUFFICIENT → 1 or 2; PROVISIONAL → 3; SUPPORTED → 4, or 5 when every component is known and nothing is against;
- the quotient;
- the operational context (mode, live session, pending operations, approval rules).

Changes:
- **A provisionally supported FAIL cannot select a permission or recovery action** (`PROVISIONAL_BARRED_GROUPS`). It gets ALERT, ESCALATE or WATCH (the first one permitted) instead, and the reason is recorded (`test_a_provisional_finding_cannot_select_a_permission_action`).
- **A finding withheld by VS-01** gets an action from what is unknown:
  - a retrievable item → COLLECT_EVIDENCE;
  - a required authoritative item → REQUEST_CONTEXT, or HOLD_TOOL_CALL for a missing policy with a pending live operation;
  - an upstream finding → REQUEST_COP_SUPPORT;
  - contrary evidence → NONE (`candidate_disproved`);
  - nothing left to retrieve → NONE, routed assisted (`support_weak`).
- **Unchanged:**
  - PASS selects no remedial action;
  - the taxonomy;
  - `check_typed`;
  - PE-01, AU-01 and AU-02;
  - the human-required labels;
  - the quotient formula and its bands.

## 9. Tests

`tests/test_runtime_support.py` has 24 new tests, all offline.

| Area | Tests |
|---|---|
| Structure | Hard and graded checks separate and covering every finding check; every COP has components and thresholds equal to their formulas; ρ values are the existing telemetry levels |
| Strong evidence | `test_strong_evidence_is_supported`, `test_the_score_is_the_weighted_sum_it_reports` |
| Borderline | `test_borderline_evidence_is_provisional_and_names_its_limitations`, `test_the_same_warning_does_not_carry_a_high_severity_claim`, `test_the_same_borderline_evidence_on_unreliable_telemetry_is_insufficient_not_false` |
| Contradictory | `test_evidence_for_the_opposite_rejects_the_candidate`, `test_a_policy_deny_rejects_allowed`, `test_a_hard_check_failure_rejects_whatever_the_evidence` |
| Missing | `test_missing_evidence_is_unknown_never_evidence_against`, `test_low_support_for_anomalous_does_not_prove_normal`, `test_normal_needs_its_own_support_not_the_absence_of_anomaly`, `test_a_quiet_sufficient_metric_does_not_oppose_an_attention_label` |
| False-positive / false-negative risk | `test_false_positive_risk_an_unsupported_model_alarm_does_not_stand` (a 0.99-confident ANOMALOUS with quiet metrics is rejected), `test_false_negative_risk_a_quiet_reading_over_an_attention_state_does_not_pass`, `test_false_negative_risk_a_quiet_reading_on_missing_records_does_not_pass`, `test_self_reported_confidence_is_not_an_input` |
| Risk Mitigation | `test_risk_label_is_checked_against_the_register_recomputation` |
| Triage and action | Provisional cap; quotient separate; insufficient support asks for what is unknown; a graph run carries VS-01 and its record for every COP |

Existing tests restated by intent. Each one is a behaviour change for review:

| Test | Before | Now | Why |
|---|---|---|---|
| `test_runtime_graph::test_policy_enforcement_paths` | With no policy store, the condition-only reading stood | INCONCLUSIVE: VS-01 INSUFFICIENT, `required_unknown == ["policy_evaluation"]` | A missing policy cannot be replaced. The policy evaluation is required for every Policy Enforcement label. **Needs your agreement** (section 11) |
| `test_runtime_error_paths::test_an_intervention_with_no_policy_basis_is_blocked_before_approval` | BLOCK reached PE-01 and was blocked | The INCIDENT is provisional (telemetry only), so BLOCK becomes ALERT before PE-01 | A permission action needs SUPPORTED evidence. PE-01 is still covered by the verifier-level test and by the new `test_pe01_still_blocks_a_supported_intervention_with_no_policy_basis` |
| `test_runtime_live_path::test_model_text_claiming_approval_does_not_execute_anything` | Supervised mode interrupted for approval | ALERT is selected and the case is routed to a human; nothing executes | Same reason. A person still decides, because INCIDENT is a human label |
| `test_runtime_triage::test_s02_...` | – | Passes after the provisional limitations were added to the triage record's limiting evidence | – |

Full suite: 397 passed, 1 skipped.

## 10. Disclosure: changes made after the first bench run

The first run of the graded layer withheld two bench findings that are correct by ground truth:
- `runtime_monitoring:abnormal` (DEGRADED);
- `recovery_resilience:normal` (RECOVERED).

Investigating them showed that the evidence semantics were wrong, not the thresholds. I made four corrections. **No threshold or weight value was changed.**

1. **Runtime Monitoring.** A telemetry measure that cannot be computed is itself an observation of degraded telemetry; the existing rule reads coverage UNKNOWN as DEGRADED. Its UNKNOWN rows are therefore WARNING-level observations (`unknown_is_observation`). Also, strength for an attention label is now relative to the severity claimed, so a WARNING fully supports a LOW-severity DEGRADED.
2. **Recovery & Resilience.** RECOVERED presupposes a failure. The failure metric in an attention state is the precondition of the judgement, not evidence against it, so the failure metrics are contextual and neutral (`precondition`).
3. **Sufficient vs necessary metrics.** Initially I tied STALLED to `insights.stalled` and let quiet metrics oppose any attention label. The existing rules use those metrics as sufficient conditions, not necessary ones. Quiet metrics are now neutral for attention labels, except where the metrics define the label (Anomaly Detection and Runtime Monitoring).
4. **Cited upstream findings.** When a COP's rule derives its label from a verified upstream finding and cites it (Behavior Analytics DRIFTING from Context Memory, Incident Response INCIDENT from Recovery FAILED_RECOVERY), that finding is direct evidence, like a cited record. Both tests that broke on this were scenario tests of that design.

After these corrections the two bench findings are PROVISIONALLY_SUPPORTED, not SUPPORTED, because each rests on one independent source. Please review these four as semantics.

## 11. Limitations, what needs agreement, and what needs live validation

**Needs your agreement:**
1. Policy Enforcement now requires the policy evaluation for *every* label. A condition-only VIOLATION with no policy store is no longer accepted. This follows "a missing policy cannot be replaced", but it removes a reading that the rule's own design kept.
2. Provisional findings are barred from permission and recovery actions.
3. The relevance tiers (1.0 / 0.5 / 0.25) are a design choice: direct, supporting, contextual. They are the only weights that are not taken from existing constants.

**Known limitations:**
- The twelve generic profiles judge records by verified citation. They do not re-read each record independently, as the six hand-specified ones do. Their quiet-label support from "read, nothing cited against" is half strength by assumption.
- Validated memory is empty in every bench case, so it is always unknown (0.25 / W of every assessment). No assessment reaches band 5, because band 5 requires every component to be known.
- No outcome history exists yet to check whether SUPPORTED findings are right more often than PROVISIONAL ones. The decision is explainable but **uncalibrated**. The reviewer feedback on memory records, and the 20-user simulation, are where that calibration can come from.
  - The known memory defect (re-running a case overwrites reviewer feedback) matters here, and it is still unfixed.

**Needs live validation (no paid calls were made):**
- The RECHECK cases.
- Fleet F2.
- How live model candidates score under VS-01. In replay, every LLM candidate is a recorded reply.

These remain blocked on `ANTHROPIC_API_KEY` being set in the environment settings, and they stay within the approved rerun scope.

## 12. Files changed

| File | Change |
|---|---|
| `collector/cops/runtime/support.py` (new) | Hard/graded partition, per-COP profiles, evaluators, derived thresholds, `assess`, VS-01 mapping, `static_profiles` |
| `collector/cops/runtime/graph.py` | `independent_verify` computes support, adds VS-01, sets `finding.verification_support` and `finding.provisional` |
| `collector/cops/runtime/verifiers.py` | VS-01 registered (category VS, BLOCKS_FINDING) |
| `collector/cops/runtime/triage.py` | 1–5 band derived from the support decision; VS-01 causes; provisional cap on permission and recovery actions; quotient formula unchanged |
| `collector/cops/runtime/test_bench.py`, `evaluation.py`, `bench_report.py` | Support record in traces, summary and profiles; dashboard section and per-case step |
| `tests/test_runtime_support.py` (new), four restated tests, one new PE-01 test | Section 9 |
| `docs/cops/evaluation/*_before_support.*` (new) | Snapshot before this change |
| `docs/cops/RUNTIME.md` | VS-01 in the canonical verification sequence |
