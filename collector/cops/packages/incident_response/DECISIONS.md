# Incident Response: decisions

Proposed on 2026-11-27 for purvi's review. Nothing here changes a label name, the behavioural question,
`specs.metric_keys`, `specs.CONSUMES`, `specs.FEEDBACK`, the legacy rule in `cops/interpret.py`, the metrics
engine, VS-01, the requirements gate, triage, the verifiers, authority, the memory policy or the model prompt. The
shared changes are the new question tool (`tools.assess_incident_questions`, D-IR14), this package's place in
`evidence_assessment.PACKAGED`, and the read-back hook in `graph.INTERPRETED` (D-IR11).

Numbering: COP 9 in the build order (purvi, 2026-11-27); the workbook and `docs/cops/cop_catalogue.json` number
Incident Response 6. The build order is recorded apart from that numbering (project memory `cops-build-order`).

## Acceptance

| # | Date | By | Record |
|---|---|---|---|
| A-IR0 | 2026-11-27 | purvi | Incident Response approved as COP 9, with the same implementation and verification method as the eight accepted packages. Stop for review before COP 10 |
| A-IR1 | 2026-11-27 | purvi | Conditionally accepted. U-IR1 approved with a correction: an incident established by verified deterministic evidence is kept whatever the model does, and the containment plan is recorded apart (R-IR1, D-IR21). U-IR2 to U-IR6 decided as listed under Resolved. C-RK5 and C-AD acknowledged, the original expectations unchanged. Ground truth for R-IR1 written first (2c702b9). Stop for final review before COP 10 |

## Sources read

| Source | What it gives |
|---|---|
| Workbook COMPLETE_DELEGATION_DATA rows 94, 96, 98, 100, 102 | the behavioural question and IR-1 to IR-5, with each row's field list; no external-data column |
| Same sheet row 104 | the 20 built metrics (identical to the registry's 20 category E consumers) |
| agents-harness row 10 | inputs, partial-input rule ("Partial upstream evidence should reduce certainty, not erase findings"), authority ("Direct intervention only with authority"), memory ("open incident state, previous related incidents, actions attempted, approvals"), "Recommended != attempted != confirmed action", downstream (Recovery & Resilience, Governance Intelligence) |
| many-to-many mapping row 11 | inputs: insights.secrets, insights.sensitive_unasked, insights.blocked, findings from Anomaly Detection and Policy Enforcement, affected entities; actions WATCH, ALERT, INSTRUCT, REQUEST_APPROVAL, PAUSE, BLOCK, ESCALATE |
| `metric_registry.json` | 34 consumers: 20 E built, 14 NOT_IMPLEMENTED (D 4, O 5, R 1, A 4) |
| `cops/interpret.py` incident_response, `cops/runtime/rules.py` incident_response | the legacy rule (METRICS.yaml `sources.rule`) |
| `cops/specs.py` | labels NO_INCIDENT, WATCH, INCIDENT; permitted actions; consumes RM, AD, PE, ID, WO, RR (+ C&T, ResM from the topology); feedback from Risk Mitigation, verified only |
| `cops/authority.py`, `triage.py` | REQUEST_APPROVAL tier REQUEST, ESCALATE INITIATE, PAUSE/BLOCK INTERVENE; WATCH ceiling ALERT, INCIDENT ceiling INTERVENE; specialist "cannot pause, block or stop a session" |
| `memory.MEMORY_POLICY` | writes incident_outcome; reads incident_outcome, anomaly_observation, enforcement_record, recovery_outcome; scope tenant |

## Made in this package

| # | Decision | Alternatives considered | Rationale |
|---|---|---|---|
| D-IR1 | Question ids IR-1 to IR-5, from workbook rows 94 to 102. Metrics assigned by each row's field list: IR-1 secrets, sensitive_unasked, blocked (REQUIRED: `specs.metric_keys`), outside; IR-2 subagent_lanes, mcp_servers, first_seen_mcp, unattributed; IR-3 failed, reverted, retry, stalled; IR-4 the five telemetry measures; IR-5 no_oversight, fast_approvals, declined_inferred. The 14 NOT_IMPLEMENTED registry metrics go to the question their proposal answers and are NOT_CALCULATED with the missing input; the three spec missing_metrics (time_to_harm, intervention_available, affected_entities) are NOT_CALCULATED too | a flat list; count the context metrics | the workbook row's fields name what each question reads; nothing is approximated |
| D-IR2 | The grounds are the legacy rule's, and only those. Incident: a verified Policy Enforcement VIOLATION, a verified Recovery & Resilience FAILED_RECOVERY or IRREVERSIBLE, an exposure (insights.secrets or insights.sensitive_unasked in EXCEPTION or CRITICAL). Finding without incident: a verified Anomaly Detection ANOMALOUS, insights.blocked in a deviation state (a deny rule fired), and two verified findings the rule did not read (D-IR3). Every other consumed finding is context | ground on Interaction Discovery PROPAGATING or Resource Management UNAUTHORIZED | the inputs named by the workbook (mapping row 11: AD, PE, the three metrics) and the rule; a route or a consumption reading is another COP's question (new route != prohibited route; high use != unauthorized use). No semantics invented |
| D-IR3 | Policy Enforcement APPROVAL_REQUIRED and Recovery & Resilience PARTIAL_RECOVERY are verified governance findings: WATCH (finding without incident), never an incident on their own, and their workflows stay with their producers | NO_INCIDENT (legacy: neither is read) | purvi's distinction between verified governance findings and incidents; P-RK2 for Risk Mitigation (a pending authorization is not a violation). Changes a reading (below). U-IR2 |
| D-IR4 | Severity (IR-2) is the rule's: HIGH for a VIOLATION, a failed or irreversible recovery, or an exposure with an independent verified anomaly; MEDIUM for an exposure, or an anomaly Anomaly Detection alerts on; LOW for other findings without incident; NONE otherwise | count grounds | the rule has no count; several grounds are one incident (D-IR6) |
| D-IR5 | Response (IR-5) is the rule's action: REQUEST_APPROVAL for a verified VIOLATION whose operation Policy Enforcement proposes to block; ESCALATE for a failed recovery or an exposure with an independent anomaly; ALERT for other incidents and for an anomaly Anomaly Detection alerts on; WATCH for other findings; NONE. REQUEST_APPROVAL wins over ESCALATE, ESCALATE over ALERT | PAUSE or BLOCK for HIGH | the rule; authority: a pause or block needs a person (triage specialist). One combination reads differently from the rule: a VIOLATION without a pending block together with a failed recovery escalates (the rule alerted, because the failed-recovery override ran only when the label was not yet INCIDENT) |
| D-IR6 | Correlation. Each ground carries its source items (`cop_package.rk_source_items`, the same function Risk Mitigation uses: rows, metrics, decision items, quota records; tool-call ids never count). A ground whose items are all carried by grounds taken earlier (own exposure first, then producers in topological order) is correlated: listed, not a separate ground. An anomaly correlated with the exposure does not raise the severity to HIGH. However many grounds, a case has one incident, with its grounds and their independence | count grounds into severity | the profile prompt ("correlated upstream findings that rest on the same metric are one signal") and purvi's item 5. Changes a reading only when an anomaly shares the exposure's evidence |
| D-IR7 | Evidence. IR-1 is INCONCLUSIVE (insufficient evidence to establish an incident) when there is no ground and any of: a required metric unobserved; no consumed finding verified; Runtime Monitoring verified INSUFFICIENT; an incident-grade label present but unverified; the only exposure on rows the collectors dispute. A ground stands on partial evidence; partial evidence lowers confidence through the rule's formula | WATCH for INSUFFICIENT (legacy) | agents-harness row 10 ("reduce certainty, not erase findings"); purvi's item 4 (insufficient evidence is its own reading); D-CT18 (directly disputed evidence does not establish a finding alone). Changes a reading (below) |
| D-IR8 | IR-3 state. ONGOING: a denied operation still pending (Policy Enforcement proposes BLOCK), a failed or irreversible recovery, or outbound calls after a secret was read (`check_exposure.outbound_after_secret`). CONTAINED: none of those, no exposure, and a deny rule stopped an operation. RESOLVED: never, until resolution records exist (incident_resolution_status NOT_CALCULATED). Otherwise INCONCLUSIVE: still uncertain, with the reason. No incident and no finding: NO_INCIDENT_STATE | read RESOLVED from Recovery & Resilience RECOVERED | restoration is not resolution; no resolution record exists |
| D-IR9 | IR-4. INTERVENTION_JUSTIFIED: an incident with an intervention ground (pending block, failed recovery, exposure with an independent anomaly) that IR-3 does not show CONTAINED; every such ground is a verified upstream finding. ATTENTION_JUSTIFIED: any other incident. NOT_JUSTIFIED: no incident. INCONCLUSIVE with IR-1 | require IR-3 ONGOING | an exposure's state is often uncertain (D-IR8); the rule escalates it regardless |
| D-IR10 | Label from IR-1 (INCIDENT, WATCH, NO_INCIDENT, INCONCLUSIVE); severity from IR-2; action from IR-5; confidence the rule's (the lowest confidence of the verified RM, AD, PE and ID findings used, else 0.8 x evidence level). `incident_status` records the category: OBSERVED_ABNORMAL (WATCH on an anomaly only), VERIFIED_FINDING (WATCH on a governance finding or a deny rule), INCIDENT_ATTENTION, INCIDENT_INTERVENTION, INSUFFICIENT_EVIDENCE, NONE | new labels | the label vocabulary is the spec's; the five categories purvi asked for are recorded, not new labels |
| D-IR11 | Model role. Every reading is decided by the package except an INCIDENT whose intervention IR-4 justifies. Then the model is called to scope the incident and draft the containment plan, and `interpreted_incident_response` checks its conclusion against the package's answers: its label must be INCIDENT and its action the package's IR-5 action (a different action is replaced and recorded; a different label, or no verified conclusion, makes the finding INCONCLUSIVE with the package's answers and the rejected candidate kept). The model never changes the label, severity or response class | decide everything without a model (as Risk Mitigation); let the model decide intervention incidents | purvi: an LLM only where contextual interpretation is genuinely required. The rules decide whether an incident exists and which response class it needs; scoping several entities into one containment plan and comparing it with validated incident precedents is the interpretation left (triage specialist: "whether findings form one incident and a containment plan to propose"). U-IR1 |
| D-IR12 | Authority. The package proposes only NONE, WATCH, ALERT, REQUEST_APPROVAL and ESCALATE. It never proposes PAUSE, BLOCK or INSTRUCT; a block is requested through REQUEST_APPROVAL and executes only after a person approves it (graph approval interrupt); in dry run nothing executes. `incident_status.intervention.executed` is always empty | propose BLOCK for a pending denied write | agents-harness row 10 ("direct intervention only with authority"); "recommended != attempted != confirmed" |
| D-IR13 | Memory. A verified WATCH or INCIDENT writes `incident_outcome` (shared step) with the five answers, category, grounds and their sources, response and precedents read. Validated `incident_outcome` records are read as precedents, comparison only: a precedent changes no answer, label, severity or action. A precedent on the same agent is reported as a recurrence, also without effect. anomaly_observation, enforcement_record and recovery_outcome are in the policy but not read: their producers' current findings are consumed directly | weight recurrence into severity | no approved recurrence rule; U-IR4 |
| D-IR14 | `assess_incident_questions` is a baseline tool (rules `DETERMINISTIC_TOOLS`) in `tools.UNSEQUENCED` | number it | recorded bench and live replies keep their tool-call ids (D-AD9, D-CT16, D-RK12) |
| D-IR15 | Feedback. Risk Mitigation's previous-cycle finding (`specs.FEEDBACK`, verified only) is recorded in IR-1 `basis.previous_cycle` and as `earlier_risk_assessment`; it is never a ground and changes nothing. Risk Mitigation's current-cycle finding is not an input (Incident Response runs before it) | read the current risk band | avoids circular reasoning: Risk Mitigation counts an INCIDENT; Incident Response must not count Risk Mitigation's band of that incident |
| D-IR16 | Classification & Tagging is context only, as for Risk Mitigation (U-RK1): listed in IR-1 `basis.context`, never a ground | read the family as an incident category | kind, not severity or response (C&T prompt) |
| D-IR17 | The legacy rule's reading stays the computational baseline (`cop_specific.legacy_reading`); where the package reads differently both are recorded | replace the rule | the accepted packages' method |
| D-IR18 | `cops_topology.json` carries this COP's edges exactly: Interaction Discovery and Resource Management to Incident Response and Incident Response to Governance Intelligence added (all in `specs.CONSUMES`); `reads_decisions_from` = `specs.CONSUMES`, `downstream` = Risk Mitigation, Governance Intelligence; the two producers' `downstream` lists Incident Response. No other COP's edges changed | leave it | purvi's D-CT19 rule for a COP's own edges (as D-RK11) |
| D-IR19 | A pending block is read from the producer's own proposal, the published `proposed_action.requested_action` (falling back to `action`). In dry run the published `action` is the authority substitute (ALERT for a BLOCK), so the legacy rule's `action == "BLOCK"` test never fired there and its reading stayed ALERT | read only `action` | the workbook's question is what Policy Enforcement proposes, not what dry run let through; both are kept in IR-1 `basis.upstream` (`action`, `final_action`) |
| D-IR20 | After an accepted model conclusion the severity is IR-2's and the confidence the lower of the model's and IR-4's; the model's are recorded in `model_check` | keep the model's | IR-2 rates and IR-4 sets the confidence (D-IR10); the model scopes and plans only (D-IR11) |

| D-IR21 | Incident and plan decoupled (R-IR1, A-IR1). The read-back publishes the model's finding only when it concludes the package's label with the package's action, passes verification and gives a plan (plan VERIFIED, or UNVERIFIED with no plan text). Otherwise the model output is set aside, and the package's own deterministic finding is handed back (`cop_specific.verify_package_finding`). `graph.independent_verify` verifies it by `graph._verify_candidate`, with the same verifiers and VS-01 and no model baseline. If it passes, INCIDENT is published with the package's severity, evidence and IR-5 response, and the plan reads REJECTED (different label, different action or failed verification) or UNAVAILABLE (no model). The rejected model output and the reason are kept in `containment_plan.model_output`, `rejected_candidate` and `model_verifier_failures`. If the package's own evidence fails verification, the reading is INCONCLUSIVE (category INCIDENT_UNCONFIRMED, severity NONE, limitation INCIDENT_EVIDENCE_NOT_VERIFIED) | INCONCLUSIVE whenever the model is unavailable or rejected (D-IR11 as first built) | purvi, A-IR1: an unsupported model conclusion never overrides verified incident evidence or authorises a stronger intervention. The proposed action stays REQUEST_APPROVAL, a person approves, nothing is executed |
| D-IR22 | VS-01 profile for Incident Response (`support._incident`): a consumed COP whose METRICS `upstream` entry names no incident, finding, abnormal or evidence-quality label (Interaction Discovery, Workflow Orchestration, Resource Management, Classification & Tagging) is context-only and neutral (NOT_APPLICABLE). The grounding producers (Anomaly Detection, Policy Enforcement, Recovery & Resilience) are read as before | every consumed COP counted | purvi, U-IR3, scoped to Incident Response. Substantive incident verification is unchanged: every other COP's VS-01 is unchanged (harness `other_cops_vs01_unchanged`) |
| D-IR23 | Recovery & Resilience downstream: recorded in METRICS `deferred_downstream.recovery_resilience` (status DEFERRED), not wired in `specs.CONSUMES` | add the edge now | purvi, U-IR5. A current-cycle IR→RR edge with RR→IR already consumed would make a cycle (harness `rr_current_edge_would_cycle`). When RR is packaged the edge is previous-cycle feedback, as RK→IR |

## Resolved (purvi, A-IR1, 2026-11-27)

| # | Decision |
|---|---|
| U-IR1 | Model keeps the containment-planning role, decoupled from incident verification (D-IR21) |
| U-IR2 | WATCH kept for APPROVAL_REQUIRED and PARTIAL_RECOVERY (D-IR3) |
| U-IR3 | Context-only producers neutral in VS-01 for Incident Response only (D-IR22) |
| U-IR4 | Recurrence stays contextual evidence, with no effect, until a rule is approved (D-IR13) |
| U-IR5 | RR downstream relationship preserved, behavioural integration deferred until RR is packaged; topology consistent and acyclic (D-IR23) |
| U-IR6 | Confidence stays the rule's formula, uncalibrated, reported in every finding whose IR-1 is decided as limitation UNCALIBRATED_CONFIDENCE (IR-4, `_ir_limitations`) |

## Changed readings against the legacy rule (harness worlds, synthetic)

| Scenario | Legacy rule | Package | Why |
|---|---|---|---|
| approval_required_without_violation | NO_INCIDENT | WATCH LOW WATCH | D-IR3 (U-IR2) |
| partial_recovery_watch | NO_INCIDENT | WATCH LOW WATCH | D-IR3 (U-IR2) |
| incomplete_telemetry_insufficient | WATCH LOW WATCH | INCONCLUSIVE | D-IR7: insufficient evidence is its own reading |
| conflicting_disputed_exposure | INCIDENT MEDIUM ALERT | INCONCLUSIVE | D-IR7, D-CT18: a disputed row alone establishes nothing |
| missing_upstream_quiet | NO_INCIDENT | INCONCLUSIVE | D-IR7: no verified consumed finding, so the absence of an incident is not shown |
| verified_violation_intervention, simultaneous_findings_one_incident | INCIDENT HIGH ALERT | INCIDENT HIGH REQUEST_APPROVAL | D-IR19: the legacy test read the dry-run substitute ALERT, not Policy Enforcement's BLOCK proposal |

Readings moved by R-IR1 (against the package as first built):

| Scenario | Before R-IR1 | After | Why |
|---|---|---|---|
| violation_without_model, r1_verified_incident_model_unavailable | INCONCLUSIVE | INCIDENT HIGH REQUEST_APPROVAL, plan UNAVAILABLE | D-IR21 |
| adversarial_model_contradicts, r1_model_claims_no_incident | INCONCLUSIVE | INCIDENT HIGH REQUEST_APPROVAL, plan REJECTED | D-IR21 |
| r1_verified_incident_plan_rejected | INCONCLUSIVE | INCIDENT HIGH REQUEST_APPROVAL, plan REJECTED | D-IR21 |
| adversarial_model_overreaches, r1_model_requests_block | INCIDENT (model finding, action replaced) | INCIDENT (package finding), plan REJECTED, BLOCK recorded | D-IR21 |
| r1_incident_evidence_fails_verification | (new) | INCONCLUSIVE, INCIDENT_UNCONFIRMED | D-IR21 |

Every other scenario reads as the legacy rule does. Readings that used to wait on a model, and that now come from
the package without one, are listed in the verification report (`/mnt/project-files/pr4/verification/
incident_response/COP9_INCIDENT_RESPONSE.md`).

## Harness corrections (ground truth unchanged)

| # | Where | Correction | Why |
|---|---|---|---|
| C-IR1 | `harness.run_scenario`, evidence_change_changes_decision | The "only the CLI wrapper" variant is the incomplete_telemetry_insufficient world. The first run put the secret row into the CLI-wrapper session | GROUND_TRUTH.yaml describes that world as "only the CLI wrapper reported ... nothing else", just as "a deny rule instead of the secret" replaces the secret. With the secret present, D-IR7 keeps the ground (INCIDENT). That reading is kept as `cli_only_with_secret` |
| C-IR2 | `harness._not_calculated` | 17 NOT_CALCULATED names (14 mapped plus the 3 spec missing_metrics), not 18 or more | a harness slip; D-IR1 gives 14 |
| C-RK5 | `risk_mitigation/harness.py` quiet_findings_not_counted (normal_low) | incident_response is accepted in RK-2 `quiet_not_counted` when its finding is a verified NO_INCIDENT | before COP 9, Incident Response had no model in that world and read INCONCLUSIVE. It now decides NO_INCIDENT without one. Nothing is counted either way |
| C-AD (COP 9) | `anomaly_detection/harness._ir_reads_ad5` | the rule reading is read from `cop_specific.legacy_reading` when Incident Response decides without a model, and from the model finding's `baseline` otherwise | the same rule reading, recorded in a different place. With AD INDETERMINATE, the package reads INCONCLUSIVE, where the rule read WATCH (D-IR7) |

| C-RK6 | `risk_mitigation/harness.py` genuine_risk_emerging, risk_status_distinguished | Incident Response's INCIDENT in that world is listed among the inferred and correlated readings, not counted. Band EMERGING, score 3 and the controls are unchanged | the incident used to read INCONCLUSIVE in that world (no model). It is now kept (D-IR21), and RK-2 already correlates it on the same source items |
| C-RK7 | `risk_mitigation/harness.py` context_only_neutral_in_vs01, other_cops_vs01_unchanged | incident_response is left out of the "other COPs" set | U-IR3 gives Incident Response its own approved profile (D-IR22). The check still covers every other COP |
| R-IR1 superseded | `GROUND_TRUTH.yaml` `superseded:` | violation_without_model, adversarial_model_contradicts (cop_label INCONCLUSIVE → INCIDENT) and adversarial_model_overreaches (model_action_replaced → plan_rejected, model_action_block_recorded, deterministic_finding_published) | purvi-approved semantics (A-IR1). The original expectations are kept in the file with the reason |
| R-IR1 fault injection | `harness._tamper_package_finding` | scenario 5 only: a false CRITICAL insights.secrets claim is added to the package's computational INCIDENT candidate, so its own evidence fails MC-01 and SC-01 | the only way to make deterministic evidence fail independent verification in a synthetic world; no other scenario uses it |

## Shared tests restated (COP 9)

Each of these tests used Incident Response as the example of a model COP, or as a COP that proposes BLOCK. The
package now decides most readings without a model and never lets a model propose BLOCK (D-IR11, D-IR12), so each
change is marked in the test with why. No assertion about the shared machinery was weakened.

| Test | Change |
|---|---|
| `test_runtime_llm.py` (wiring, unavailable, timeout, malformed, prohibited tool for incident_response) | Incident Response runs after Runtime Monitoring and Policy Enforcement, so a verified VIOLATION justifies intervention and the model is reached |
| `test_runtime_error_paths.py` (two PE-01 tests), `test_runtime_live_path.py` (self-authorising text, out-of-vocabulary action, checkpoint recovery) | run with the `ir_model_path` fixture: the package's decision and read-back are switched off, which is the path these tests were written for |
| `test_runtime_live_eval.py::test_deterministic_cops_make_no_model_calls` | incident_response is added to the COPs that decide without a model in E2E-full |
| `test_runtime_root_causes.py::test_root_causes_can_co_occur_and_use_the_defined_codes` | incident_response:missing co-occurs EXPECTED_INCONCLUSIVE and MISSING_EVIDENCE. There is no model candidate, so there is no VERIFIER_REJECTED |
| `test_evidence_assessment.py` | the Incident Response harness is added to the files that read the package |
| `test_cop_package_risk_mitigation.py::test_vs01_context_only_component_is_not_applicable_for_risk_mitigation_only` (R-IR1) | incident_response left out of the "other COPs" set, as C-RK7: its context-only profile is approved (U-IR3, D-IR22). Every other COP is still checked |
| `test_runtime_llm.py` (R-IR1: wiring, unavailable, timeout for incident_response) | helper `no_model_conclusion`: without a usable model conclusion Incident Response publishes its verified deterministic INCIDENT, origin computational, plan UNAVAILABLE, execution NOT_EXECUTED. Other model COPs keep INCONCLUSIVE |

## Run record (2026-11-27, offline, synthetic, every model reply scripted and simulated)

| Run | Result |
|---|---|
| Incident Response harness | 354 of 354 (C-IR1, C-IR2) |
| Risk Mitigation, Classification & Tagging, Resource Management, Anomaly Detection, Policy Enforcement, Interaction Discovery, Audit & Traceability, Runtime Monitoring harnesses | 274/274 (C-RK5), 221/221, 196/196, 252/252 (C-AD), 238/238, 204/204, 138/138, 133/133 |
| Full suite (`pytest ../tests`) | 958 passed, 1 skipped |
| Evaluation | 25 of 25 PASS (C05, C06, C10a/b, C15, D-IR, E2E and E2E-full included) |
| Bench | 49 PASS, 1 FAIL, 4 INCONCLUSIVE, unchanged. incident_response normal NO_INCIDENT, abnormal INCIDENT, missing INCONCLUSIVE, all PASS. Fleet F2 still FAIL, as before COP 9 |

## Run record, R-IR1 (2026-11-27, offline, synthetic, every model reply scripted and simulated)

| Run | Result |
|---|---|
| Incident Response harness | 478 of 478, 6 superseded (R-IR1) |
| Risk Mitigation, Classification & Tagging, Resource Management, Anomaly Detection, Policy Enforcement, Interaction Discovery, Audit & Traceability, Runtime Monitoring harnesses | 274/274 (C-RK5, C-RK6, C-RK7), 221/221, 196/196, 252/252 (C-AD), 238/238, 204/204, 138/138, 133/133 |
| Full suite (`pytest ../tests`) | 959 passed, 1 failed, 1 skipped. The failure was the RK context-only test; it was restated as C-RK7 above and passes when re-run alone. The full suite was not re-run |
| Evaluation | 25 of 25 PASS |
| Bench | 49 PASS, 1 FAIL, 4 INCONCLUSIVE, unchanged. Fleet F2 still FAIL, as before COP 9 |
