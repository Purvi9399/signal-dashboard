# Resource Management

METRICS.yaml is the contract the runtime reads. This file explains it.

## Identity

| | |
|---|---|
| Key | `resource_management` |
| Classification | deterministic (one of the eight) |
| Behavioural question | Is the agent consuming resources within its authorized and operational limits? (workbook COMPLETE_DELEGATION_DATA row 146); `specs.py`: "Is resource consumption within entitlement and acceptable operating bounds?" |
| Labels | NORMAL, INEFFICIENT, EXCESSIVE, UNAUTHORIZED, INDETERMINATE, or INCONCLUSIVE (unchanged) |
| Permitted actions | NONE, WATCH, ALERT (`specs.py`, unchanged) |
| Consumes | Runtime Monitoring (`specs.SPEC_CONSUMES`), Interaction Discovery (`specs.TOPOLOGY_CONSUMES`); Workload Optimization as earlier-cycle feedback (`specs.FEEDBACK`) |
| Consumed by | Workload Optimization, Risk Mitigation, Incident Response |
| Legacy runner | `cops/interpret.py RuleInterpreter.resource_management` is unchanged and still drives the 4-COP runner; `specs.metric_keys` is unchanged |

## Purpose

Decide whether the session's consumption is within the estate's normal operating bounds and within its
entitlement, keeping the two apart. High consumption is a reading against the estate (EXCESSIVE, INEFFICIENT);
unauthorized consumption is a reading against the quota record (UNAUTHORIZED). Only the quota decides the
second.

| Layer | What it is | Where it is kept |
|---|---|---|
| Observed consumption | metric values and states, session totals | each question's `basis.measures`, `basis.reported_not_judged` |
| Above expected bounds | a judged measure in EXCEPTION or CRITICAL (hot) or WARNING (elevated) under metrics_config.json | RS-1 to RS-3 answers, RS-5 `basis.hot`, `basis.elevated` |
| Above the entitlement | the session's tokens above the quota's `token_budget` | RS-1 `basis.permitted` |
| Attribution | which lanes and MCP servers the consumption sits in, through recorded links only | RS-4 |
| Insufficient evidence | a measure missing, a baseline too small, a blocking check failed, telemetry INSUFFICIENT | INCONCLUSIVE, BASELINE_INSUFFICIENT or UNDETERMINED with reasons |

## The entitlement source

Only the quota record: case artifact `quotas`, read by `tools.get_quota` (the agent scope's entry, or `*`),
field `token_budget`. It carries no period, so the session's tokens are compared with it, as `rules.py` did
before this package (limitation `quota_period`). Memory, upstream findings, metric states and the estate
baseline are never an entitlement. With no quota, permission is NOT_EVALUATED and `requirements.py` withholds
NORMAL (status MISSING_DEPENDENCY, `gated_label` NORMAL), unchanged.

## Questions

| Id | Question (workbook) | Judged | Reported, not judged | NOT_CALCULATED | Outcomes |
|---|---|---|---|---|---|
| RS-1 | Is the agent's token consumption within expected and permitted limits? | insights.token_outlier (REQUIRED); quota token_budget | token_consumption_per_session, token_consumption_per_turn, cost_per_session, resource_consumption_rate | token_budget_utilization, token_quota_exceeded, cost_budget_utilization | WITHIN_LIMITS, WITHIN_EXPECTED, ELEVATED, ABOVE_EXPECTED, ABOVE_PERMITTED, BASELINE_INSUFFICIENT |
| RS-2 | Are its execution duration and action rate within acceptable operational thresholds? | insights.duration_outlier, activity.action_rate_ratio (REQUIRED) | execution_duration_per_session, execution_duration_threshold_breach (no approved threshold), tool_timeout_rate | none | WITHIN_THRESHOLDS, ELEVATED, OUTSIDE_THRESHOLDS, BASELINE_INSUFFICIENT |
| RS-3 | Are stalls, repeated operations or excessive resource consumption creating capacity problems? | insights.stalled (REQUIRED) | insights.repetition, insights.retry, insights.failed (observed, not counted: U-RS2), resource_stall_rate, resource_redundant_execution_cost, resource_concurrency_level | resource_capacity_utilization | NO_WASTE_COUNTED, ELEVATED, WASTE_OBSERVED |
| RS-4 | Are model selection, subagent activity and resource usage consistent with the agent's allocated permissions and quotas? | attribution through Interaction Discovery; interaction.subagent_lanes, interaction.mcp_servers (REQUIRED, breadth recorded not counted) | subagent_resource_consumption, models observed | model_selection_compliance, resource_quota_compliance | SINGLE_AGENT, ATTRIBUTED, UNATTRIBUTED |
| RS-5 | Is resource consumption approaching or exceeding a limit that requires intervention? | RS-1 to RS-4, Runtime Monitoring | telemetry.collector_coverage, telemetry.unknown_share | resource_limit_proximity, resource_threshold_breach_count | NO_INTERVENTION, ATTENTION, INTERVENTION_REQUIRED, UNDETERMINED |

`resource_intervention_required` (registry category O) is RS-5's answer itself, not an input.

Blocking checks: CONFLICTING_SOURCES for RS-1; CONFLICTING_ACTION_SOURCES for RS-2, RS-3 and RS-4, as Anomaly
Detection's token and action questions. A question whose blocking check fails is INCONCLUSIVE with its measures
kept in `observed_not_judged`.

## Measures counted toward the label

Only the four `specs.metric_keys` the legacy rule judged: BURN (insights.token_outlier, activity.action_rate_ratio)
and WASTE (insights.duration_outlier, insights.stalled), at the states it used (EXCEPTION, CRITICAL hot; WARNING
elevated). The other 27 assigned metrics are observed, reported or NOT_CALCULATED; none is a new trigger.

## Baselines

Self-inclusive, as `signal_metrics` computes the three estate-relative measures (the session is part of its own
estate). Each RS-1 and RS-2 measure records its comparison (`baseline`: method `self_inclusive`, sessions,
median, version) and, beside it, the leave-one-out reading `cop_package.ad_leave_one_out` gives
(`basis.leave_one_out`, `used: self_inclusive`). The leave-one-out reading decides nothing (U-RS1).

## Authority

It proposes ALERT or WATCH. Triage and the authority check decide what is recorded or executed (`triage.py`
bands for this COP unchanged). No budget, quota, permission or allocation is changed by this COP.
