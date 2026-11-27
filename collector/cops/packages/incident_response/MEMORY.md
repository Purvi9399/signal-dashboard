# Incident Response: memory

The policy (`memory.MEMORY_POLICY["incident_response"]`) is unchanged. It writes `incident_outcome`, reads
`incident_outcome`, `anomaly_observation`, `enforcement_record` and `recovery_outcome`, and has scope tenant.

| What | When | Content |
|---|---|---|
| Write | the shared step (`graph.validate_memory_update`), for a verified substantive WATCH or INCIDENT | `cop_specific.memory_payload`: the five answers, the category, the grounds with their sources and independence, the response, the precedents read, and decided_by (package, or package and model). For a model-planned incident it also holds `model_check` (accepted, model label and action, plan, scope) |
| Read | `tools.t_assess_incident_questions` through `_memory_lookup("incident_outcome")` | established (validated) and verified-pending records. Only established records are listed as precedents in IR-5 `basis.precedents`, "comparison only, never proof" |
| Recurrence | IR-5 `basis.recurrence` | earlier validated WATCH or INCIDENT records on this tenant. Listed with RECURRENCE_NOT_WEIGHED, no effect (U-IR4) |

A precedent never changes an answer, label, severity, action or confidence. Scenario memory_precedent gives the
same reading with and without the validated record. anomaly_observation, enforcement_record and recovery_outcome
are not read, because the current findings of their producers are consumed directly (D-IR13).

## Previous cycle

Risk Mitigation's verified finding from the previous cycle (`specs.FEEDBACK`) is delivered as feedback. The rule
records it as `cop_specific.earlier_risk_assessment`, and the package records it as `cop_specific.previous_cycle`
with "context only, never counted". It grounds nothing. Scenario previous_cycle_feedback_not_circular gives the
same answers with and without it (D-IR15).

A written record is the COP's assessment. It is not the outcome of an intervention. incident_intervention_success
and incident_residual_risk stay NOT_CALCULATED until intervention outcomes are recorded.
