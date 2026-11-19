# Runtime Monitoring: interpretation

## Pipeline

All steps are deterministic. Runtime Monitoring makes no model call today.

| Step | Code | Deterministic or model |
|---|---|---|
| Compute metrics | `signal_metrics.compute(rows, cfg, computed_at, catalogue=True)` | deterministic |
| Assess evidence per question | `evidence_assessment.assess` | deterministic |
| Resolve raw_ref against the raw store | `evidence_assessment.resolve_raw_refs` | deterministic |
| Observe collector operation | `cop_package.collector_operation` | deterministic |
| Observe and answer each question | `cop_package.observe_runtime_monitoring`, run as the tool `assess_runtime_evidence` | deterministic |
| Threshold label | `rules.runtime_monitoring`, which calls `interpret.RuleInterpreter.runtime_monitoring` | deterministic |
| COP label from the threshold label and the five answers | `cop_package.aggregate_runtime_monitoring`, applied in `rules._apply_questions` | deterministic |
| Verify the finding | `graph.independent_verify` and `verifiers.py` | deterministic |
| Select the action | `triage.py` (`LABEL_ACTIONS`, `THRESHOLDS`) | deterministic |
| Write memory | `graph.validate_memory_update` and `MemoryGateway.write` | deterministic |

**Operations that could use a model later, not built:**
- explaining which collector is right when sources disagree (the `telemetry_integrity` specialist in `triage.SPECIALISTS`, recorded as a dry-run referral only);
- summarising the cause of a telemetry gap for a person.

Either needs your approval. Neither may replace a deterministic answer or verify itself.

## How the label is decided

Two steps, both deterministic.

**1. The threshold label** (`interpret.RuleInterpreter.runtime_monitoring`, unchanged):
- **INSUFFICIENT** when `telemetry.collector_coverage` or `telemetry.unknown_share` is EXCEPTION or CRITICAL against its approved threshold.
- **DEGRADED** when any of the five telemetry metrics is WARNING, EXCEPTION or CRITICAL, or coverage is UNKNOWN.
- **SUFFICIENT** otherwise.
- `rules.runtime_monitoring` makes it INSUFFICIENT when no telemetry metric could be judged at all.

**2. The five answers** (`cop_package.aggregate_runtime_monitoring`). Each question is put in one class, and each class has a ceiling: the best label it allows.

| Class | When | Ceiling |
|---|---|---|
| RELIABLE_SUFFICIENT | answered, and the answer shows nothing wrong | SUFFICIENT |
| DEGRADED_TELEMETRY | answered, and the answer shows a telemetry condition: an expected collector missing (RM-1), a collector that stopped part-way (RM-1), actions partly or not linked (RM-3), evidence not current or not continuous (RM-4), unknown share not NORMAL (RM-5) | DEGRADED |
| EVIDENCE_MISSING | not answered, because data that was supplied is empty or absent | DEGRADED |
| CONTRADICTORY_OR_UNVERIFIABLE | sources disagree (RM-2), or not answered because a blocking reliability check failed: action sources disagree, a raw_ref does not resolve, rows lack provenance, timestamps cannot be compared or are out of order; or an observation is dated after the evaluation time | DEGRADED |
| ASSESSMENT_INCONCLUSIVE | not answered only because something could not run: no approved limit, no raw store, no evaluation time, a metric not built, no history, no comparison possible, or an input never supplied | SUFFICIENT, with a qualification |

The COP label is the worse of the threshold label and the worst ceiling. Consequences:
- A blocking reliability failure in any question can never give SUFFICIENT.
- Questions never raise the label to INSUFFICIENT; that stays with the approved thresholds.
- Questions never make the label better than the threshold label.
- When the questions lower the label, the action, severity and evidence confidence follow the label (DEGRADED: WATCH, LOW), and the finding gains one checkable claim per question that set it, citing the `assess_runtime_evidence` tool result.
- ASSESSMENT_INCONCLUSIVE leaves the label alone and is listed in `qualifications`, and the rationale says which questions were not fully assessed.

**Observed condition and evidence quality stay apart.** DEGRADED_TELEMETRY is an observed condition of the telemetry. EVIDENCE_MISSING and CONTRADICTORY_OR_UNVERIFIABLE are about the quality of the evidence. ASSESSMENT_INCONCLUSIVE is neither. The three labels cannot say which, so the class is published beside the label in `cop_specific.evidence_assessment` (see CONNECTIONS.md).

**Supplied versus missing.** A field that no row carries at all, or an event kind that never arrived for a tool that declares no capability for it (`EVENT_KIND_BASES`: results, plans, compactions, config events, approvals), was not supplied to the assessment. That makes the question ASSESSMENT_INCONCLUSIVE. A field that is present but empty is evidence missing. This keeps fixtures and loaders that never selected a column from being read as degraded telemetry (DECISIONS.md D10).

**RM-5 is not counted twice.** Its causes taken from RM-1 to RM-4 are already counted in those questions. RM-5's own contribution is the unknown share and its own blocking checks. When it is uncertain only because of an earlier question, its class is that question's (the label cannot change, since the label takes the worst class anyway).

**An observation dated after the evaluation time** makes STALE COMPARISON_NOT_POSSIBLE. Unlike other comparisons that cannot be made, it contradicts the clock, so it counts as CONTRADICTORY_OR_UNVERIFIABLE.

## Uncertainty handling

- **Missing data.** A required metric without a value makes the question INSUFFICIENT, with the metric and its reason. A NOT_APPLICABLE value satisfies only a requirement marked REQUIRED_WHEN_APPLICABLE.
- **No comparison possible.** With one source, or no event seen twice, RM-2 is INSUFFICIENT. Agreement is never assumed. For RM-3 and RM-5 it is listed under `not_assessed`.
- **Checks that cannot run.**
  - A check with no approved limit is NOT_EXECUTED (LIMIT_NOT_APPROVED).
  - A check with no evaluation time is NOT_EXECUTED (NO_EVALUATION_TIME).
  - Provenance resolution with no raw store is NOT_EXECUTED (RAW_STORE_NOT_SUPPLIED).
  - A check with no input is EVIDENCE_MISSING.
  - None of these is ever treated as passed.
- **Unbuilt metrics.** `collector_heartbeat` and `collector_errors` are reported NOT_CALCULATED and listed under `not_assessed` in every answer that names them.
- **Provenance.** A raw_ref resolves when a stored source event of the same session has it as its `source_ref`, as the file its `source_ref` points into (`path:line`), or as its `file_origin`. Every `derived_from` id must be a stored `source_id` of the same session. A ref that does not resolve fails the check; it is never taken as authentic.
- **Collector operation.** A collector that contributed has stopped when, after its last observation, the session holds an event of a type it reported earlier that only other collectors saw. When `collector_capability` rows are supplied, a collector reported `silent`, or last seen before the session ended, has also stopped. The stop is in RM-1's basis (`stopped`, `stopped_because`); RM-1's outcome still says whether every expected collector contributed.
- **Good and bad results are not the same as sufficiency.** A verified bad result is a SUFFICIENT answer with a finding marked `answer`. Examples are sources disagreeing (RM-2), no turn ids (RM-3) and stale evidence (RM-4). The same failure in a question it does not ask about is a finding marked `context`, or `blocking` where the question lists it.
- **No single score.** The mapping document's `evidence_sufficiency_score` is deliberately not built.

## Thresholds

The only thresholds used are those already in `metrics_config.json` `thresholds`, through the metric states they give. RM-1 and RM-3 use definitional cut points: none missing, all linked, none linked. `evidence_limits` has no approved values, so the harness passes test values and says so.
