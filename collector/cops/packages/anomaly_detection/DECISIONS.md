# Anomaly Detection: decisions

Proposed on 2026-11-20; awaiting purvi's review. No label name, behavioural question, threshold, verifier or the
metrics engine is changed. One shared-runtime change, D-AD1, needs purvi's approval: it is listed first.

## Shared architecture (needs approval)

| # | Decision | Alternatives considered | Rationale |
|---|---|---|---|
| D-AD1 | An LLM COP whose package decides its label skips the model. `rules.anomaly_detection` sets `cop_specific.decided_without_model` when AD-5 is NO_DEVIATION or UNDETERMINED; `graph.evidence_precheck` then publishes that finding (no baseline beside it, so it cannot support itself) with stop reason `decided_by_package_without_model`, which `graph.route_after_precheck`, `test_bench.dimensions` and `live_eval.verify_records` treat like the evidence precheck: no call by design (`graph.NO_MODEL_NEEDED`) | always call the model and bind its label to AD-5; call it and record the package beside it, as before | purvi 11:11Z: "Determine where deterministic calculations are sufficient and where LLM interpretation is genuinely necessary". With no deviation there is nothing to interpret. The other two keep a paid call on every quiet session and let the model reach ANOMALOUS where no question deviates |
| D-AD2 | With a deviation, the model interprets it and `graph.independent_verify` reads its verified label back as AD-5 (`cop_package.interpreted_anomaly_detection`, registered in `graph.INTERPRETED`). The verifiers run before, unchanged | let the package label a deviation ANOMALOUS | AD-5 asks whether a deviation is meaningful or legitimate; only the model sees the task context, and the workbook names no deterministic rule for it |
| D-AD9 | `assess_anomaly_questions` is called before the model and takes no place in the tool-call sequence (`tools.UNSEQUENCED`). The model's tool-call ids are those of the recorded live run of 2026-11-02, so its replies still replay | number it like other baseline tools | the first suite run showed the replay of `anomaly_detection:abnormal` rejected by ID-02 and SC-01 because every cited id had moved by one; that is a numbering artefact, not a finding |

## Made in this package

| # | Decision | Alternatives considered | Rationale |
|---|---|---|---|
| D-AD3 | A deviation is a measure in EXCEPTION or CRITICAL against a sufficient baseline (`deviation_states`). WARNING is recorded in `basis.warnings` and not counted | count WARNING | the states the legacy rule acted on; no new cut-off |
| D-AD4 | Baselines are those `signal_metrics.estate` already computes, read and not recomputed (SPEC.md, Baselines), with their size, minimum and a version digest | per-tool or per-window baselines built here | no approved specification defines another comparison group; building one would invent methodology. What is not taken into account is a structured limitation (`baseline_window`) |
| D-AD5 | Lower token use and a lower action rate are measured and NOT_EVALUATED (limitation LIMIT_NOT_APPROVED) | judge them against a mirrored limit | no lower limit is approved (as ID-4, D18 there) |
| D-AD6 | A first-seen MCP count for an operator with no earlier session is BASELINE_INSUFFICIENT, not novelty | count every server as new | with no history every server is new by definition; reading that as unfamiliar would equate a first session with a deviation |
| D-AD7 | NORMAL needs all four questions answered without a deviation; a missing measure or baseline gives UNDETERMINED (INDETERMINATE) | NORMAL when one primary metric is judged, as the threshold reading did | the answers decide the label (requirement 6); an unanswered question is not evidence of normality |
| D-AD8 | AD-1 is its own question: repetition, retry or failure in EXCEPTION or CRITICAL is a deviation the model interprets | keep retry and repetition as support only | workbook row 31 asks it as a question. The threshold reading is unchanged as the recorded baseline |
| D-AD10 | A question whose blocking check (conflicting sources) fails or cannot run is INCONCLUSIVE; what it observed is kept as `observed_not_judged` | judge the observation anyway | the reliability contract of the accepted packages |
| D-AD11 | When VS-01 rejects a package NORMAL (a deviation metric in WARNING), the answers stay as given and the label is INCONCLUSIVE | relabel to INDETERMINATE | the Policy Enforcement precedent (U-PE1, A-PE2): the verifier is not weakened and the answers are not rewritten |
| D-AD12 | Behaviour Analytics LOOPING or POOR_RECOVERY is recorded beside AD-1 with whether its lineage is independent, and never makes AD-1 deviate | count it | its lineage overlaps AD-1's own metrics; counting it would double-count one observation |
| D-AD13 | Validated `anomaly_observation` records are listed as precedents for comparison only; they never change an answer | use precedents to decide | memory is not a baseline; no self-validation |
| D-AD14 | Unusual is never a violation: no answer names policy, and the vocabulary has no violation | | purvi 11:11Z: "Anomaly Detection must not equate novelty with wrongdoing" (scenario novelty_is_not_a_violation) |

## Ground truth corrected after the first run

Recorded in GROUND_TRUTH.yaml `post_run_corrections` with the reason; the meaning of the answers was not changed.

- C-AD1, below_threshold_warning: label NORMAL became INCONCLUSIVE. VS-01 reads WARNING as opposing a quiet label (U-AD3).
- C-AD2, legitimate_retries_interpreted: AD-5 LEGITIMATE_VARIATION became INCONCLUSIVE. VS-01 rejects a model NORMAL over a deviation (U-AD2).
- C-AD3, behaviour_analytics_correlated: Behaviour Analytics reads the world as POOR_RECOVERY, not LOOPING.

## Earlier tests and cases restated (behaviour changed by design, D-AD1)

These used Anomaly Detection on a quiet session as the generic vehicle for the model path, which no longer calls
the model. Each now gives the model a deviation to interpret, or moves where it needs a model PASS reading.

- Evaluation C13, C14, C16 (`evaluation._retried`, `_normal_retried`): a failing test retried, so the model is
  consulted. C14's scripted reply concludes ANOMALOUS, since VS-01 rejects NORMAL over the retry.
- `test_runtime_verifiers.py`: the module rows are a token spike; the true-claims, RP-01 and AU-01 tests conclude
  ANOMALOUS; MC-01's wrong value is 4.0 (40.0 is now the true ratio); DS-01's thin session has 40000 tokens.
- `test_runtime_error_paths.py`, three model-failure tests: run on the spike rows.
- `test_runtime_llm.py`, `test_runtime_live_path.py`: rows from `evaluation.sensitive_retried_session`. The
  allowlist test compares with `profiles.allowlists` (profile tools and baseline tools).
- `test_runtime_graph.py`: thin telemetry and the Runtime Monitoring verdict now decide INDETERMINATE or NORMAL
  without the model.
- `test_runtime_actions.py`, `test_runtime_triage.py` s04: the rows of case C16. s09 (an action proposed on PASS)
  moves to Knowledge & Policy Management's normal case.
- `test_runtime_live_eval.py`: Anomaly Detection may make no call on its normal case. `test_runtime_root_causes.py`:
  `anomaly_detection:missing` is INDETERMINATE (accepted by the case).
- `test_runtime_scenario.py` and `docs/cops/evaluation/scenario/contradictory.json`: the contradictory variant is
  decided INDETERMINATE (one collector, Runtime Monitoring INSUFFICIENT); the reviewer's REFUTED now applies to a
  verified finding. The other four variants' summaries are unchanged. The reviewer note no longer says "NORMAL".
- `test_evidence_assessment.py`: the users of the assessment include this harness and `graph.py`.

## For purvi's decision

| # | Question |
|---|---|
| U-AD1 | Approve D-AD1: an LLM COP's package may decide without the model where nothing needs interpreting |
| U-AD2 | LEGITIMATE_VARIATION is defined but cannot be verified: VS-01 (and DS-03 when the threshold reading is ANOMALOUS) rejects a model NORMAL over an observed deviation. Should VS-01 accept a verified explanation of a deviation as direct evidence? |
| U-AD3 | VS-01 treats WARNING as opposing a quiet label, while this package does not count WARNING as a deviation. Which should hold? |
| U-AD4 | Lower limits for tokens and action rate (both NOT_EVALUATED today) |
| U-AD5 | A temporal window for the baselines (every fetched session weighs the same) |
| U-AD6 | The minimum history for `access.first_seen_mcp` (one earlier session today; `min_sessions_for_baseline` is not applied) |
| U-AD7 | The token and rate baselines include the session judged, as `signal_metrics` computes them |
| U-AD8 | The comparison group is the fetched estate or the agent tool, not the model, agent or task class |
| U-AD9 | The contradictory scenario variant no longer shows a model's NORMAL being contradicted. Redesign it, or keep it as the package's catch |
| U-AD10 | Definitions for `task_adjusted_anomaly_score` and `tool_use_deviation_per_agent` (NOT_CALCULATED) |

## Not done, by instruction

No paid model call (every model reply is scripted, simulated=True). No Supabase replay, collector change or
dashboard work. No change to the legacy four-COP runner, the verifiers, the metrics engine or a threshold.
