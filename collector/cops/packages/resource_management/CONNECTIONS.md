# Resource Management: connections

## Upstream

Read only when verified and substantive (`rules.verified`).

| Producer | Reads | Effect |
|---|---|---|
| Runtime Monitoring | label | its evidence level scales every confidence (`rules.evidence_conf`); INSUFFICIENT makes a quiet RS-5 UNDETERMINED (INDETERMINATE) and is contrary evidence beside a positive reading; recorded in `upstream_used` as evidence quality, not corroboration |
| Interaction Discovery | ID-2 answer, ID-2 `basis.links` (RECORDED, INFERRED, INFERRED_AGAINST_TIME_ORDER, UNLINKED), `edges`, label | RS-4 attribution. Only RECORDED delegation links (parent_agent_id on the lane's rows) attribute; a link inferred from timing, an unlinked lane, ID-2 not FULLY_RECONSTRUCTED, PATH_PARTIAL or no verified finding leave consumption UNATTRIBUTED and discount the confidence. A verified interaction finding is a claim and an `upstream_used` entry only when it attributed |
| Workload Optimization (earlier cycle) | its verified finding | `cop_specific.advisory`, `applied: False`; changes nothing (unchanged) |

## Downstream

| Consumer | Reads | Effect today |
|---|---|---|
| Risk Mitigation | the verified finding and its lineage (`tools.t_calculate_risk_components`, `independent_upstream`) | counted as correlated, not as an independent signal, whenever the finding cites insights.token_outlier: Risk Mitigation's own metric_keys include it. Its rule reading did not change in the downstream scenario (LOW with and without this COP) |
| Incident Response | the verified finding (`rules.incident_response`) | listed in `upstream_used` as input and, when not NORMAL, an upstream claim; its rule reading does not depend on it (NO_INCIDENT with and without this COP in the downstream scenario) |
| Workload Optimization | listed in `specs.SPEC_CONSUMES` | `rules.workload_optimization` does not read this COP's finding (U-RS6) |
| Notified | `cop_specific.recipients` | Risk Mitigation when a burn measure is hot, Workload Optimization when a waste measure is hot |

## Tested

`tests/test_cop_package_resource_management.py` and the harness (scenarios attribution_*, incomplete_telemetry,
spike_on_incomplete_telemetry, downstream_consumers, quota_change_changes_decision); `tests/test_cop_influence.py`
(Interaction Discovery to Resource Management, Workload Optimization advisory);
`tests/test_cop_package_interaction_discovery.py::test_resource_management_reads_id2_not_only_the_label`;
`tests/test_runtime_graph.py::test_resource_management_alternate_outcomes`; evaluation and bench cases for this COP.
