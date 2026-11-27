# Recovery & Resilience: specification

COP 10 in the build order: Runtime Monitoring, Interaction Discovery, Audit & Traceability, Policy Enforcement,
Anomaly Detection, Resource Management, Classification & Tagging, Risk Mitigation, Incident Response, then Recovery &
Resilience. The workbook and `docs/cops/cop_catalogue.json` number this COP 13. The build order is recorded apart
from that numbering.

`METRICS.yaml` is the contract the runtime reads. This file explains it.

## Behavioural question

- `cops/specs.py` (unchanged): "After failure or intervention, did the system return to a viable state without
  compounding the problem?"
- Workbook COMPLETE_DELEGATION_DATA row 195: "Has the system recovered from disruption, and can it continue
  operating reliably?". Rows 195, 199, 201 and 203 give the four questions below. Row 197 has a field list and
  external data but no question text, and is read as RR-1's inputs (D-RR1, U-RR1). Row 205 lists the 31 metrics.

## Questions

| Id | Question | Answers |
|---|---|---|
| RR-1 | Has a failure, incident or governance intervention disrupted the agent's workflow? | DISRUPTED, NOT_DISRUPTED |
| RR-2 | Has the affected system, resource or workflow been restored to an acceptable state? | RESTORED_VERIFIED, PARTIALLY_RESTORED, NOT_RESTORED, IRREVERSIBLE_CHANGE, NOTHING_TO_RESTORE |
| RR-3 | Are failures, retries or incidents recurring after recovery? | RECURRING, NOT_RECURRING, NO_RECOVERY_TO_FOLLOW |
| RR-4 | Is there sufficient post-recovery evidence to confirm stability, or is additional monitoring or intervention required? | STABILITY_CONFIRMED, MONITORING_REQUIRED, INTERVENTION_REQUIRED, NOTHING_TO_CONFIRM |

Every question can also be INCONCLUSIVE. RR-2 depends on RR-1, RR-3 on RR-2, and RR-4 on all three
(`METRICS.yaml depends_on`).

## Metrics (31, registry consumers = recovery_resilience)

| Question | Built, judged against approved thresholds (category E) | Built, reported only (catalogue, NO_THRESHOLD) | NOT_CALCULATED |
|---|---|---|---|
| RR-1 | insights.failed (REQUIRED), insights.stalled, insights.blocked | recovery_disruption_count | none |
| RR-2 | insights.reverted (REQUIRED when applicable) | recovery_time, recovery_workflow_resumption_rate | recovery_action_success_rate, recovery_action_completion, recovery_state_restoration, recovery_verification_success_rate |
| RR-3 | insights.retry (REQUIRED when applicable), insights.repetition, insights.duration_outlier, insights.token_outlier, activity.action_rate_ratio | recovery_failure_rate, recovery_retry_recurrence, recovery_stability_duration | recovery_post_intervention_failure_rate, recovery_incident_recurrence_rate |
| RR-4 | telemetry.collector_coverage, telemetry.source_agreement, telemetry.turn_linkage, telemetry.lane_linkage, telemetry.unknown_share, insights.outside | none | recovery_evidence_sufficiency, recovery_post_recovery_health_score, recovery_residual_failure_risk, recovery_additional_intervention_required |

That makes:
- 15 metrics judged against approved thresholds;
- 6 catalogue metrics reported with their value and never judged (no approved threshold);
- 10 metrics NOT_CALCULATED, each with the registry's missing input.

The REQUIRED metrics are `specs.metric_keys` exactly. The spec's missing_metrics are handled as follows:
- recovery_time is now computed by the catalogue;
- recovery_success_after_failure and irreversible_change are NOT_CALCULATED as metrics. RR-2 reads the
  post-restore checks and the irreversible-command pattern instead.

## Inputs beyond metrics

- **Recovery records** (case artifact `recovery_records`) are read through `tools.get_recovery_records` and
  `tools.validate_checkpoints`, which recomputes each checkpoint hash.
  - A legacy record has `checkpoint_id`, `checkpoint_content`, `checkpoint_hash`, `restored_at` and
    `post_restore_checks`. It keeps its meaning.
  - Optional fields `action`, `recommended_by`, `approval`, `execution`, `expected_outcome` and `disruption_refs`
    are read when present (METRICS.yaml `record_fields`; D-RR4, U-RR2).
- **The session's own rows**: failures, stalls, deny-rule stops, reverted work, irreversible commands
  (`tools.IRREVERSIBLE`), event timestamps and the collectors' agreement.
- **Behavior Analytics' finding** (the only consumed COP), as context.
- **Validated recovery_outcome memory**, as comparison only.

## Labels, severity, confidence, action

| RR-2 (and RR-3) | Label | Severity | Confidence | Action (RR-4) |
|---|---|---|---|---|
| IRREVERSIBLE_CHANGE | IRREVERSIBLE | HIGH | 0.85 | ALERT |
| NOT_RESTORED | FAILED_RECOVERY | HIGH | 0.85 | ALERT |
| PARTIALLY_RESTORED, or RESTORED_VERIFIED with RR-3 RECURRING | PARTIAL_RECOVERY | MEDIUM | 0.8 | WATCH |
| RESTORED_VERIFIED, NOT_RECURRING | RECOVERED | NONE | 0.85 | NONE when stability is confirmed, else WATCH |
| NOTHING_TO_RESTORE, or RR-2 unanswered | INCONCLUSIVE | NONE | 0.0 | NONE without a disruption, else WATCH |

No model is used (`profiles.DETERMINISTIC_COPS`). Actions never exceed ALERT (`specs.permitted_actions`).
