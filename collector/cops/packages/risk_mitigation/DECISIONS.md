# Risk Mitigation: decisions

Proposed on 2026-11-27 for purvi's review. Nothing here changes a label name, the behavioural question, a
threshold, the register, `specs.metric_keys`, `specs.CONSUMES`, the legacy rule in `cops/interpret.py`, the metrics
engine, VS-01, the requirements gate, triage, the verifiers, authority or the model prompt. The shared changes are
the signal inputs of `tools.calculate_risk_components` (D-RK3, D-RK6), the question tool (D-RK12) and this COP's
edges in `cops_topology.json` (D-RK11).

Numbering: COP 8 in the build order; the workbook and `docs/cops/cop_catalogue.json` number Risk Mitigation 9.

## Acceptance

| # | Date | By | Record |
|---|---|---|---|
| A-RK1 | 2026-11-27 | purvi | Conditionally accepted after review of SPEC.md, INTERPRETATION.md and DECISIONS.md. Decisions on U-RK1 to U-RK10 below ("Revision 1"). Ground truth for the new scenarios was written and committed before the code (b8fe5cc, GROUND_TRUTH.yaml revision R-RK1). Final review is due before COP 9 starts |

## Made in this package

| # | Decision | Alternatives considered | Rationale |
|---|---|---|---|
| D-RK1 | Question ids RK-1 to RK-4, from workbook COMPLETE_DELEGATION_DATA rows 132, 134, 136, 138; metric list row 140 (40 metrics) | RM-1 to RM-4 | RM is Runtime Monitoring's; RS Resource Management's |
| D-RK2 | Only the five `specs.metric_keys` (insights.secrets, sensitive_unasked, outside, reverted, token_outlier) count as observed risk, at EXCEPTION or CRITICAL. The other 20 built metrics are observed and reported under the question they belong to and never counted. The 15 NOT_IMPLEMENTED registry metrics and the 3 spec missing_metrics are NOT_CALCULATED with their missing input | count the oversight or novelty metrics as risk | no approved rule maps them to risk; purvi: do not invent semantics |
| D-RK3 | A signal is a verified substantive consumed finding with a risk-bearing label (`METRICS.yaml upstream`). A quiet reading is not a signal. The rule is applied at the source, `tools.calculate_risk_components` (`cop_package.rk_signal_split`), so the legacy rule, VS-01's recomputation and the package count the same signals | fix it in the package only | the old tool counted NORMAL, CONTAINED and ALLOWED as signals: an ordinary session read EMERGING (impact 1 x 3 quiet findings). Fixing only the package left VS-01 recomputing EMERGING and rejecting LOW ("no direct evidence supports LOW"). VS-01's code is unchanged; its input is corrected. The reading before the fix is recorded (`basis.previous_reading`) |
| D-RK4 | Risk-bearing labels: AD ANOMALOUS; ID NEW_ROUTE, PROPAGATING; PE VIOLATION, APPROVAL_REQUIRED; ResM EXCESSIVE, UNAUTHORIZED; IR INCIDENT, WATCH. Quiet: each COP's first interpretation, and ResM INEFFICIENT. Undetermined (INDETERMINATE, PATH_PARTIAL, UNCERTAIN): a limitation, never a signal | count INEFFICIENT; leave APPROVAL_REQUIRED out | read from each COP's interpretations in `specs.py`; no approved list exists, U-RK2 |
| D-RK5 | Independence is `rules.independent_upstream` (lineage overlap with this COP's counted metrics or a signal already counted), as the legacy rule. Unlike D-CT10, a producer's `metric_keys` overlap does not by itself make it correlated | add the metric_keys overlap (C&T) | Policy Enforcement's lineage holds tool calls, not metrics; with the overlap rule it would always be correlated and the accepted ground truth of evaluation C05 and bench F2 (EMERGING or MATERIAL) could not hold. U-RK3 |
| D-RK6 | An irreversible operation (`tools.IRREVERSIBLE`) on a row the collectors disagree on (`collectors_agree` false) is disputed and not counted; it is contrary evidence and a CONFLICTING_SOURCES limitation. Applied in `calculate_risk_components` too (`irreversible_disputed`) | count it (old tool) | purvi's rule for COP 7 (D-CT18): directly disputed evidence does not establish a finding without independent support |
| D-RK7 | RK-1 is INCONCLUSIVE when nothing is observed or inferred and either a counted metric is unobserved or no consumed finding is verified. A LOW band with RK-1 INCONCLUSIVE is INCONCLUSIVE; an EMERGING or MATERIAL band stands on its positive evidence | LOW (old rule) | the absence of risk is not shown when the combined findings are missing (workbook row 13). Changes a label (below) |
| D-RK8 | No register, or a register without EMERGING and MATERIAL thresholds: RK-2 and RK-4 INCONCLUSIVE, the legacy status MISSING_DEPENDENCY kept. The hook runs on every rule path (`rules.risk_mitigation` wraps `_risk_mitigation_legacy`), so the model is not called | let the model run (before) | the model cannot supply impact or thresholds (`profiles` prompt); with no provider it stopped `llm_unavailable` |
| D-RK9 | No reading needs a model: the register formula, the label lists and the control prerequisites decide every case (`operations`: all deterministic) | call the model for EMERGING or MATERIAL, or to choose among controls | purvi: an LLM only where contextual interpretation is genuinely required. Risk Mitigation stays an LLM COP in `profiles`; nothing in the approved rules leaves a judgement open. U-RK4 |
| D-RK10 | RK-4 lists the register controls of the applicable risks. A control whose prerequisites are met is recommended to the risk owner and never applied (`executed: []`); none met is NO_CONTROL_AVAILABLE, which changes neither label nor action. MATERIAL escalates to a person. Residual risk is NOT_ESTABLISHED | apply C-block-write; INSTRUCT for EMERGING | authority: Risk Mitigation may not block; triage: it cannot accept residual risk; no mitigation effectiveness is approved |
| D-RK11 | `cops_topology.json`: added Incident Response to Risk Mitigation; Risk Mitigation's `reads_decisions_from` lists its five producers and Incident Response's `downstream` lists Risk Mitigation; the Risk Mitigation to Incident Response link is marked `feedback`. No other COP's edges changed | leave it | purvi's D-CT19 rule for this COP's own edges; narrows U-CT11 by one missing edge and one feedback edge |
| D-RK12 | `assess_risk_questions` is in `tools.UNSEQUENCED`, as AD's and C&T's question tools | number it | recorded bench replies keep their tool-call ids (D-AD9, D-CT16) |
| D-RK13 | Confidence 0.75 for every decided label, no evidence scaling (the legacy rule's) | scale by Runtime Monitoring's evidence level | unchanged from the rule; uncalibrated, U-RK10 |
| D-RK14 | Memory: a verified, non-LOW finding writes `mitigation_outcome` with the answers, band, score, signals and recommended controls; validated records are read as precedents and change nothing; `incident_outcome` is not read | read incident outcomes into likelihood | no approved use of historical incident frequency; U-RK6 |
| D-RK15 | RK-3 names exposure by event and finding (agent lane, resource); asset criticality and ownership are not known | estimate criticality from paths | no asset inventory; registry R metrics NOT_CALCULATED |

## Changed readings

The package against the rule before this change, on the harness worlds and the bench and evaluation cases.

| Case | Before (legacy rule; no model offline) | Now |
|---|---|---|
| every harness world | INCONCLUSIVE (`llm_unavailable`: an LLM COP with no provider) | decided by the package, no model call |
| normal_low (rule reading) | EMERGING: AD NORMAL, PE ALLOWED and ResM NORMAL counted as 3 signals x impact 1 | LOW (D-RK3) |
| genuine_risk_emerging (rule reading) | MATERIAL: 3 counted x impact 3 = 9 | EMERGING: PE VIOLATION only, 3 (D-RK3) |
| false_positive_policy_concern (rule reading) | EMERGING | LOW, score 1 |
| conflicting_disputed_irreversible (rule reading) | MATERIAL | EMERGING (D-RK6) |
| missing_upstream (rule reading) | LOW | INCONCLUSIVE (D-RK7) |
| missing_register, register_without_thresholds | INCONCLUSIVE MISSING_DEPENDENCY | the same, with the four answers and no model |
| evaluation C05 | EMERGING or MATERIAL through the scripted model (simulated) | EMERGING, decided without the model; 25 of 25 unchanged |
| bench risk_mitigation:normal, abnormal, missing | LOW, EMERGING, INCONCLUSIVE (recorded live replies) | LOW, EMERGING, INCONCLUSIVE, decided by the package; 49 PASS, 1 FAIL, 4 INCONCLUSIVE unchanged |
| bench fleet F2 | FAIL | FAIL: the reported F2 is the recorded live run, whose labels stand (Risk Mitigation INCONCLUSIVE then). The offline scripted F2 now reads Risk Mitigation EMERGING; it fails on Incident Response (generic scripted reply) |

No accepted COP's label moved (all seven harnesses pass unchanged).

## Unresolved (for purvi), first build; decided in A-RK1 (see Revision 1)

| # | Item | Today |
|---|---|---|
| U-RK1 | The workbook's RK-1 external data names Classification & Tagging; `specs.SPEC_CONSUMES` does not. Adding it is acyclic (C&T runs before IR) | not added; a family is a kind, not a risk, so it would be context only |
| U-RK2 | The risk-bearing label lists (D-RK4), in particular APPROVAL_REQUIRED and IR WATCH as signals and INEFFICIENT as quiet | as D-RK4 |
| U-RK3 | Independence by lineage only (D-RK5), or with the metric_keys overlap used for C&T | lineage only |
| U-RK4 | Risk Mitigation is one of the ten LLM COPs but no approved rule leaves a judgement to a model. Candidate roles: choosing among several eligible controls (proportionality), residual risk once effectiveness exists | no model call |
| U-RK5 | Registry D metrics (affected entity count, propagation potential, evidence sufficiency, recurrence) have unapproved proposals; R metrics need the asset inventory; A metrics need action and outcome records | NOT_CALCULATED |
| U-RK6 | `incident_outcome` memory is readable but unused; the workbook's RK-2 external data asks for historical incident frequencies | not read |
| U-RK7 | Impact is the highest register impact at the session's highest sensitivity tier, whichever risk the signals support (legacy formula): a tier-3 session with an unrelated violation gets impact 3 | kept |
| U-RK8 | The register has no approval or version validity check (compare U-CT12); the requirements gate gates LOW only | presence counts |
| U-RK9 | INCONCLUSIVE readings take the shared fallback action (REQUEST_CONTEXT, universal; or PASS_TO_COP) | shared triage, unchanged |
| U-RK10 | Confidence 0.75 is uncalibrated | kept |

## Revision 1 (A-RK1): purvi's decisions

| U | purvi's decision | What was done | Where |
|---|---|---|---|
| U-RK1 | Add Classification & Tagging as an upstream context provider; never an independent risk signal; check ordering and cycles | D-RK18: added to `specs.TOPOLOGY_CONSUMES`, context only (`METRICS.yaml upstream ... context_only: true`), listed in RK-1 `basis.context`, never counted, correlated or scored. `specs.topological_order` is still acyclic (C&T 10th, IR 11th, RiskM 12th of 18). Topology JSON carries the link | `specs.py`, `METRICS.yaml`, `cop_package.rk_signal_split`, `cops_topology.json` |
| U-RK2 | Keep the mapping provisionally; examine whether APPROVAL_REQUIRED and IR WATCH weigh the same as verified violations or incidents; no weight change without a rule | Examined: both counted as one full signal, the same weight as VIOLATION and INCIDENT. Kept. Each is flagged (`basis.signal_kinds`, contrary line, limitation SIGNAL_WEIGHT_PROVISIONAL). Findings below (E-RK1, E-RK2); a proposal (P-RK2) is reported, not applied | `cop_package.RK_SIGNAL_KIND` |
| U-RK3 | Examine independence by lineage and by source-metric overlap; different tool-call ids are not independence; keep genuinely independent findings | D-RK16: a finding also needs one source item not carried by this COP's own observed evidence or a counted signal. Tool-call ids are not source items. The lineage rule (D-RK5) still applies first. The lineage-only reading is recorded | `cop_package.rk_source_items`, `rk_signal_split`, `tools.t_calculate_risk_components` |
| U-RK4 | Approve deterministic Risk Mitigation; keep the LLM designation until the fleet-wide review | No change: no model call (D-RK9). `profiles` still lists Risk Mitigation as an LLM COP | n/a |
| U-RK5, U-RK6 | Keep missing metrics and historical incident frequencies unavailable for scoring | Unchanged: 15 registry metrics and 3 spec metrics NOT_CALCULATED; `incident_outcome` not read | n/a |
| U-RK7 | Investigate the impact defect with independent ground truth; propose the smallest correction binding impact to the applicable risk and its evidence; no asset criticality | Confirmed by scenario unrelated_high_sensitivity_low_impact. Correction proposed (P-RK1), recorded as a diagnostic (`basis.impact_binding`, `applied: false`, limitation IMPACT_NOT_BOUND), not applied: it changes the accepted formula and VS-01's input | `cop_package._rk_bound_impact` |
| U-RK8 | Require approval and a valid version; an unapproved register establishes no EMERGING or MATERIAL; keep audit and historical records | D-RK17: `tools.risk_register_problem`. The register must have `approved: true` and a non-empty string `version`. Otherwise read_risk_register and evaluate_control_prerequisites return missing with the reason, the requirements gate counts it unavailable, and the finding is INCONCLUSIVE MISSING_DEPENDENCY with the requirement `risk_register.approval` or `risk_register.version`. `synthetic.risk_register()` now carries `approved: true`. No recorded bench, live or evaluation record was rewritten | `tools.py`, `requirements.AVAILABLE_IF`, `UNAVAILABLE_WHY` |
| U-RK9, U-RK10 | Keep the conservative fallback and uncalibrated confidence; document both | Unchanged. U-RK10 is now a limitation on every decided reading (UNCALIBRATED_CONFIDENCE). U-RK9 stays with shared triage (REQUEST_CONTEXT or PASS_TO_COP for INCONCLUSIVE) | `_rk_limitations` |

### New decisions in this revision

| # | Decision | Alternatives considered | Rationale |
|---|---|---|---|
| D-RK16 | Source items: lineage metric ids, claim refs (`observable:`, `metric:`, an upstream finding's own items via `finding:`), and in the driver questions' bases: decision items (DENY, REQUIRE_APPROVAL) by row, or `proposed:<action>:<resource>` without a row; measures in a deviation state by metric and row; `quota:<source>` when a quota is exceeded. Telemetry measures and tool-call ids are excluded. This COP's own items are its counted metrics, the rows behind them in a deviation state and its undisputed irreversible rows. Producers are taken in `specs.topological_order`. A risk-bearing finding with no source item is correlated ("independence is not shown") | metric_keys overlap as C&T (D-CT10) | purvi U-RK3. The C&T rule would make Policy Enforcement always correlated (rejected in D-RK5). Source items keep a denied proposed write or an exceeded quota independent, and make an approval missing on the secret row this COP already observes the same signal |
| D-RK17 | Register validity: `approved is True` and `version` a non-empty string. No effective date or expiry is checked, because the register carries none | check thresholds only (before) | purvi U-RK8, the same rule as the taxonomy (D-CT17) |
| D-RK18 | Classification & Tagging consumed as context: class CONTEXT, never in signals, correlated or quiet lists; not counted as "verified upstream" for RK-1; its absence is named as context not listed. The previous reading (D-RK3 baseline) reads `SPEC_CONSUMES`, so it never counts C&T | add it as a signal | purvi U-RK1 |

### Examination (U-RK2)

| # | Finding |
|---|---|
| E-RK1 | APPROVAL_REQUIRED counts as one signal, the same as VIOLATION. It reads "an approval is required and missing", not "a boundary was crossed". When it rests on a row this COP already observes (the .env read under P-read-env), U-RK3 now makes it correlated (multiple_findings_same_metric: LOW, where the lineage-only rule read EMERGING). When it rests on a distinct pending operation (a proposed /staging write), it is still counted at full weight and flagged (approval_required_without_violation: EMERGING) |
| E-RK2 | Incident Response WATCH counts as one signal, the same as INCIDENT. The legacy rule (`cops/interpret.py`, `incident_response`) reads WATCH on four grounds: a single anomaly, several anomalies, "a deny rule fired; the control worked", and "evidence is insufficient". The anomaly grounds rest on Anomaly Detection's finding, which U-RK3 correlates through IR's upstream claim. The insufficient-evidence ground has no source item, so it is correlated. The deny-rule ground rests on insights.blocked, which is not one of this COP's metrics. It would count at full weight for a control that worked. Incident Response is an LLM COP and is INCONCLUSIVE offline, so no harness world reaches WATCH. This is reported, not tested |

### Proposals for purvi's approval (not implemented)

| # | Proposal | Effect if approved |
|---|---|---|
| P-RK1 (U-RK7) | Impact from the highest sensitivity tier among the rows that support the counted risk: this COP's observed rows, its irreversible rows and the observable rows the counted signals cite. A proposed operation carries no tier. The register's `min_sensitivity_tier` still decides which risks apply, and no asset criticality is used. Needs the same input in `tools.calculate_risk_components` (`max_sensitivity_tier` becomes the supporting tier), so VS-01's recomputation agrees. This is a change to its input, not its code | unrelated_high_sensitivity_low_impact: EMERGING to LOW (impact 3 to 1). Every other harness scenario: unchanged (`impact_binding.band` equals the band). Shown in `basis.impact_binding` today |
| P-RK2 (U-RK2) | Count APPROVAL_REQUIRED and IR WATCH only when another independent risk signal or an observed risk supports them, or give a register weight per signal kind. Needs a rule from purvi; no weight is proposed here | approval_required_without_violation would move from EMERGING, depending on the rule |
| P-RK3 (U-RK1, VS-01) | In `support._risk`, mark a context-only producer's component as contextual (neutral), so VS-01 does not read a family as evidence against LOW | the blocked-call probe would go back from PROVISIONALLY_SUPPORTED to SUPPORTED. No harness case moves |

### Changed readings in revision 1

| Case | Before (D-RK5 lineage only) | Now |
|---|---|---|
| the 18 scenarios of the first build | as listed above | unchanged: 158 of 158 expectations still met |
| multiple_findings_same_metric (new) | EMERGING: Policy Enforcement APPROVAL_REQUIRED counted (tool-call lineage) | LOW, verified: APPROVAL_REQUIRED and INCIDENT rest on the secret row this COP observes; C&T INTEGRITY is context |
| independent_distinct_evidence (new) | MATERIAL | MATERIAL: denied prod write and exceeded quota are distinct (score 6) |
| unrelated_high_sensitivity_low_impact (new) | EMERGING | EMERGING (formula kept); P-RK1 would read LOW |
| approval_required_without_violation (new) | EMERGING | EMERGING, flagged as pending approval |
| unapproved_register, register_version_missing (new) | EMERGING (presence was enough) | INCONCLUSIVE MISSING_DEPENDENCY, reason named |
| ct_context_no_inflation (new) | n/a (C&T was not consumed) | EMERGING, score 3, the same as without C&T |
| material_escalates_never_applies (new) | MATERIAL | MATERIAL, ESCALATE to a person, C-block-write recommended, nothing executed |
| evaluation | 25 of 25; C05 EMERGING, E2E EMERGING, E2E-full LOW | unchanged |
| bench (offline) | 49 PASS, 1 FAIL, 4 INCONCLUSIVE; risk_mitigation normal, abnormal, missing PASS; F2 FAIL (recorded live run) | unchanged; no new live call |

Run record (2026-11-27): Risk Mitigation harness 237 of 237; the seven accepted harnesses unchanged (RM 133, ID 204,
AT 138, PE 238, AD 252, ResM 196, C&T 221); suite 933 passed, 1 skipped; evaluation 25 of 25; bench 49/1/4.
Shared changes in this revision: `specs.TOPOLOGY_CONSUMES`, `tools` (register validity, own-evidence input to
calculate_risk_components), `requirements.AVAILABLE_IF` and `UNAVAILABLE_WHY` for risk_register,
`synthetic.risk_register` (`approved: true`), `cops_topology.json`. VS-01, triage, the verifiers, `specs.metric_keys`,
`cops/interpret.py`, labels, authority and the scoring formula are unchanged.

## Harness corrections after the first run

First run 156 of 158. GROUND_TRUTH.yaml is unchanged; every correction is marked in `harness.py`.

| # | What | Why |
|---|---|---|
| C-RK1 | `no_current_cycle_consumer` ignores consumption entries with cycle "previous" | the log also records the previous-cycle feedback edge; the check implementation counted it |
| C-RK2 | The cycle-2 history carries the first cycle's `run_id` and `decided_at` | `graph.prior_feedback` withholds a finding that does not name its cycle; the world passed the bare finding |
| C-RK3 | Incident Response's reading of the feedback is read from its rule finding (`rules.incident_response` through `run_cop`, as `tests/test_cop_influence.py`) and its delivery from the feedback view | Incident Response is an LLM COP; with no provider its published finding is INCONCLUSIVE and carries no rule fields |

After C-RK1 and C-RK2: 157; after C-RK3: 158 of 158.

Revision 1, first run: 235 of 237.

| # | What | Why |
|---|---|---|
| C-RK4 | In multiple_findings_same_metric and ct_context_no_inflation, Classification & Tagging runs with evaluation C08's scripted reply (`evaluation.ct_steps`, simulated), as the C&T harness does | with the secret at tier 3 it has two candidate families (INTEGRITY, BOUNDARY) and needs its model to choose; without one it is INCONCLUSIVE and there is no verified context to list |

After C-RK4: 237 of 237. GROUND_TRUTH.yaml unchanged.
