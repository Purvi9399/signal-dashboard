# Risk Mitigation: memory

Policy (`memory.MEMORY_POLICY["risk_mitigation"]`, unchanged): writes `mitigation_outcome`; reads
`mitigation_outcome` and `incident_outcome`; scope tenant.

| What | When | Content |
|---|---|---|
| Write | the shared step (`graph.validate_memory_update`): a verified substantive finding that is not LOW | `cop_specific.memory_payload`: the four answers, band, score, signals counted, register version, recommended controls, precedents read, decided_by package |
| Read | `tools.t_assess_risk_questions` through `_memory_lookup("mitigation_outcome")` | validated (`established`) records only, in RK-4 `basis.precedents`, "comparison only, never proof" |

A precedent never changes a band, a signal count, an action or a confidence (scenario memory_precedent: the same
reading with and without the validated record). `incident_outcome` is in the policy but is not read by this
package: no approved use of earlier incident outcomes in the register formula exists (U-RK6).

A written record is the COP's assessment, not an outcome of a mitigation; a reviewer's feedback on the record is
what the shared feedback ledger keeps. risk_mitigation_effectiveness and risk_mitigation_completion stay
NOT_CALCULATED until mitigation actions and their outcomes are recorded.
