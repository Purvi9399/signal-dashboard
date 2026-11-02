# Live evaluation of the ten LLM COPs (SIMULATED, offline)

Generated 2026-11-02T06:24:03Z.

Every model reply here is scripted (`FixtureProvider`, generic careful script) and every record is `simulated=True`. This run checks the cases and the harness, not any model.

| Case | Expected | Label | Status | Verified | Outcome | Calls | Tokens in/out | Latency ms | Cost |
|---|---|---|---|---|---|---|---|---|---|
| `workflow_orchestration:normal` | NORMAL | NORMAL | SUBSTANTIVE | True | agree | 2 | 2495/444 | 8 | unpriced |
| `workflow_orchestration:abnormal` | STALLED | INCONCLUSIVE | INCONCLUSIVE | False | inconclusive | 2 | 2561/152 | 3 | unpriced |
| `workflow_orchestration:missing` | INCONCLUSIVE | INCONCLUSIVE | REJECTED_BY_VERIFIER | False | agree | 2 | 2502/363 | 4 | unpriced |
| `anomaly_detection:normal` | NORMAL | NORMAL | SUBSTANTIVE | True | agree | 2 | 3679/524 | 3 | unpriced |
| `anomaly_detection:abnormal` | ANOMALOUS | INCONCLUSIVE | INCONCLUSIVE | False | inconclusive | 2 | 3768/159 | 4 | unpriced |
| `anomaly_detection:missing` | INDETERMINATE, INCONCLUSIVE | INCONCLUSIVE | REJECTED_BY_VERIFIER | False | agree | 2 | 3527/523 | 6 | unpriced |
| `context_memory:normal` | CONTEXT_OK | INCONCLUSIVE | INCONCLUSIVE | False | inconclusive | 2 | 1992/133 | 3 | unpriced |
| `context_memory:abnormal` | CONTEXT_LOSS | INCONCLUSIVE | INCONCLUSIVE | False | inconclusive | 2 | 2026/133 | 3 | unpriced |
| `context_memory:missing` | INCONCLUSIVE | INCONCLUSIVE | INCONCLUSIVE | False | agree | 2 | 2011/148 | 3 | unpriced |
| `incident_response:normal` | NO_INCIDENT | NO_INCIDENT | SUBSTANTIVE | True | agree | 2 | 2640/416 | 3 | unpriced |
| `incident_response:abnormal` | INCIDENT, WATCH | INCONCLUSIVE | INCONCLUSIVE | False | inconclusive | 2 | 2962/156 | 3 | unpriced |
| `incident_response:missing` | INCONCLUSIVE, WATCH | INCONCLUSIVE | REJECTED_BY_VERIFIER | False | agree | 2 | 2642/372 | 5 | unpriced |
| `classification_tagging:normal` | UNCLASSIFIED | UNCLASSIFIED | SUBSTANTIVE | True | agree | 2 | 2229/465 | 4 | unpriced |
| `classification_tagging:abnormal` | INTEGRITY | INCONCLUSIVE | INCONCLUSIVE | False | inconclusive | 2 | 2271/157 | 6 | unpriced |
| `classification_tagging:missing` | INCONCLUSIVE | INCONCLUSIVE | INCONCLUSIVE | False | agree | 2 | 2227/157 | 3 | unpriced |
| `risk_mitigation:normal` | LOW | LOW | SUBSTANTIVE | True | agree | 2 | 2599/512 | 3 | unpriced |
| `risk_mitigation:abnormal` | EMERGING, MATERIAL | INCONCLUSIVE | INCONCLUSIVE | False | inconclusive | 2 | 2643/157 | 3 | unpriced |
| `risk_mitigation:missing` | INCONCLUSIVE | INCONCLUSIVE | REJECTED_BY_VERIFIER | False | agree | 2 | 2577/458 | 5 | unpriced |
| `governance_intelligence:normal` | NO_PATTERN | NO_PATTERN | SUBSTANTIVE | True | agree | 2 | 2020/280 | 4 | unpriced |
| `governance_intelligence:abnormal` | RECURRING, CONCENTRATED | INCONCLUSIVE | REJECTED_BY_VERIFIER | False | inconclusive | 2 | 2021/280 | 3 | unpriced |
| `governance_intelligence:missing` | INCONCLUSIVE | INCONCLUSIVE | INCONCLUSIVE | False | agree | 2 | 1880/148 | 3 | unpriced |
| `workload_optimization:normal` | EFFICIENT | EFFICIENT | SUBSTANTIVE | True | agree | 2 | 2194/476 | 4 | unpriced |
| `workload_optimization:abnormal` | REDUNDANT | EFFICIENT | SUBSTANTIVE | True | missed_finding | 2 | 2207/425 | 3 | unpriced |
| `workload_optimization:missing` | INCONCLUSIVE | INCONCLUSIVE | REJECTED_BY_VERIFIER | False | agree | 2 | 2213/481 | 3 | unpriced |
| `knowledge_policy_management:normal` | CURRENT | CURRENT | SUBSTANTIVE | True | agree | 2 | 1903/287 | 3 | unpriced |
| `knowledge_policy_management:abnormal` | CONFLICT | INCONCLUSIVE | REJECTED_BY_VERIFIER | False | inconclusive | 2 | 1942/287 | 3 | unpriced |
| `knowledge_policy_management:missing` | INCONCLUSIVE | INCONCLUSIVE | INCONCLUSIVE | False | agree | 2 | 1911/150 | 3 | unpriced |
| `governance_architecture:normal` | ADEQUATE | ADEQUATE | SUBSTANTIVE | True | agree | 2 | 3815/340 | 3 | unpriced |
| `governance_architecture:abnormal` | GAP | INCONCLUSIVE | REJECTED_BY_VERIFIER | False | inconclusive | 2 | 3828/340 | 4 | unpriced |
| `governance_architecture:missing` | INCONCLUSIVE | INCONCLUSIVE | INCONCLUSIVE | False | agree | 2 | 3825/151 | 3 | unpriced |

Totals: agree 19, inconclusive 10, false_positive 0, missed_finding 1, unsupported_quiet_label 0, disagree 0, error 0, cases 30, ran 30, skipped 0, llm_calls 60, llm_errors 0, input_tokens 77110, output_tokens 9074, cost_usd 0, unpriced_calls 60, latency_ms 113.3, simulated_calls 60

Invocation records verified: yes, no problems
