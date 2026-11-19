# Runtime Monitoring: interpretation

## Pipeline

All steps are deterministic. Runtime Monitoring makes no model call today.

| Step | Code | Deterministic or model |
|---|---|---|
| Compute metrics | `signal_metrics.compute(rows, cfg, computed_at, catalogue=True)` | deterministic |
| Assess evidence per question | `evidence_assessment.assess` | deterministic |
| Observe and answer each question | `cop_package.observe_runtime_monitoring` | deterministic |
| COP label (SUFFICIENT / DEGRADED / INSUFFICIENT) | `rules.runtime_monitoring`, which calls `interpret.RuleInterpreter.runtime_monitoring` | deterministic |
| Verify the finding | `graph.independent_verify` and `verifiers.py` | deterministic |
| Select the action | `triage.py` (`LABEL_ACTIONS`, `THRESHOLDS`) | deterministic |
| Write memory | `graph.validate_memory_update` and `MemoryGateway.write` | deterministic |

**Operations that could use a model later, not built:**
- explaining which collector is right when sources disagree (the `telemetry_integrity` specialist in `triage.SPECIALISTS`, recorded as a dry-run referral only);
- summarising the cause of a telemetry gap for a person.

Either needs your approval. Neither may replace a deterministic answer or verify itself.

## How the current label is decided

`interpret.RuleInterpreter.runtime_monitoring`, unchanged:
- **INSUFFICIENT** when `telemetry.collector_coverage` or `telemetry.unknown_share` is EXCEPTION or CRITICAL against its approved threshold.
- **DEGRADED** when any of the five telemetry metrics is WARNING, EXCEPTION or CRITICAL, or coverage is UNKNOWN.
- **SUFFICIENT** otherwise.
- `rules.runtime_monitoring` makes the label INSUFFICIENT when no telemetry metric could be judged at all.

The label reads only the five `telemetry.*` metrics. It does not read ingest times, evidence age, continuity or provenance. Those are exactly the cases where the harness finds it too confident (`DECISIONS.md` D1).

## Uncertainty handling

- **Missing data.** A required metric without a value makes the question INSUFFICIENT, with the metric and its reason. A NOT_APPLICABLE value satisfies only a requirement marked REQUIRED_WHEN_APPLICABLE.
- **No comparison possible.** With one source, or no event seen twice, RM-2 is INSUFFICIENT. Agreement is never assumed. For RM-3 and RM-5 it is listed under `not_assessed`.
- **Checks that cannot run.**
  - A check with no approved limit is NOT_EXECUTED (LIMIT_NOT_APPROVED).
  - A check with no evaluation time is NOT_EXECUTED (NO_EVALUATION_TIME).
  - A check with no input is EVIDENCE_MISSING.
  - None of these is ever treated as passed.
- **Unbuilt metrics.** `collector_heartbeat` and `collector_errors` are reported NOT_CALCULATED and listed under `not_assessed` in every answer that names them.
- **Good and bad results are not the same as sufficiency.** A verified bad result is a SUFFICIENT answer with a finding marked `answer`. Examples are sources disagreeing (RM-2), no turn ids (RM-3) and stale evidence (RM-4). The same failure in a question it does not ask about is a finding marked `context`, or `blocking` where the question lists it.
- **No single score.** The mapping document's `evidence_sufficiency_score` is deliberately not built.

## Thresholds

The only thresholds used are those already in `metrics_config.json` `thresholds`, through the metric states they give. RM-1 and RM-3 use definitional cut points: none missing, all linked, none linked. `evidence_limits` has no approved values, so the harness passes test values and says so.
