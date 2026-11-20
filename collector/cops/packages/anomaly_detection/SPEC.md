# Anomaly Detection

METRICS.yaml is the contract the runtime reads. This file explains it.

## Identity

| | |
|---|---|
| Key | `anomaly_detection` |
| Classification | LLM COP (one of the ten, `profiles.LLM_COPS`); the package decides alone where nothing needs interpreting |
| Behavioural question | Is the observed behaviour materially different from what is expected in this context? (`specs.py`); the workbook (row 31): Is the agent behaving meaningfully differently from what is expected? |
| Labels | NORMAL, ANOMALOUS, INDETERMINATE, or INCONCLUSIVE (unchanged) |
| Permitted actions | NONE, WATCH, ALERT, REQUEST_CONTEXT, PASS_TO_COP (unchanged; never BLOCK) |
| Consumes | Runtime Monitoring, Behaviour Analytics (`specs.CONSUMES`) |
| Consumed by | Classification & Tagging, Policy Enforcement, Risk Mitigation, Incident Response; Behaviour Analytics reads its earlier-cycle finding |

## Purpose

Say whether the agent behaves meaningfully differently from comparable sessions. Unusual is not prohibited: no
answer here is a policy judgement, the vocabulary has no violation, and permission stays Policy Enforcement's
question. Each answer keeps four things apart:

| Layer | What it is | Where it is kept |
|---|---|---|
| Observed measure | the metric value and the state its approved threshold gives it | `basis.measures[]` (`value`, `state`) |
| Baseline | the comparison group, its size, the minimum and a version digest, as the metrics used it | `measures[].baseline` |
| Deviation | a measure in EXCEPTION or CRITICAL against a sufficient baseline (`deviation_states`) | `basis.deviating`; WARNING in `basis.warnings` |
| Meaning | whether deviations are a meaningful anomaly or legitimate variation | AD-5, from the model's verified interpretation |

## Deterministic and model operations

| Operation | By | Code |
|---|---|---|
| Metrics and their states | deterministic | `signal_metrics.compute` |
| Baselines | deterministic | `signal_metrics.estate` (read, not recomputed, by `cop_package._ad_baselines`) |
| Presence, reliability, sufficiency | deterministic | `evidence_assessment.assess` |
| AD-1 to AD-4 | deterministic | `cop_package.observe_anomaly_detection` |
| AD-5 when no question deviates, or evidence or a baseline is short | deterministic | `cop_package._ad5`, `decide_anomaly_detection` |
| AD-5 when a question deviates: meaningful anomaly or legitimate variation | model | the investigation loop (`graph.llm_*`) |
| Reading the verified interpretation back as AD-5 | deterministic | `cop_package.interpreted_anomaly_detection` |
| Verification, triage, memory write, decision record | deterministic | `verifiers.py`, `triage.py`, `graph.validate_memory_update`, `decision.py` |

The model is called only when AD-1 to AD-4 observed a deviation. Without a model (dry run, or no provider), a
deviation leaves AD-5 and the label INCONCLUSIVE.

## Inputs and outputs

- Rows: the case session's observables and the fetched estate. Metrics: `signal_metrics.compute(..., catalogue=True)`.
- Tool (`rules.DETERMINISTIC_TOOLS`): `assess_anomaly_questions`, which reads the estate baselines, validated
  `anomaly_observation` memory and the upstream findings. The model's tools are unchanged
  (`retrieve_trace_window`, `compare_versioned_baseline`, `find_similar_validated_cases`, `calculate_deviation`,
  `analyse_tool_call_sequence`); it also sees the tool's results as `baseline_tool_results`.
- Output: the finding, with `cop_specific.questions`, `drivers`, `contrary`, `limitations`, `recipients`,
  `memory_payload`, and `decided_without_model`; for a model finding also `detail` and the rule reading as
  `baseline`; and the common decision record (`decision.py`).

## Baselines

| Measure | Comparison group | Minimum | Direction judged |
|---|---|---|---|
| `insights.token_outlier` | every session of the fetched estate with token counts, this one included; estate median | 5 sessions (`min_sessions_for_baseline`) | above only |
| `insights.duration_outlier` | every timed step of the estate; a step over 10x the median step (`duration_outlier_multiple`) | none configured | above only |
| `activity.action_rate_ratio` | sessions of the same agent tool, this one included; median actions per minute | 5 sessions | above only |
| `path.transition_novelty` | transitions of the other sessions of the same agent tool | 5 sessions and 5 transitions in this session (`min_transitions_for_novelty`) | above only |
| `access.first_seen_mcp` | earlier sessions of the same operator | one earlier session (with none, every server is new by definition) | above only |
| `insights.repetition`, `insights.retry`, `insights.failed` | none: approved absolute thresholds per session | none | above only |

Not taken into account, because no approved specification asks for it or no definition exists: a temporal window
(every session of the fetched estate weighs the same; limitation `baseline_window`), the model, agent or task class
(`task_adjusted_anomaly_score` is NOT_CALCULATED), and a per-agent tool distribution (`tool_use_deviation_per_agent`
is NOT_CALCULATED). The token and rate baselines include the session judged, as `signal_metrics` computes them; the
measured effect and a leave-one-out proposal are in DECISIONS.md (U-AD7). Task-class peers are compared leave-one-out,
and only as legitimacy context.

## The five questions

Workbook COMPLETE_DELEGATION_DATA rows 31-35; metric list row 36; partial-input rule row 4.

### AD-1 Is the agent repeating actions, retrying operations or failing unusually often?

`insights.repetition`, `insights.failed` (required), `insights.retry`, `telemetry.turn_linkage` (when applicable).
Blocking check: CONFLICTING_ACTION_SOURCES.

- UNUSUALLY_FREQUENT: any of the three in EXCEPTION or CRITICAL.
- NOT_UNUSUAL: each within its limit or not applicable, and turn linkage not degraded (repetition is counted
  within a turn, so unlinked actions are not compared).
- INCONCLUSIVE: a measure missing, or turn linkage in EXCEPTION or CRITICAL.
- Behaviour Analytics LOOPING or POOR_RECOVERY is recorded in `basis.upstream` with whether it is independent.

### AD-2 Is its token consumption or execution duration significantly different from comparable sessions?

`insights.token_outlier`, `insights.duration_outlier` (required); `token_expansion_per_turn` (reported, no
threshold). Blocking check: CONFLICTING_SOURCES.

- SIGNIFICANTLY_DIFFERENT: either in EXCEPTION or CRITICAL.
- COMPARABLE: both within their limits.
- BASELINE_INSUFFICIENT: what is not within limits lacks a sufficient baseline (named with its size and minimum).
- INCONCLUSIVE: a measure missing for another reason (for example no token counts in this session).
- Lower token use than the estate is measured and not judged (no lower limit approved).

### AD-3 Is the agent performing actions at an unusually high or low rate?

`activity.action_rate_ratio` (required). Blocking check: CONFLICTING_ACTION_SOURCES.

- RATE_UNUSUAL: EXCEPTION or CRITICAL (an unusually high rate).
- RATE_WITHIN_BASELINE: within the upper limit. A low rate is measured and NOT_EVALUATED: no lower limit is
  approved (limitation LIMIT_NOT_APPROVED, as ID-4 treats depth and breadth, D18 there).
- BASELINE_INSUFFICIENT: fewer than five sessions of the tool, or no median rate.

### AD-4 Is the agent following an unfamiliar execution path or accessing previously unseen MCP servers?

`path.transition_novelty`, `access.first_seen_mcp` (required); `telemetry.lane_linkage` (when applicable);
`interaction.mcp_servers`, `interaction.subagent_lanes`, `resource_access_novelty_files` (reported);
`tool_use_deviation_per_agent` (NOT_CALCULATED). Blocking check: CONFLICTING_ACTION_SOURCES.

- UNFAMILIAR: novelty or a first-seen server in EXCEPTION or CRITICAL against a sufficient baseline.
- FAMILIAR: both within their limits, lane linkage not degraded.
- BASELINE_INSUFFICIENT: the open measures lack a baseline; a first-seen count for an operator with no earlier
  session is not counted as novelty.
- INCONCLUSIVE: a measure missing for another reason, or lane linkage degraded.

### AD-5 Do the observed deviations constitute a meaningful behavioural anomaly, rather than legitimate task variation?

`task_adjusted_anomaly_score` (NOT_CALCULATED). Depends on AD-1 to AD-4 and Runtime Monitoring.

- NO_DEVIATION: no question deviates and every question was answered (deterministic).
- UNDETERMINED: no question deviates, and Runtime Monitoring reads the telemetry INSUFFICIENT or a question is
  BASELINE_INSUFFICIENT or INCONCLUSIVE (deterministic).
- MEANINGFUL_ANOMALY or LEGITIMATE_VARIATION: a question deviates and the model's verified interpretation is
  ANOMALOUS or NORMAL; UNDETERMINED when it is INDETERMINATE.
- INCONCLUSIVE: a question deviates and there is no verified interpretation (no model, the investigation stopped,
  or a verifier rejected it; the reason says which, LEGITIMACY_NOT_ESTABLISHED for a NORMAL without checkable
  context for every deviation).
- With a deviation, `basis.context` lists for each deviating measure the checkable context that could explain it:
  task-class peers (tokens and action rate; the `task_classes` record, at least 5 peers, the same metric function
  and thresholds) and validated NORMAL precedents. An authorised workload change or a task's expected volume has
  no approved record and is not accepted (`context_not_accepted`).

## Authority

It proposes NONE, WATCH or ALERT (and REQUEST_CONTEXT in the rule reading). Triage and the authority check decide
what is recorded or executed. In dry run nothing executes.
