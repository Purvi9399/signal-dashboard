# Anomaly Detection: decisions

Proposed on 2026-11-20. D-AD1 approved by purvi at 12:13Z; revision 2 (D-AD15 to D-AD20, U-AD2, U-AD3, U-AD7) made
at her request (12:13Z, 12:24Z) and awaiting her review. No label name, behavioural question or threshold, and
not the metrics engine, is changed.

## Shared architecture

| # | Decision | Alternatives considered | Rationale |
|---|---|---|---|
| D-AD1 (approved 12:13Z) | An LLM COP whose package decides its label skips the model. `rules.anomaly_detection` sets `cop_specific.decided_without_model` when AD-5 is NO_DEVIATION or UNDETERMINED; `graph.evidence_precheck` then publishes that finding (no baseline beside it, so it cannot support itself) with stop reason `decided_by_package_without_model`, which `graph.route_after_precheck`, `test_bench.dimensions` and `live_eval.verify_records` treat like the evidence precheck: no call by design (`graph.NO_MODEL_NEEDED`) | always call the model and bind its label to AD-5; call it and record the package beside it, as before | purvi 11:11Z: "Determine where deterministic calculations are sufficient and where LLM interpretation is genuinely necessary". With no deviation there is nothing to interpret. The other two keep a paid call on every quiet session and let the model reach ANOMALOUS where no question deviates |
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

## Revision 2 (purvi 12:13Z and 12:24Z): U-AD2, U-AD3, U-AD7

Ground truth for this revision was committed before the code (3400172) and the harness met it on its first run.

| # | Decision | Alternatives considered | Rationale and consequence |
|---|---|---|---|
| D-AD15 | Legitimacy context. For each deviating measure the package lists the checkable context that could explain it (AD-5 `basis.context`, `cop_package._ad_context`). Two sources: task-class peers (the existing `task_classes` record; the session against the other sessions of its class, through `signal_metrics.m_token_outlier` / `m_action_rate` on `signal_metrics.estate` of the peers and the approved thresholds; at least `min_sessions_for_baseline` peers) for tokens and action rate only; and a VALIDATED NORMAL `anomaly_observation` naming the measure | accept the model's stated reason; accept a task plan's objective; a new "workload change" record | purvi: "must cite independently checkable evidence". The two sources are the ones the case and memory already hold. No approved record of an authorised workload change or of a task's expected volume exists, so neither is accepted (`context_not_accepted`); inventing one would invent a data contract |
| D-AD16 | VS-01 and DS-03 re-check that context from the case rows, the case's task-class record and validated memory (`cop_package.ad_legitimacy`, registered in `cop_package.LEGITIMACY`), never from the model's text, and require the finding to cite it (a tool_observation on `assess_anomaly_questions` under `questions.AD-5.answer.basis.context`; SC-01 checks the cited value) | trust the cited tool result; let the model's claim decide | the verifier recomputes what it checks. A fabricated `explained: true` fails SC-01 (scenario fabricated_context_citation) |
| D-AD17 | VS-01 for Anomaly Detection: a deviation that checkable context explains is the premise of either reading, not evidence for it. The deviation metric, the versioned baseline and the rule baseline stop counting for or against it; a new direct component `legitimacy_context` supports NORMAL when every deviation is explained and cited, opposes NORMAL when one is not, and opposes ANOMALOUS when every deviation is explained. For the quiet label this applies only when every deviation is explained: one explained deviation never covers another (scenario explained_tokens_unexplained_retries) | drop the deviation metrics from VS-01; lower VS-01's thresholds | narrowest change that keeps every verifier running: the four outcomes are distinguished (meaningful anomaly supported; legitimate variation supported by context; unsupported explanation, rejected with `LEGITIMACY_NOT_ESTABLISHED`; insufficient evidence, INDETERMINATE or rejected). The component is not applicable where there is no deviation, so the package's NORMAL and every earlier ANOMALOUS are scored as before |
| D-AD18 | DS-03 passes a quiet model reading over a contradicting threshold reading only when the same re-check explains every deviation and the finding cites it (`baseline_contradiction_explained`); otherwise it fails as before, naming what is unexplained | remove DS-03 for Anomaly Detection | DS-03's own text says a quiet reading "must explain that"; this is the explanation, checked. COPs not in `LEGITIMACY` are unaffected (`test_ds03_is_unchanged_for_other_cops`) |
| D-AD19 | U-AD3. For Anomaly Detection's quiet label only (component flag `warning_is_partial`), a metric in WARNING is within the deviation limit: it counts as partial support (1 - the WARNING strength, 2/3) and is recorded in the component's basis; EXCEPTION and CRITICAL still oppose. The package keeps it in `basis.warnings` and does not count it as a deviation. below_threshold_warning is NORMAL, VS-01 PROVISIONALLY_SUPPORTED (WARN), action NONE | count WARNING as a deviation; drop it | the three layers stay apart: a warning (recorded, lowers support), a deviation (the model is consulted), a verified meaningful anomaly. Other COPs' quiet labels still read WARNING as opposing (`test_a_warning_still_opposes_another_cops_quiet_label`) |
| D-AD20 | A verified legitimate variation is remembered: `graph.validate_memory_update` writes a quiet finding when `cop_specific.remember_quiet` is set, which only `interpreted_anomaly_detection` sets, for AD-5 LEGITIMATE_VARIATION | write nothing for NORMAL, as before | without it a validated NORMAL precedent could never exist (the gateway validates only VERIFIED records). It is a precedent only after an independent reviewer validates it |

### U-AD7: leave-one-out comparison (no behaviour change)

`/mnt/project-files/pr4/verification/anomaly_detection/LOO_COMPARISON.json` compares 108 measures on the 33 harness
worlds, the three live-evaluation cases and five worlds built to expose contamination. The leave-one-out reading
uses `signal_metrics.estate` and the metric functions on the estate without the session: no second engine.

- No harness, evaluation or bench world changes state.
- Two contamination patterns change it:
  1. **Self-inclusion meets the minimum.** With four earlier sessions, the session itself makes the fifth, so
     `token_outlier` (CRITICAL) and `action_rate_ratio` (NORMAL or EXCEPTION) are judged; left out, there is no
     baseline (UNKNOWN). Worlds minimum_estate_4_others and rate_at_minimum.
  2. **Self-inclusion masks a spike.** When spikes approach half of the estate, the session tips the median: 1.95
     (NORMAL) against 40 (CRITICAL) left out. Worlds two_spikes_among_5 and half_estate_shifted.
- With five or more other sessions and the spike rare, the two agree (minimum_estate_5_others). Path novelty
  already excludes the session.

Proposal for purvi:
- Record a versioned leave-one-out reading beside each self-including measure in `_ad_baselines`, using the same
  functions. Let it decide only once approved.
- Count the minimum over other sessions.
- Until then, the scenarios baseline_contamination_half_estate and baseline_minimum_counts_self pin today's
  behaviour; they do not endorse it.

### Restated by this revision

- C-AD1 is superseded by design revision R-AD1 (GROUND_TRUTH.yaml): below_threshold_warning is NORMAL again, with the WARNING qualified.
- R-AD2: legitimate_retries_interpreted and legitimate_large_task_interpreted keep their answers and labels, and now name LEGITIMACY_NOT_ESTABLISHED.
- `test_evidence_assessment.py`: `support.py` and `verifiers.py` read the package (the legitimacy re-check).
- Bench: unchanged at 48 PASS, 1 FAIL, 5 INCONCLUSIVE; no label moved in this revision.

## For purvi's decision

| # | Question |
|---|---|
| U-AD1 | Approved 12:13Z (D-AD1) |
| U-AD2 | Resolved by D-AD15 to D-AD18; for review. Still open inside it: an approved record of an authorised workload change or of a task's expected volume, so that either can be accepted as context |
| U-AD3 | Resolved by D-AD19; for review |
| U-AD4 | Kept: lower limits for tokens and action rate are not approved; the low direction stays NOT_EVALUATED |
| U-AD5 | Kept: no approved baseline window; limitation `baseline_window` |
| U-AD6 | Kept: first-seen MCP needs one earlier session of the operator; `min_sessions_for_baseline` is not applied to it without approval |
| U-AD7 | Measured (above): self-inclusion changes the reading in two patterns. Proposal: versioned leave-one-out beside the current reading, decisive only once approved |
| U-AD8 | Kept: the comparison group is the estate or the agent tool; task-class peers are used only as legitimacy context (D-AD15), not as the baseline |
| U-AD9 | Open: redesign the contradictory scenario variant, or keep it as the package's catch |
| U-AD10 | Kept: `task_adjusted_anomaly_score` and `tool_use_deviation_per_agent` NOT_CALCULATED, needing a task-class baseline definition and an approved distance method |

## Not done, by instruction

No paid model call (every model reply is scripted, simulated=True). No Supabase replay, collector change or
dashboard work. No change to the legacy four-COP runner, the verifiers, the metrics engine or a threshold.
