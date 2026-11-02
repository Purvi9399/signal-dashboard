# Live evaluation of the ten LLM COPs (SIMULATED, offline)

Generated 2026-11-02T22:18:25Z.

Every model reply here is scripted (`FixtureProvider`, generic careful script) and every record is `simulated=True`. This run checks the cases and the harness, not any model.

| Case | Expected | Label | Status | Verified | Outcome | Calls | Tokens in/out | Latency ms | Cost |
|---|---|---|---|---|---|---|---|---|---|
| `workflow_orchestration:normal` | NORMAL | NORMAL | SUBSTANTIVE | True | agree | 2 | 2600/444 | 9 | unpriced |
| `workflow_orchestration:abnormal` | STALLED | INCONCLUSIVE | INCONCLUSIVE | False | inconclusive | 2 | 2665/152 | 3 | unpriced |
| `workflow_orchestration:missing` | INCONCLUSIVE | INCONCLUSIVE | REJECTED_BY_VERIFIER | False | agree | 2 | 2606/363 | 6 | unpriced |
| `anomaly_detection:normal` | NORMAL | NORMAL | SUBSTANTIVE | True | agree | 2 | 3783/524 | 6 | unpriced |
| `anomaly_detection:abnormal` | ANOMALOUS | INCONCLUSIVE | INCONCLUSIVE | False | inconclusive | 2 | 3874/159 | 6 | unpriced |
| `anomaly_detection:missing` | INDETERMINATE, INCONCLUSIVE | INCONCLUSIVE | REJECTED_BY_VERIFIER | False | agree | 2 | 3632/523 | 4 | unpriced |
| `context_memory:normal` | CONTEXT_OK | INCONCLUSIVE | INCONCLUSIVE | False | inconclusive | 2 | 2097/133 | 3 | unpriced |
| `context_memory:abnormal` | CONTEXT_LOSS | INCONCLUSIVE | INCONCLUSIVE | False | inconclusive | 2 | 2132/133 | 3 | unpriced |
| `context_memory:missing` | INCONCLUSIVE | INCONCLUSIVE | INCONCLUSIVE | False | agree | 2 | 2116/148 | 3 | unpriced |
| `incident_response:normal` | NO_INCIDENT | NO_INCIDENT | SUBSTANTIVE | True | agree | 2 | 2744/416 | 3 | unpriced |
| `incident_response:abnormal` | INCIDENT, WATCH | INCONCLUSIVE | INCONCLUSIVE | False | inconclusive | 2 | 3067/156 | 3 | unpriced |
| `incident_response:missing` | INCONCLUSIVE, WATCH | INCONCLUSIVE | REJECTED_BY_VERIFIER | False | agree | 2 | 2746/372 | 4 | unpriced |
| `classification_tagging:normal` | UNCLASSIFIED | UNCLASSIFIED | SUBSTANTIVE | True | agree | 2 | 2333/465 | 3 | unpriced |
| `classification_tagging:abnormal` | INTEGRITY | INCONCLUSIVE | INCONCLUSIVE | False | inconclusive | 2 | 2375/157 | 3 | unpriced |
| `classification_tagging:missing` | INCONCLUSIVE | INCONCLUSIVE | INCONCLUSIVE | False | agree | 2 | 2331/157 | 3 | unpriced |
| `risk_mitigation:normal` | LOW | LOW | SUBSTANTIVE | True | agree | 2 | 2703/512 | 3 | unpriced |
| `risk_mitigation:abnormal` | EMERGING, MATERIAL | INCONCLUSIVE | INCONCLUSIVE | False | inconclusive | 2 | 2747/157 | 3 | unpriced |
| `risk_mitigation:missing` | INCONCLUSIVE | INCONCLUSIVE | REJECTED_BY_VERIFIER | False | agree | 2 | 2682/458 | 3 | unpriced |
| `governance_intelligence:normal` | NO_PATTERN | NO_PATTERN | SUBSTANTIVE | True | agree | 2 | 2125/280 | 3 | unpriced |
| `governance_intelligence:abnormal` | RECURRING, CONCENTRATED | INCONCLUSIVE | REJECTED_BY_VERIFIER | False | inconclusive | 2 | 2126/280 | 3 | unpriced |
| `governance_intelligence:missing` | INCONCLUSIVE | INCONCLUSIVE | INCONCLUSIVE | False | agree | 2 | 1985/148 | 3 | unpriced |
| `workload_optimization:normal` | EFFICIENT | EFFICIENT | SUBSTANTIVE | True | agree | 2 | 2299/476 | 3 | unpriced |
| `workload_optimization:abnormal` | REDUNDANT | EFFICIENT | SUBSTANTIVE | True | missed_finding | 2 | 2311/425 | 3 | unpriced |
| `workload_optimization:missing` | INCONCLUSIVE | INCONCLUSIVE | REJECTED_BY_VERIFIER | False | agree | 2 | 2317/481 | 3 | unpriced |
| `knowledge_policy_management:normal` | CURRENT | CURRENT | SUBSTANTIVE | True | agree | 2 | 2007/287 | 3 | unpriced |
| `knowledge_policy_management:abnormal` | CONFLICT | INCONCLUSIVE | REJECTED_BY_VERIFIER | False | inconclusive | 2 | 2046/287 | 4 | unpriced |
| `knowledge_policy_management:missing` | INCONCLUSIVE | INCONCLUSIVE | INCONCLUSIVE | False | agree | 2 | 2015/150 | 3 | unpriced |
| `governance_architecture:normal` | ADEQUATE | ADEQUATE | SUBSTANTIVE | True | agree | 2 | 3921/340 | 4 | unpriced |
| `governance_architecture:abnormal` | GAP | INCONCLUSIVE | REJECTED_BY_VERIFIER | False | inconclusive | 2 | 3933/340 | 4 | unpriced |
| `governance_architecture:missing` | INCONCLUSIVE | INCONCLUSIVE | INCONCLUSIVE | False | agree | 2 | 3929/151 | 3 | unpriced |

Totals: agree 19, inconclusive 10, false_positive 0, missed_finding 1, unsupported_quiet_label 0, disagree 0, provider_failure 0, error 0, cases 30, ran 30, skipped 0, llm_calls 60, llm_errors 0, input_tokens 80247, output_tokens 9074, cost_usd 0, unpriced_calls 60, latency_ms 115.0, simulated_calls 60

Invocation records verified: yes, no problems
