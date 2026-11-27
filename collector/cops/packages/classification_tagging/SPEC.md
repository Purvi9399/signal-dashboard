# Classification & Tagging: specification

The runtime reads `METRICS.yaml`; this file explains it. Where they differ, `METRICS.yaml` wins.

## Behavioural question

`cops/specs.py`, unchanged: "What kind of governance event is this, and which entities should remain associated
with it?" The workbook (COPS- implementation 1.xlsx, COMPLETE_DELEGATION_DATA row 107) words it "What kind of
governance event has occurred, and how should it be categorized?". The second half of the specs wording is
answered by CT-5's entity association. Kind only: not severity, not response. The only permitted action is NONE.

## Questions

| Id | Workbook row | Question | Counted (indicates a family) | Observed, not counted |
|---|---|---|---|---|
| CT-1 | 107 | Is the event related to boundary crossing, authorization or access restrictions? | insights.outside, insights.sensitive_unasked (BOUNDARY) | access.first_seen_mcp |
| CT-2 | 109 | Does the event involve inadequate oversight, missing approval or inappropriate human intervention? | insights.blocked (OVERSIGHT) | insights.no_oversight, oversight.fast_approvals, oversight.declined_inferred |
| CT-3 | 111 | Does the event involve sensitive information, credentials or security-related exposure? | insights.secrets (INTEGRITY) | none |
| CT-4 | 113 | Does the event involve behavioural, execution, integrity or resource-related concerns? | none | insights.failed, repetition, retry, stalled, reverted, lockfile, token_outlier, duration_outlier, path.transition_novelty, activity.action_rate_ratio, interaction.subagent_lanes, interaction.mcp_servers; verified Anomaly Detection and Resource Management findings |
| CT-5 | 115 | Can the event be classified with sufficient confidence and linked to the correct actors, resources and evidence? | CT-1 to CT-4, the taxonomy | insights.unattributed, telemetry.collector_coverage, source_agreement, turn_linkage, lane_linkage, unknown_share |

The four counted metrics are `cops/specs.py` metric_keys, and the families are the legacy rule's
(`cops/interpret.py` classification_tagging). The thresholds are metrics_config.json's; a family is indicated
at EXCEPTION or CRITICAL. WARNING does not indicate a family.

## Metric coverage

The workbook's metric list (row 117) and the registry agree on 26 metrics, all category E, IMPLEMENTED_EXISTING.
Every one is built and read. Four count toward a family; 22 are observed and reported. The spec's four
`missing_metrics` (actor_type, resource_type, incident_family, classification_confidence) are not registry
metrics; CT-5 reports each as NOT_CALCULATED with its missing input (`METRICS.yaml` not_calculated). No proxy
stands in for any of them.

## Taxonomy

The approved, versioned taxonomy (case artifact `taxonomy`, `tools.query_taxonomy`). `requirements.py` gates
every label on it, and since revision 1 checks that it is approved and carries a non-empty version
(`tools.taxonomy_problem`, D-CT17). The package names a family only when its id is in the approved taxonomy; an
indicated family the taxonomy does not define is a limitation, not a label. The package compares ids only; the
definitions are read by the model for an ambiguous event.

## Labels

UNCLASSIFIED, BOUNDARY, INTEGRITY, OVERSIGHT and INCONCLUSIVE. LOOPS, OUTCOME and COST are in the spec's
vocabulary but no approved rule maps evidence to them, so this package never produces them (U-CT1).

## Partial input

Workbook row 11: "Missing sensitivity/file metadata can degrade classification." A family question with a counted
metric unobserved and none indicated is not answered; if no family is indicated and any family question is
unanswered, CT-5 is UNDETERMINED (INCONCLUSIVE). A family that is indicated stands on partial evidence, with the
families not evaluated listed as limitations.
