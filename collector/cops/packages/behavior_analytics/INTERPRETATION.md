# Behavior Analytics: interpretation

Observation (`cop_package.observe_behavior_analytics`) and interpretation (`cop_package.decide_behavior_analytics`)
are separate functions. Both are deterministic. `rules.behavior_analytics` runs the legacy rule first, keeps its
reading in `cop_specific.legacy_reading`, then applies the package's answers (`rules._ba_questions`). The
`_with_pattern_persistence` wrapper then records Anomaly Detection's earlier finding.

## BA-1: execution behaviour

1. Recount insights.retry and insights.failed over distinct, undisputed events (`cop_package.ba_recount`): one row
   per tool_use_id; rows with collectors_agree false left out. Judge each recount with the approved threshold
   (retry 1/2/5, failed 1/5/20).
2. Recounted retry and failed both EXCEPTION or CRITICAL: RETRY_AFTER_FAILURE.
3. Else recounted retry, or insights.repetition (already distinct calls), EXCEPTION or CRITICAL: REPEATED_ACTIONS.
   Repetition whose every evidence row is disputed does not count.
4. Else a workload metric (token, duration or action-rate outlier) EXCEPTION or CRITICAL: WORKLOAD_ONLY. A
   workload outlier alone is not a behavioural pattern (WORKLOAD_NOT_A_PATTERN).
5. Else, a metric that deviates only on disputed rows: INCONCLUSIVE (CONFLICTING_SOURCES).
6. Else pattern metrics judged: NO_PATTERN. A raw metric that deviated only because of duplicate rows is named
   (DUPLICATE_TELEMETRY). A stall in a deviation state is reported (STALL_NOT_LABELLED).
7. Nothing judged: INCONCLUSIVE.

## BA-2: against usual behaviour

OUTSIDE_USUAL_RANGE when an estate- or tool-relative metric (token, duration and action-rate outliers, transition
novelty, first-seen MCP server, work outside the workspace) is EXCEPTION or CRITICAL. WITHIN_USUAL_RANGE when one is
judged and none deviates. The agent's own earlier values (count, median, max of repetition, retry and failed) and
its number of earlier sessions are reported. No peer comparison is made (PEER_COMPARISON_NOT_MADE, U-BA4).

## BA-3: over time

The metrics followed are the ones behind the current reading: those behind RETRY_AFTER_FAILURE or
REPEATED_ACTIONS, the workload metrics behind WORKLOAD_ONLY, and fast approvals when they deviate.

For each, over the agent's ordered earlier sessions (earlier sessions recounted by event, like BA-1):

| Reading | Condition |
|---|---|
| RECURRING | at least one earlier session in a deviation state |
| WORSENING | recurring, the state ranks (NORMAL 0, WARNING 1, EXCEPTION 2, CRITICAL 3) of the judged earlier sessions and this one never decrease in start order, and this one ranks above the first |
| ISOLATED | no earlier deviation, and at least min_sessions_for_baseline (5) earlier observed sessions. NOT_APPLICABLE counts as observed, not deviating |
| INCONCLUSIVE | fewer observed sessions (INSUFFICIENT_HISTORY) |

Across metrics: WORSENING if any, else RECURRING if any, else ISOLATED if all, else INCONCLUSIVE. NO_CURRENT_PATTERN
when nothing deviates now. This session without a timestamp: INCONCLUSIVE (MISSING_TIMESTAMPS). An earlier session
without one is listed and not used (UNORDERED_HISTORY). No pattern is called persistent (PERSISTENCE_NOT_ASSESSED).

What BA-3 never counts:
- a memory precedent (this COP's own earlier classification);
- Anomaly Detection's earlier finding (it reads the same metrics);
- duplicate rows of one event.

## BA-4: context

CONTEXT_ALTERED when a verified substantive Context & Memory CONTEXT_LOSS or CONTRADICTORY, or Workflow
Orchestration DRIFTING or UNEXPECTED_BRANCH, is consumed. An unverified finding is listed and alters nothing
(UPSTREAM_NOT_VERIFIED). An absent one is UPSTREAM_ABSENT. behavior_model_variation is reported.

## Label, severity, confidence, action

| Label | From | Severity | Confidence | Action |
|---|---|---|---|---|
| POOR_RECOVERY | BA-1 RETRY_AFTER_FAILURE | MEDIUM | 0.8 | WATCH |
| LOOPING | BA-1 REPEATED_ACTIONS | from the worst deviating state (EXCEPTION MEDIUM, CRITICAL HIGH) | 0.75 | WATCH |
| DRIFTING | BA-4 CONTEXT_ALTERED, no BA-1 pattern | LOW | 0.6 | WATCH |
| STABLE | otherwise | NONE | 0.75 | NONE |
| INCONCLUSIVE | BA-1 unanswered | NONE | 0.0 | WATCH |

Confidence is scaled by min(1, evidence_conf / 0.9), as the legacy rule does, and is uncalibrated (U-BA6).

## Anomaly Detection's earlier finding

`rules._pattern_persistence` keeps its statuses (NO_VERIFIED_HISTORY, NO_EARLIER_DEVIATION, RECURRING,
NOT_RECURRING, UNDETERMINED). When the earlier finding names this same session it reads SAME_EVENTS.
`counted_as_occurrence` is always false. It changes no answer, label, confidence or baseline.

## Readings that differ from the legacy rule

| Case | Legacy | Package |
|---|---|---|
| one failure seen by several collectors | LOOPING WATCH | STABLE NONE (DUPLICATE_TELEMETRY) |
| failures only on disputed rows | LOOPING WATCH | INCONCLUSIVE WATCH (CONFLICTING_SOURCES) |

Every other scenario reads as the legacy rule does. The scope is new.
