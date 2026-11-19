# Runtime Monitoring: connections

## Upstream

None. Runtime Monitoring consumes no COP (`specs.CONSUMES["runtime_monitoring"] == ()`) and has no previous-cycle feedback edge (`specs.FEEDBACK`). It runs first (`specs.ORDER`).

## Downstream: who consumes its finding

These come from `specs.SPEC_CONSUMES`, which is the current-cycle edge. There are eleven consumers:
- interaction_discovery
- agent_lifecycle_management
- context_memory
- workflow_orchestration
- behavior_analytics
- anomaly_detection
- resource_management
- workload_optimization
- policy_enforcement
- incident_response
- audit_traceability

The legacy hand-off list (`DECLARED_DOWNSTREAM`) names only audit_traceability and incident_response. The Block 1 topology edges add nothing for this COP.

## How its finding is used downstream

| Mechanism | Code | Effect |
|---|---|---|
| Quiet-label bound | `graph._gate_required`, `_telemetry_insufficient` | A verified INSUFFICIENT reading turns a consumer's quiet label into INCONCLUSIVE, with reason MISSING_EVIDENCE, before verification |
| DS-01 verifier | `verifiers.py` (DS-01) | Rejects a quiet label under verified INSUFFICIENT telemetry, and a quiet label resting on unobservable metrics under DEGRADED telemetry |
| Evidence profile | `requirements.upstream_role` (BOUNDS_QUIET) | Runtime Monitoring is the only upstream that bounds a label. Every other upstream finding is context |
| Triage cause | `triage.py` (`telemetry_insufficient`) | A consumer's inconclusive result under INSUFFICIENT telemetry is attributed to telemetry, and its referral routes to Runtime Monitoring |
| Activation | `activation.policies_v1` (`rule:case_has_telemetry@1`) | Runtime Monitoring activates for every case with telemetry |
| Consultations | `activation` contracts anomaly_detection → runtime_monitoring and behavior_analytics → runtime_monitoring (`upstream_evidence`) | Consumers may consult it within the referral limits |
| Specialist | `triage.SPECIALISTS["runtime_monitoring"]` (`telemetry_integrity`) | A recorded dry-run referral. No specialist runtime exists |

Only a **verified** Runtime Monitoring finding bounds anything. An unverified one is ignored by `_telemetry_insufficient` and DS-01.

## Tested

In `tests/test_cop_package_runtime_monitoring.py` and the harness scenario downstream_consumption, thin telemetry gives a verified INSUFFICIENT reading. Behavior Analytics then withholds STABLE. With full telemetry, the same COP reports STABLE.

The five question answers are not consumed by anyone yet (`DECISIONS.md` D1).
