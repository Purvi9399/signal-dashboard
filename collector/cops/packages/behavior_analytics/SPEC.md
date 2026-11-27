# Behavior Analytics: specification

COP 11 in the build order: Runtime Monitoring, Interaction Discovery, Audit & Traceability, Policy Enforcement,
Anomaly Detection, Resource Management, Classification & Tagging, Risk Mitigation, Incident Response, Recovery &
Resilience, then Behavior Analytics. The workbook and `docs/cops/cop_catalogue.json` number this COP 12. The build
order is recorded apart from that numbering.

`METRICS.yaml` is the contract the runtime reads. This file explains it.

## Behavioural question

- `cops/specs.py` (unchanged): "What behavioural pattern best characterizes this execution or sequence of
  executions?"
- Workbook COMPLETE_DELEGATION_DATA row 181: "What behavioural patterns characterize the agent over time?". Rows
  181, 183, 185 and 187 give the four questions below; row 190 lists the 37 metrics.

## Questions

| Id | Question | Answers |
|---|---|---|
| BA-1 | What are the agent's typical action sequences, tool-use patterns and execution behaviours? | RETRY_AFTER_FAILURE, REPEATED_ACTIONS, WORKLOAD_ONLY, NO_PATTERN |
| BA-2 | How does its current behaviour compare with its own historical behaviour and comparable agents? | OUTSIDE_USUAL_RANGE, WITHIN_USUAL_RANGE |
| BA-3 | Which behavioural tendencies persist across sessions? | ISOLATED, RECURRING, WORSENING, NO_CURRENT_PATTERN |
| BA-4 | How does its behaviour vary across tasks, sessions, models and contexts? | CONTEXT_ALTERED, NO_ALTERATION_SHOWN |

Every question can also be INCONCLUSIVE. BA-3 depends on BA-1 and BA-2 (`METRICS.yaml depends_on`).

## How the answers make the label

- BA-1 gives the pattern: RETRY_AFTER_FAILURE is POOR_RECOVERY, REPEATED_ACTIONS is LOOPING.
- With no pattern in BA-1, BA-4 CONTEXT_ALTERED gives DRIFTING.
- Otherwise STABLE. BA-1 unanswered is INCONCLUSIVE.
- BA-2 and BA-3 give the scope the label is published with (`behaviour_status.scope`): this execution only,
  recurring across distinct sessions, worsening across consecutive sessions, or undetermined. A label is never
  published as a persistent tendency: persistence needs an approved window (U-BA3).
- OVER_EXPLORING stays unreachable: exploration_rate is not computed (U-BA7).

## Metrics (37, registry consumers = behavior_analytics)

| Kind | Count | Metrics |
|---|---|---|
| Judged against approved thresholds (category E) | 20 | the five metric_keys (insights.repetition, insights.retry, insights.failed, insights.stalled, oversight.fast_approvals) and insights.reverted, insights.blocked, interaction.subagent_lanes, interaction.mcp_servers, telemetry.turn_linkage, telemetry.lane_linkage, telemetry.unknown_share, insights.token_outlier, insights.duration_outlier, activity.action_rate_ratio, path.transition_novelty, access.first_seen_mcp, insights.outside, oversight.declined_inferred, insights.no_oversight |
| Reported, never judged (no approved threshold) | 7 | behavior_action_sequence_frequency, behavior_tool_usage_distribution, behavior_tool_transition_frequency, behavior_model_variation, behavior_repetition_trend, behavior_retry_trend, behavior_failure_trend |
| NOT_CALCULATED (registry NOT_IMPLEMENTED, no definition) | 10 | behavior_execution_pattern_frequency, behavior_historical_deviation_score, behavior_peer_deviation_score, behavior_approval_pattern_trend, behavior_pattern_persistence, behavior_change_point_score, behavior_pattern_stability, behavior_task_variation, behavior_context_variation, behavior_investigation_prioritya |

The spec's missing_metrics (loop_score_per_lane, exploration_rate, planning_deviation, recovery_pattern) are
NOT_CALCULATED with no registry metric. Each NOT_CALCULATED metric's missing input is in `METRICS.yaml` and is
recorded as a NOT_CALCULATED limitation on every finding.

## Event identity

insights.retry and insights.failed count rows. The same failed call seen by three collectors counts three times.
BA-1 recounts both over distinct, undisputed events (one row per tool_use_id) and judges the recount with the same
approved thresholds (D-BA3). The metrics themselves are unchanged (U-BA1).

## Temporal definitions (D-BA6)

| Term | Definition |
|---|---|
| Same session | the same session_id |
| Distinct sessions | different session_ids |
| The agent's history | this operator's other sessions of the same tool that started before this one |
| Consecutive observations | those sessions in start order |
| Unordered session | a session without a start timestamp: listed (UNORDERED_HISTORY), not used |
| Window | none approved: all ordered history is read |

## Deterministic, no model

Every answer rests on approved metric states, event identities and timestamps (`model_role: none`,
`profiles.DETERMINISTIC_COPS`, workbook Sheet1 row 74 "Rules").

## Authority

NONE and WATCH only. "Observe, classify, record, pass. No enforcement from behavioral characterization alone"
(agents-harness row 16). Nothing is enforced, blocked or paused.
