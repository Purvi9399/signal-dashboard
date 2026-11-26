# Resource Management: decisions

Proposed on 2026-11-26 for purvi's review. Conditionally accepted by purvi on 2026-11-26 (A-RS1 below); revision 1
(D-RS17 to D-RS19, R-RS1) accepted on 2026-11-26 (A-RS2). The open limitations below stay open. Nothing here changes a label name, the behavioural
question, a threshold, `specs.metric_keys`, the legacy rule in `cops/interpret.py`, the metrics engine, the
requirements gate, VS-01, triage or shared architecture.

## Acceptance

| # | Date | What purvi decided |
|---|---|---|
| A-RS1 | 2026-11-26 | COP 6 conditionally accepted. U-RS1 approved: adopt leave-one-out baselines with the existing shared method, with baseline versioning and reproducibility, keeping the previous readings in the comparison record (D-RS17). U-RS2 and U-RS3 kept as implemented. The per-session quota interpretation kept provisionally, documenting the missing scope and period (D-RS19). RS-4: verify that ATTRIBUTED cannot come from an absent ID-2 answer and correct narrowly (D-RS18). Unavailable metrics and open limitations kept; no fabricated resource estimates, quotas or workload explanations. Final review before COP 7 |
| A-RS2 | 2026-11-26 | Revision 1 accepted (d9714b8, d1d8e7f): final acceptance of COP 6. Open limitations preserved: U-RS4 (quota scope, measure, period; per-session reading provisional), U-RS5, U-RS6, U-RS7, U-RS8, U-RS9, U-RS10, and the 8 NOT_CALCULATED metrics. COP 7 authorized |

## Made in this package

| # | Decision | Alternatives considered | Rationale |
|---|---|---|---|
| D-RS1 | Question ids RS-1 to RS-5 | RM-1 to RM-5 | RM is Runtime Monitoring's prefix |
| D-RS2 | Only the four `specs.metric_keys` count toward the label, as BURN (token outlier, action rate) and WASTE (duration outlier, stalls), at the legacy states (EXCEPTION and CRITICAL hot, WARNING elevated). The other 27 assigned metrics are observed, reported or NOT_CALCULATED | count every assigned metric with a threshold (repetition, retry, failed, subagent lanes, MCP servers) | purvi: do not change accepted governance semantics without approval. Counting more metrics would make new labels; it is put to purvi as U-RS2 |
| D-RS3 | UNAUTHORIZED only when the session's tokens exceed the quota record's `token_budget` (`tools.get_quota`), as `rules.py` did. A reading above the estate is EXCESSIVE or INEFFICIENT, with or without a quota, and says it is "not above an allowance" | read a large spike as unauthorized; compare across sessions | purvi: do not confuse high consumption with unauthorized consumption. The quota record has no period, so it is compared per session, as before (limitation `quota_period`, U-RS4) |
| D-RS4 | A WARNING alone reads INEFFICIENT (WATCH, LOW), as the legacy rule did | not count WARNING, as Anomaly Detection decided (D-AD3, R-AD1) | keeps the existing meaning; the inconsistency between the two COPs is U-RS3 |
| D-RS5 | Tokens not measured (no row carries total_tokens) leave RS-1 INCONCLUSIVE and the label INDETERMINATE | the legacy reading: NORMAL on the three measures that could be judged | the legacy rule's own principle ("a missing meter is not evidence of thrift") applied per question; token consumption is the first thing RS-1 asks. Changes a label: see "Labels that changed" |
| D-RS6 | A self-inclusive baseline below `min_sessions_for_baseline` for the token outlier or the action rate makes the question BASELINE_INSUFFICIENT and the label INDETERMINATE when nothing is above bounds | NORMAL on the measures that could be judged (legacy) | as Anomaly Detection's BASELINE_INSUFFICIENT; changes a label |
| D-RS7 | Runtime Monitoring verified INSUFFICIENT turns a quiet RS-5 into UNDETERMINED (INDETERMINATE) | NORMAL with the confidence scaled (legacy, 0.27) | the same reading `rules.anomaly_detection` applies; quiet meters on missing telemetry are not consumption within bounds. Changes a label |
| D-RS8 | Positive evidence stands on partial evidence: a measure above bounds, or a quota exceeded, decides even when another question is unanswered or telemetry is INSUFFICIENT; the confidence is scaled by the evidence level and Runtime Monitoring's reading is contrary evidence | INDETERMINATE whenever anything is missing | the legacy rule judged whichever measures could be judged; Interaction Discovery D2 and Policy Enforcement D-PE10 do the same |
| D-RS9 | Blocking checks: CONFLICTING_SOURCES for RS-1, CONFLICTING_ACTION_SOURCES for RS-2 to RS-4, as Anomaly Detection's token and action questions. A failed check leaves the question INCONCLUSIVE with its measures in `observed_not_judged` | no blocking check (the legacy rule ignored reliability) | consumption read from disagreeing collectors is not judged. Changes a label (unreliable_telemetry was EXCESSIVE 0.47) |
| D-RS10 | Attribution (RS-4) is computed in the package (`cop_package._rs4`), moved from `rules._attribute_consumption`, with the same conditions, reason strings, field shape and 0.6/0.9 discount. It adds one condition: every delegation link in ID-2 `basis.links` must be RECORDED. An INFERRED, INFERRED_AGAINST_TIME_ORDER or UNLINKED link leaves consumption UNATTRIBUTED with an INFERRED_RELATIONSHIP limitation | the ID-2 answer alone | purvi: never attribute subagent consumption through an unverified parent-child relationship. ID-2 already reads such a graph as partial; the link check holds even if it did not (tested with a constructed finding) |
| D-RS11 | **Superseded by D-RS17 (2026-11-26).** Baselines stay self-inclusive, as the metrics compute them. The leave-one-out reading (`ad_leave_one_out`) is recorded beside each RS-1 and RS-2 measure and decides nothing | adopt leave-one-out now | purvi: do not silently change baseline methodology. Comparison below; U-RS1 |
| D-RS12 | Confidence, severity, action and recipients are the legacy formulas over the counted measures; confidence is rounded to three places | two places (`interpret._out`) | as the other packages; 0.233 where the legacy gave 0.23 |
| D-RS13 | The finding's rationale says what was compared: the tokens against `token_budget`, or that permission was not evaluated | keep the legacy rationale | the legacy rationale said "no quota configured" in every case, including when a quota was configured and compared; the interpret.py text itself is unchanged |
| D-RS14 | usage_baseline precedents are listed in RS-5 and never decide | use them to adjust baselines | memory is not a measure or an entitlement; no self-validation |
| D-RS15 | Runtime Monitoring is recorded in `upstream_used` as evidence quality, not a claim; Interaction Discovery is a claim only when it attributed | cite every verified upstream | extra claims add lineage and change other COPs' independence (D-PE12) |
| D-RS16 | Every operation is deterministic | a model for "legitimate high workload" | the workbook's questions are settled by approved thresholds and the quota; legitimacy needs a workload record that does not exist (limitation `workload_record`), not a model |
| D-RS17 | Revision 1. Baselines are leave-one-out with Anomaly Detection's shared method: `assess_resource_management` reads the events through `ad_leave_one_out` and the baselines through `_ad_baselines` (method `leave_one_out@1`; version = metric_version, a hash of the baseline session ids, and the method, so the same rows give the same version in any order). `LEAVE_ONE_OUT_COPS` (`anomaly_detection`, `resource_management`) makes `graph._events` (picture, VS-01, legacy rule baseline) and `tools.t_get_metric_rows` and `compute_baseline` read the same rows. `rs_baseline_comparison` keeps the self-inclusive reading (`_rs_self_inclusive`, renamed from `_rs_baselines`) beside each RS-1 and RS-2 measure with its value, state, version and sufficiency; `differs` is true when the state or the sufficiency differs, and the difference is contrary evidence. It decides nothing | a new Resource Management baseline function; drop the self-inclusive reading | purvi approved U-RS1 and asked for the shared method and for the earlier readings to be preserved. Changes two labels (below) |
| D-RS18 | Revision 1, narrow RS-4 fix. `_rs4` attributes only when the verified Interaction Discovery finding has an ID-2 answer equal to FULLY_RECONSTRUCTED (was: FULLY_RECONSTRUCTED or absent), and every subagent lane has a RECORDED delegation link (new). Otherwise UNATTRIBUTED with reason "Interaction Discovery's finding carries no ID-2 answer, so no relationship is verified" or "no recorded delegation link for lane(s): ...". Field shape, the other reasons and the 0.6/0.9 discount are unchanged | leave as it was | purvi: attribution must require independently verified relationships or explicit recorded evidence. Before the fix, a verified finding with its ID-2 answer removed, or with ID-2 FULLY_RECONSTRUCTED and no delegation links, gave ATTRIBUTED (shown by running the new scenario against the be18839 `_rs4`). No existing scenario changed |
| D-RS19 | The quota stays per session, provisionally: the session's tokens against `token_budget`. The record carries no scope, measure or period, while the registry's `budgets_and_quotas` contract has scope, measure, period and limit, so no per-period comparison, no utilisation and no cost comparison is made; `token_budget_utilization`, `cost_budget_utilization` and `resource_quota_compliance` stay NOT_CALCULATED and limitation `quota_period` is reported | compare per period | purvi kept the per-session reading provisionally; U-RS4 stays open for the approved contract |

## Labels that changed in revision 1 (D-RS17)

Against be18839 (self-inclusive), running both harnesses on all 28 earlier scenarios. Every other scenario keeps
its label, confidence and five answers exactly.

| World | be18839 (self-inclusive) | Revision 1 (leave-one-out) | Why |
|---|---|---|---|
| baseline_contamination_half_estate | NORMAL 0.8; RS-1 WITHIN_LIMITS | EXCESSIVE 0.7, ALERT; RS-1 ABOVE_EXPECTED | s1 no longer moves its own median to 20500; against the other seven (median 1000) it reads 40 (CRITICAL) |
| baseline_minimum_counts_self | EXCESSIVE 0.7; RS-1 ABOVE_EXPECTED | INDETERMINATE 0.2; RS-1 and RS-2 BASELINE_INSUFFICIENT, RS-5 UNDETERMINED | s1 no longer counts toward its own minimum: four other sessions, five needed |

The new scenario attribution_id2_absent reads EXCESSIVE 0.467 with RS-4 UNATTRIBUTED.

## Labels that changed against the legacy rule

Against the rule this package replaces (`interpret.py` reading plus the `rules.py` quota override), on the
harness worlds. Every other world keeps its label; confidences match to two places.

| World | Before | Now | Decision |
|---|---|---|---|
| missing_token_measurements | NORMAL 0.8 | INDETERMINATE 0.2 | D-RS5 |
| insufficient_baseline_history | NORMAL 0.8 | INDETERMINATE 0.2 | D-RS6 |
| incomplete_telemetry | NORMAL 0.27 | INDETERMINATE 0.067 | D-RS7 |
| unreliable_telemetry | EXCESSIVE 0.47 | INDETERMINATE 0.133 | D-RS9 |

None of these was in an accepted test; every earlier Resource Management test, evaluation case and bench case
keeps its expectation.

## Baseline methodology: self-inclusive and leave-one-out (U-AD7 follow-up)

Written for be18839, before U-RS1 was approved. Kept as the record of the comparison purvi decided on; D-RS17
adopted the leave-one-out column.

Resource Management reads insights.token_outlier, insights.duration_outlier and activity.action_rate_ratio as
`signal_metrics` computes them: against the estate with the evaluated session in it. Anomaly Detection reads the
same three leave-one-out since revision 3 (D-AD21 to D-AD24). The difference, measured on this package's harness
worlds by running the same package answers on both readings (scratch comparison, not runtime code):

| World | Self-inclusive (used) | Leave-one-out (recorded) | Why |
|---|---|---|---|
| baseline_contamination_half_estate (4 at 1000 tokens, 3 at 40000, s1 at 40000) | token outlier 1.95 NORMAL; RS-1 WITHIN_LIMITS; NORMAL | 40.0 CRITICAL; RS-1 ABOVE_EXPECTED; EXCESSIVE | s1 is the fourth heavy session of eight and moves the median to 20500 |
| baseline_minimum_counts_self (4 earlier sessions, s1 at 40000) | 5 sessions meet min_sessions_for_baseline; 40.0 CRITICAL; EXCESSIVE | 4 sessions are too few; RS-1 and RS-2 BASELINE_INSUFFICIENT; INDETERMINATE | s1 counts toward its own minimum |
| the other 18 single-session worlds | | same labels and answers | one session among eight does not move the median |

So the self-inclusive reading lets the evaluated session hide itself (the first row) and vouch for its own
baseline (the second). Leave-one-out removes both, and Anomaly Detection's comparison found no regression there.
Adopting it here would change these two readings and nothing else in the harness. Not changed: U-RS1.

## Open for purvi

| # | Question | Current behaviour |
|---|---|---|
| U-RS1 | **Resolved 2026-11-26 (A-RS1, D-RS17): leave-one-out adopted.** Adopt leave-one-out baselines for Resource Management, as approved for Anomaly Detection? | leave-one-out; the self-inclusive reading recorded in `basis.leave_one_out` and in contrary evidence when the readings differ |
| U-RS2 | **Kept as implemented (A-RS1).** Should repeated, retried or failed operations count as waste (workbook RS-3 names "repeated operations")? | observed, listed in RS-3 `not_counted` and contrary evidence, not counted (a retry EXCEPTION alone reads NORMAL) |
| U-RS3 | **Kept as implemented (A-RS1).** Should a WARNING alone still read INEFFICIENT, when Anomaly Detection records WARNING without counting it? | INEFFICIENT, WATCH, LOW (legacy) |
| U-RS4 | **Per-session reading kept provisionally (A-RS1, D-RS19); still open.** The quota record: `tools.get_quota` reads `{token_budget}` with no period, scope or measure, while the registry's `budgets_and_quotas` contract has scope, measure, period and limit. Which is approved, where it lives, and whether consumption is compared per session or per period across sessions (RS-5 external data)? Cost budgets likewise | per session against token_budget; cost not compared |
| U-RS5 | Registry questions for `token_quota_exceeded` (rate-limit errors?) and `resource_threshold_breach_count` (which thresholds?) | NOT_CALCULATED |
| U-RS6 | Downstream influence is evidence only: Workload Optimization lists this COP in `specs.CONSUMES` but its rule does not read it; Risk Mitigation always counts it as correlated (both read insights.token_outlier); Incident Response's rule reading does not depend on it | unchanged; recorded in CONNECTIONS.md |
| U-RS7 | INDETERMINATE is written to usage_baseline memory because it is substantive and not the quiet label (`graph.validate_memory_update`, shared). Should it be? | written, as before this package |
| U-RS8 | Missing references: capacity data (no collector), an approved model registry, an approved limit-approach threshold, an approved session duration threshold | each NOT_EVALUATED with a limitation |
| U-RS9 | Subagent token consumption: synthetic and most real rows record tokens on the main turn only, so `subagent_resource_consumption` is UNKNOWN and attribution is by call counts, not tokens | attribution reports calls per actor; tokens per lane not claimed |
| U-RS10 | A legitimately heavy task reads EXCESSIVE (ALERT on a CRITICAL spike) because no workload record exists; Anomaly Detection has task-class context, this COP does not | EXCESSIVE with limitation `workload_record` |

## Ground truth revision

| # | Date | What | Why |
|---|---|---|---|
| R-RS1 | 2026-11-26 | GROUND_TRUTH.yaml revised and committed (d9714b8) before the revised package was run: normal_within_quota baseline checks expect leave_one_out@1 over 7 sessions with the self-inclusive reading (8) recorded; heavy_estate_baseline expects the leave-one-out median 20500 over 14 sessions (answers and label unchanged, 1.95 is below WARNING); baseline_contamination_half_estate expects EXCESSIVE, ALERT, 0.7; baseline_minimum_counts_self expects RS-1 and RS-2 BASELINE_INSUFFICIENT, RS-5 UNDETERMINED, INDETERMINATE, 0.2; new scenario attribution_id2_absent expects RS-4 UNATTRIBUTED with ID-2 removed and with no delegation links | purvi's decisions A-RS1 (U-RS1 and RS-4). Each expectation follows from the approved method and min_sessions_for_baseline, not from a run. First revised run: 196 of 196 |

## Harness corrections after the first run

| # | What | Why |
|---|---|---|
| C-RS1 | The two downstream checks (`risk_mitigation_reads_resource_management`, `incident_response_reads_resource_management`) first read the downstream findings. Risk Mitigation and Incident Response are model COPs and stop with `llm_unavailable` without a provider, so their findings carry no components or upstream list. The checks now read the runtime's consumption record and Risk Mitigation's `calculate_risk_components` result, which is what those COPs consume | a check implementation error; GROUND_TRUTH.yaml is unchanged. First run 186 of 188, then 188 of 188 |
