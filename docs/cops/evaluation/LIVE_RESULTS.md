# Live evaluation of the ten LLM COPs (LIVE provider calls)

Generated 2026-11-02T22:15:44Z, model `claude-sonnet-5-5`.

| Case | Expected | Label | Status | Verified | Outcome | Calls | Tokens in/out | Latency ms | Cost |
|---|---|---|---|---|---|---|---|---|---|
| `workflow_orchestration:normal` | NORMAL | NORMAL | SUBSTANTIVE | True | agree | 2 | 7359/2416 | 17823 | $0.0389 |
| `workflow_orchestration:abnormal` | STALLED | INCONCLUSIVE | REJECTED_BY_VERIFIER | False | inconclusive | 2 | 9048/3134 | 25661 | $0.0494 |
| `workflow_orchestration:missing` | INCONCLUSIVE | INCONCLUSIVE | INCONCLUSIVE | False | agree | 2 | 7360/1298 | 11989 | $0.0277 |
| `anomaly_detection:normal` | NORMAL | NORMAL | SUBSTANTIVE | True | agree | 2 | 8433/2975 | 20882 | $0.0466 |
| `anomaly_detection:abnormal` | ANOMALOUS | ANOMALOUS | SUBSTANTIVE | True | agree | 2 | 8621/3650 | 25896 | $0.0537 |
| `anomaly_detection:missing` | INDETERMINATE, INCONCLUSIVE | INCONCLUSIVE | INCONCLUSIVE | False | agree | 2 | 8700/3421 | 23321 | $0.0516 |
| `context_memory:normal` | CONTEXT_OK | CONTEXT_OK | SUBSTANTIVE | True | agree | 2 | 6471/1908 | 16045 | $0.0320 |
| `context_memory:abnormal` | CONTEXT_LOSS | CONTEXT_LOSS | SUBSTANTIVE | True | agree | 2 | 6641/2235 | 16184 | $0.0356 |
| `context_memory:missing` | INCONCLUSIVE | INCONCLUSIVE | INCONCLUSIVE | False | agree | 2 | 6472/1017 | 9607 | $0.0231 |
| `incident_response:normal` | NO_INCIDENT | NO_INCIDENT | SUBSTANTIVE | True | agree | 2 | 8073/2444 | 18238 | $0.0406 |
| `incident_response:abnormal` | INCIDENT, WATCH | INCIDENT | SUBSTANTIVE | True | agree | 2 | 8855/3837 | 25621 | $0.0561 |
| `incident_response:missing` | INCONCLUSIVE, WATCH | INCONCLUSIVE | REJECTED_BY_VERIFIER | False | agree | 2 | 8353/3371 | 23860 | $0.0504 |
| `classification_tagging:normal` | UNCLASSIFIED | INCONCLUSIVE | INCONCLUSIVE | False | inconclusive | 2 | 6938/1761 | 17169 | $0.0315 |
| `classification_tagging:abnormal` | INTEGRITY | INTEGRITY | SUBSTANTIVE | True | agree | 2 | 7241/3494 | 24060 | $0.0494 |
| `classification_tagging:missing` | INCONCLUSIVE | INCONCLUSIVE | INCONCLUSIVE | False | agree | 2 | 7269/1726 | 14819 | $0.0318 |
| `risk_mitigation:normal` | LOW | INCONCLUSIVE | REJECTED_BY_VERIFIER | False | inconclusive | 2 | 7927/3475 | 23640 | $0.0506 |
| `risk_mitigation:abnormal` | EMERGING, MATERIAL | EMERGING | SUBSTANTIVE | True | agree | 2 | 8092/3843 | 27934 | $0.0546 |
| `risk_mitigation:missing` | INCONCLUSIVE | INCONCLUSIVE | INCONCLUSIVE | False | agree | 2 | 7925/1407 | 11965 | $0.0299 |
| `governance_intelligence:normal` | NO_PATTERN | INCONCLUSIVE | REJECTED_BY_VERIFIER | False | inconclusive | 2 | 6723/3077 | 24212 | $0.0442 |
| `governance_intelligence:abnormal` | RECURRING, CONCENTRATED | INCONCLUSIVE | REJECTED_BY_VERIFIER | False | inconclusive | 2 | 6739/3134 | 23963 | $0.0448 |
| `governance_intelligence:missing` | INCONCLUSIVE | INCONCLUSIVE | INCONCLUSIVE | False | agree | 2 | 6368/1147 | 9625 | $0.0242 |
| `workload_optimization:normal` | EFFICIENT | EFFICIENT | SUBSTANTIVE | True | agree | 2 | 7108/2571 | 18631 | $0.0399 |
| `workload_optimization:abnormal` | REDUNDANT | INCONCLUSIVE | REJECTED_BY_VERIFIER | False | inconclusive | 2 | 7152/3157 | 23561 | $0.0459 |
| `workload_optimization:missing` | INCONCLUSIVE | INCONCLUSIVE | REJECTED_BY_VERIFIER | False | agree | 2 | 7084/2884 | 20141 | $0.0430 |
| `knowledge_policy_management:normal` | CURRENT | CURRENT | SUBSTANTIVE | True | agree | 2 | 6279/1914 | 15163 | $0.0317 |
| `knowledge_policy_management:abnormal` | CONFLICT | INCONCLUSIVE | REJECTED_BY_VERIFIER | False | inconclusive | 2 | 6269/2465 | 18462 | $0.0372 |
| `knowledge_policy_management:missing` | INCONCLUSIVE | INCONCLUSIVE | INCONCLUSIVE | False | agree | 2 | 6131/925 | 7879 | $0.0215 |
| `governance_architecture:normal` | ADEQUATE | GAP | SUBSTANTIVE | True | false_positive | 2 | 10913/3596 | 24391 | $0.0578 |
| `governance_architecture:abnormal` | GAP | INCONCLUSIVE | REJECTED_BY_VERIFIER | False | inconclusive | 2 | 10896/4086 | 28130 | $0.0627 |
| `governance_architecture:missing` | INCONCLUSIVE | INCONCLUSIVE | REJECTED_BY_VERIFIER | False | agree | 2 | 10983/3755 | 25484 | $0.0595 |

Totals: agree 21, inconclusive 8, false_positive 1, missed_finding 0, unsupported_quiet_label 0, disagree 0, provider_failure 0, error 0, cases 30, ran 30, skipped 0, llm_calls 60, llm_errors 0, input_tokens 232423, output_tokens 80123, cost_usd 1.266076, unpriced_calls 0, latency_ms 594354.4, simulated_calls 0

Invocation records verified: yes, no problems
