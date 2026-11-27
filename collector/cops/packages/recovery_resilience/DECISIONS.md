# Recovery & Resilience: decisions

Proposed on 2026-11-27 for purvi's review. Nothing here changes a label name, the behavioural question,
`specs.metric_keys`, `specs.CONSUMES`, `specs.FEEDBACK`, the metrics engine, VS-01, the requirements gate, triage,
the verifiers, authority, the memory policy or another COP's semantics. The shared changes are:
- the new question tool (`tools.assess_recovery_questions`);
- this package's place in `evidence_assessment.PACKAGED`;
- the package's answers applied in `rules.recovery_resilience` (`_rr_questions`), with the legacy reading kept as
  the computational baseline. `rules.recovery_resilience` names the legacy rule's three metric inputs and passes
  them to `_recovery_resilience_legacy`, so `activation._rule_reads` still finds them (the activation bundle check);
- `docs/cops/cops_topology.json`: this COP's `reads_decisions_from` corrected from [] to [behavior_analytics], as
  `specs.CONSUMES` already says. No runtime edge changed.

Numbering: COP 10 in the build order (purvi, 2026-11-27). The workbook and `docs/cops/cop_catalogue.json` number
Recovery & Resilience 13. The build order is recorded apart from that numbering (project memory
`cops-build-order`).

## Acceptance

| # | Date | By | Record |
|---|---|---|---|
| A-RR0 | 2026-11-27 | purvi | Recovery & Resilience approved as COP 10, after its identity was confirmed in the workbook and registry, with the same implementation and verification method as the nine accepted packages. Stop for review before COP 11 |
| A-RR1 | 2026-11-27 | purvi | Conditionally accepted after review of SPEC.md, INTERPRETATION.md and DECISIONS.md. Decisions: U-RR1 the four canonical questions approved, row 197 is RR-1 input data, not a fifth question; U-RR2 the optional, backward-compatible record schema approved provisionally, with recommended, approved, attempted, executed, observed and verified kept apart; U-RR3 PARTIAL_RECOVERY approved for a verified restoration followed by a new failure, with the restoration and the recurrence separately traceable (RR-2 `recovery_verified`, RR-3 `recurring`, `recovery_status`); U-RR4 historical recurrence stays context only; U-RR5 NONE, WATCH and ALERT, no execution authority; U-RR6 WATCH approved for RECOVERED when post-restoration stability is not established; U-RR7 uncalibrated confidence kept, with its limitation; U-RR8 P-RR1 approved, with seven requirements (R-RR1 below). Investigations A and B asked for. C-RR1, C-RR2 and C-RR3 acknowledged as harness corrections, with their reasons recorded and ground truth unchanged. Stop for final review before COP 11 |
| A-RR2 | 2026-11-27 | purvi | Formally accepted after R-RR1 (P-RR1 feedback edge, investigations A and B; harness 469/469, nine accepted harnesses pass, suite 999 passed and 1 skipped, evaluation 25/25, bench 49 PASS, 1 FAIL, 4 INCONCLUSIVE). C-IR3 acknowledged as an approved harness correction, with Incident Response ground truth preserved. Carried forward unresolved: U-RR9 (no record field names the restored resources, so an unresolved irreversible effect is never ruled out), U-RR10 (no approved monitoring window; STABILITY_CONFIRMED means post-restore activity), U-RR7 (uncalibrated confidence, with its limitation). The bench failures are unchanged and not caused by this package: governance_architecture:normal FAIL and fleet F2 FAIL (Risk Mitigation reads EMERGING or MATERIAL=NO INCONCLUSIVE), with 4 INCONCLUSIVE (workflow_orchestration, workload_optimization, knowledge_policy_management, governance_architecture abnormal) |

## Identity confirmed

| Source | What it says |
|---|---|
| `cops/specs.py` SPECS | key `recovery_resilience`; question "After failure or intervention, did the system return to a viable state without compounding the problem?"; metric_keys insights.reverted, insights.retry, insights.failed; missing_metrics recovery_success_after_failure, irreversible_change, recovery_time; labels RECOVERED, PARTIAL_RECOVERY, FAILED_RECOVERY, IRREVERSIBLE; permitted_actions NONE, WATCH, ALERT; downstream incident_response, governance_intelligence |
| `specs.SPEC_CONSUMES` | consumes behavior_analytics; consumed by incident_response and governance_intelligence (current cycle) |
| `profiles.DETERMINISTIC_COPS` | decided by rules, no model |
| `rules.recovery_resilience` | the legacy rule (METRICS.yaml sources.rule). `cops/interpret.py` has no method for this COP |
| `memory.POLICIES` | writes recovery_outcome, reads recovery_outcome, tenant scope |
| `requirements.REQUIREMENTS` | recovery_records REQUIRED for RECOVERED when the session had a failure (`_had_failure`); NOT_APPLICABLE otherwise |
| Workbook COMPLETE_DELEGATION_DATA rows 195 to 205 | four question texts (rows 195, 199, 201, 203), one field list with external data and no question text (row 197), metric list row 205 (31) |
| Workbook agents-harness row 17 | inputs, tools, authority ("Observe, verify recovery, recommend further restoration, initiate approved recovery"), validation ("Must verify actual recovery, not just action success"), orchestration, coordination (Incident Response, Risk Mitigation, Audit, Governance Intelligence) |
| Workbook Sheet1 rows 21, 75, 92 | "Recovery-state machine + retry/recovery ratio + restoration verification"; "Rules. Checks recovery records to see whether failures were resolved"; tools get_recovery_records, validate_checkpoints |
| Workbook many-to-many mapping row 18 | question "Can the system recover safely from failures, and has recovery actually succeeded?"; actions WATCH, ALERT; feeds Incident Response, Risk Mitigation, Audit & Traceability |
| `metric_registry.json` | 31 metrics with consumer recovery_resilience, matching COPS_METRIC_MAPPING.md (sha256 5bd3c16f...) |

## Made in this package

| # | Decision | Alternatives | Why |
|---|---|---|---|
| D-RR1 | Four questions, RR-1 to RR-4, from the four question texts. Row 197 (a field list and external data, no question text) is read as RR-1's inputs: its fields are added to RR-1's, and its external data ("Upstream Incident Response findings, governance intervention records, workflow state before disruption and event timestamps") is what RR-1 asks about | a fifth question with invented text; drop row 197 | inventing question text is not allowed; row 197's data answers RR-1's "has a failure, incident or governance intervention disrupted the workflow?", and RR-1's own row (195) has no external-data cell. U-RR1 |
| D-RR2 | Each of the 31 metrics is assigned to the question its registry meaning answers (METRICS.yaml mapping). The mapping document lists them per COP, not per question | per-COP only | each question needs its own requirements. Disruption (RR-1): failures, stalls, deny-rule blocks. Restoration (RR-2): reverted work, recovery time, resumption, action and verification rates. Recurrence (RR-3): retries, repetition, recurrence and stability measures, the outliers that would show compounding. Sufficiency (RR-4): telemetry quality, work outside the workspace, health and residual risk |
| D-RR3 | RR-1 DISRUPTED when an undisputed row failed (success false or a non-zero exit code: `requirements._had_failure`'s definition), stalled (hung_seconds), was stopped by a deny rule (policy_rule_matched with permission_decision deny), reverted work (reverted_to_earlier) or ran an irreversible command (tools.IRREVERSIBLE), or a recovery record shows a restoration was needed. NOT_DISRUPTED needs insights.failed observed and none of these. A failure resting only on rows the collectors dispute establishes nothing (CONFLICTING_SOURCES) | metric deviation states only | a single failed deploy is a disruption to recover from, though insights.failed reads WARNING. The definition is the one the runtime already uses to require recovery records. Disputed rows: D-CT18 and D-IR7 |
| D-RR4 | A recovery record may carry action, recommended_by, approval, execution, expected_outcome and disruption_refs (METRICS.yaml record_fields), all optional. A legacy record (checkpoint, hash, restored_at, post-restore checks) keeps its meaning: a restoration executed at restored_at, verified by its checks | a new artifact | workbook row 199 column 5 ("Intended recovery action, action execution record, expected recovery outcome and verification results") and row 197 column 5. No store holds these today. The schema needs approval (U-RR2) |
| D-RR5 | Seven states are kept apart in `cop_specific.recovery_status`: intervention recommended (a record names an action), approved (approval APPROVED or NOT_REQUIRED), attempted (execution ATTEMPTED, EXECUTED or FAILED, or a legacy restored_at), executed successfully (execution EXECUTED or legacy restored_at; this says the action ran, not that the system recovered), recovery observed (session evidence: reverted rows, a failed command later succeeding), recovery verified (a record whose recomputed checkpoint hash does not fail and every post-restore check passed), and incomplete, failed or inconclusive (the label and category). `executed_by_this_cop` is always empty | one state field | purvi's item 4; agents-harness row 17 "Must verify actual recovery, not just action success" |
| D-RR6 | RR-2, in the legacy order. An undisputed irreversible command (tools.IRREVERSIBLE) is IRREVERSIBLE_CHANGE (a disputed one is listed and grounds nothing). Records missing: INCONCLUSIVE, MISSING_DEPENDENCY. Records empty: INCONCLUSIVE (no recovery recorded). A record is checked when it was executed, has post-restore checks, and does not conflict with another record on the same checkpoint and time. A checkpoint whose hash fails, or every checked record passing none of its checks, is NOT_RESTORED. Every record checked and passing every check is RESTORED_VERIFIED. Some restored and some not (failed checks or unconfirmed records) is PARTIALLY_RESTORED. No checked record at all is INCONCLUSIVE, with the reason (approval pending, approval rejected, not started, attempted without checks, conflicting records) | the legacy rule unchanged | the legacy rule read a record with no post-restore checks as PARTIAL_RECOVERY. That infers partial success from an attempt alone, which purvi's item 5 rules out. Changes readings (below) |
| D-RR7 | RR-3 RECURRING when an undisputed row fails or stalls after the latest restoration time of a checked record. A restoration verified by its checks with failures recurring afterwards is PARTIAL_RECOVERY (METRICS.yaml recurring_label), not RECOVERED. Retry, repetition and the catalogue recurrence measures are reported. Validated recovery_outcome precedents are listed, with no effect | recurrence as context only | agents-harness row 17: "Monitor recurrence ... partial restoration"; "did the system return to a viable state without compounding the problem?" (specs). A restored checkpoint that fails again is not a viable state. Changes a reading (below). U-RR3 |
| D-RR8 | Behavior Analytics (the only consumed COP) is context: listed with its label and source items, never a ground. A quiet reading (STABLE) grounds nothing | ground on POOR_RECOVERY | its patterns rest on the same failure and retry metrics this COP reads (the same evidence counted once); no workbook input for this COP names it |
| D-RR9 | RR-4 gives the action. INTERVENTION_REQUIRED (ALERT) for IRREVERSIBLE and FAILED_RECOVERY: "further restoration is recommended; a person decides". MONITORING_REQUIRED (WATCH) for PARTIAL_RECOVERY, for an INCONCLUSIVE reading after a disruption, and for RECOVERED without a confirmed stable window. STABILITY_CONFIRMED (NONE) for RECOVERED with at least one undisputed successful action after the restoration time, no recurrence and no telemetry measure in a deviation state. NOTHING_TO_CONFIRM (NONE) with no disruption | NONE for every RECOVERED (legacy) | agents-harness row 17: verify system health, compare residual issues, then close or escalate; a restoration not yet followed by observed work is not shown stable. Changes actions (below). U-RR6 |
| D-RR10 | Authority: NONE, WATCH or ALERT only (specs.permitted_actions). The package never executes, requests or initiates a rollback, restart, block, pause or restoration. "Initiate approved recovery" (agents-harness row 17) is not implemented: authority grants this COP no INITIATE tier | add REQUEST_APPROVAL or INITIATE | authority is not changed from a package. U-RR5 |
| D-RR11 | Severity and confidence are the legacy rule's: IRREVERSIBLE and FAILED_RECOVERY HIGH 0.85, PARTIAL_RECOVERY MEDIUM 0.8, RECOVERED NONE 0.85, INCONCLUSIVE NONE 0.0 | scale by evidence | no approved calibration; UNCALIBRATED_CONFIDENCE is recorded (U-RR7) |
| D-RR12 | Memory. A verified substantive reading writes recovery_outcome (shared step) with the four answers, the category, the recovery status and the record ids. RECOVERED is this COP's quiet label, which the shared step skips by default, so the finding sets the existing `remember_quiet` flag: a verified recovery after a disruption is an outcome worth keeping. Validated recovery_outcome records are read as precedents, comparison only: a precedent changes no answer, label, severity or action. A precedent on the same agent with FAILED_RECOVERY or PARTIAL_RECOVERY is reported as a recurrence, also without effect | weight recurrence | no approved recurrence rule (as U-IR4) |
| D-RR13 | Cycle (superseded in part by R-RR1: Incident Response is now read on the approved previous-cycle feedback edge, as context only). Incident Response consumes this COP in the current cycle (specs.CONSUMES). This COP does not read Incident Response in any form: a current-cycle edge back would close a cycle, and a previous-cycle edge is a new feedback relationship that needs approval (P-RR1, CONNECTIONS.md). RR-1 reads the session's own timestamped rows and the recovery records instead | add the edge | purvi's item 6: no silent topology change and no circular evidence |
| D-RR14 | A recovery finding never changes the evidence it rests on or Incident Response's: the package reads its inputs and writes only its own finding and recovery_outcome record | none | purvi's item 12 |
| D-RR15 | The legacy reading stays the computational baseline in `cop_specific.legacy_reading`. When the package reads differently, PREVIOUS_READING_DIFFERS names both readings | drop it | same as the accepted packages |
| D-RR16 | VS-01 (`support._recovery`) is unchanged: the failure metrics are a precondition, not evidence either way | a package profile | no change asked; shared |

## Corrections after the first run (2026-11-27)

The first harness run met 332 of 337 expectations. Ground truth was not edited. One code fix, three harness
corrections:

| # | What failed | Correction | Why |
|---|---|---|---|
| fix | recovered_verified_stable `record_written` | the finding sets `remember_quiet` for RECOVERED (D-RR12) | the shared memory step skipped the quiet label; ground truth asks for the record |
| C-RR1 | failed_recovery_grounds_incident `failed_grounds_checkable` | the check reads the claim's result_field and asserted value, not the tool name | harness slip: the claims carry the field path, not the tool |
| C-RR2 | approval_pending `action_within_authority` | the check also accepts a triage PASS_TO_COP (REQUEST_COP_SUPPORT) that authority passed | shared triage refers the case to Behavior Analytics when its finding is missing (AU-01 PASS); the package's own proposal is WATCH. Not a package action |
| C-RR3 | evidence_change_changes_decision variant `no_later_work` | the variant restores as the session's last step (`restore_last()`) | `synthetic.Session.full_instrumentation` adds an Edit after the extras, so the first world had successful work after the restore |

After these: 337 of 337. The nine accepted harnesses were run with this package in place and their counts are
unchanged (below).

## Readings that changed from the legacy rule

| Scenario | Legacy | Package | Decision |
|---|---|---|---|
| attempted_unconfirmed, adversarial_self_reported_success | PARTIAL_RECOVERY, WATCH | INCONCLUSIVE, WATCH | D-RR6: an attempt without checks is not partial success |
| successful_block_not_recovered | PARTIAL_RECOVERY, WATCH | INCONCLUSIVE, WATCH | D-RR6: a block that ran is not recovery |
| approval_pending, approval_rejected | PARTIAL_RECOVERY, WATCH | INCONCLUSIVE, WATCH | D-RR6: nothing was attempted |
| disputed_restoration_evidence | PARTIAL_RECOVERY, WATCH | INCONCLUSIVE, WATCH | D-RR6: conflicting records are not checked |
| irreversible_disputed | IRREVERSIBLE, ALERT | RECOVERED, WATCH | D-RR6: a disputed command grounds nothing; stability not confirmed (D-RR9) |
| recurrence_after_recovery | RECOVERED, NONE | PARTIAL_RECOVERY, WATCH | D-RR7, U-RR3 |
| recovered_stability_unobserved | RECOVERED, NONE | RECOVERED, WATCH | D-RR9, U-RR6 |
| missing_recovery_records, empty_records_resumption_observed, conflicting_telemetry_disputed_failure | INCONCLUSIVE, NONE | INCONCLUSIVE, WATCH | D-RR9: a disruption with recovery unknown is watched |

No label name changed. No other COP's reading moved in its harness.

## Proposed for approval (approved 2026-11-27, implemented in R-RR1)

| # | Proposal | Status |
|---|---|---|
| P-RR1 | A previous-cycle feedback edge Incident Response → Recovery & Resilience (`specs.FEEDBACK["recovery_resilience"] = ("incident_response",)`, verified only). RR-1 would read the earlier cycle's verified INCIDENT, with its decided_at before the restoration time, as context naming the disruption and its containment plan. It would never ground FAILED_RECOVERY or IRREVERSIBLE and never change Incident Response's finding. The edge passes the `specs` FEEDBACK assertion (Incident Response is not in CONSUMES[recovery_resilience]) and adds no current-cycle cycle. Incident Response's deferred_downstream entry (U-IR5) names the same edge | approved by purvi (A-RR1, U-RR8) and implemented in R-RR1 |

## Decided (A-RR1); kept for the record

| # | Question | Today | Options |
|---|---|---|---|
| U-RR1 | Row 197 has no question text (D-RR1) | read as RR-1's inputs | a fifth question once its text is supplied |
| U-RR2 | Recovery record schema (D-RR4) | optional fields, read when present | approve; change; a separate intervention-record store |
| U-RR3 | Recurrence after a verified restoration reads PARTIAL_RECOVERY (D-RR7) | PARTIAL_RECOVERY | RECOVERED with a recurrence limitation |
| U-RR4 | Incident recurrence across sessions (recovery_outcome precedents) | listed, no effect | an approved recurrence rule |
| U-RR5 | "Initiate approved recovery" (D-RR10) | not implemented | an authority grant with a person's approval |
| U-RR6 | RECOVERED without a confirmed stable window is WATCH (D-RR9) | WATCH | NONE as the legacy rule |
| U-RR7 | Confidence is the legacy rule's constants, uncalibrated | kept | calibrate against outcomes |
| U-RR8 | P-RR1, the Incident Response feedback edge | not added | approve; keep deferred |

All eight were decided by purvi on 2026-11-27 (A-RR1).

## Run record (2026-11-27)

| Check | Result |
|---|---|
| Recovery & Resilience harness | 337 of 337 (27 scenarios), no model call |
| Accepted harnesses | IR 478/478 (6 superseded), RK 274/274 (21 superseded), CT 221/221, RS 196/196, AD 252/252, PE 238/238, ID 204/204, AT 138/138, RM 133/133: unchanged |
| Package tests | `tests/test_cop_package_recovery_resilience.py` 27 passed |
| Full suite | 986 passed, 1 skipped, 1 failed on the first run: `test_evidence_assessment` lists the harnesses that read the package, and this harness was added to it, as for every earlier COP. Re-run of that test passed: 987 passed, 1 skipped |
| Shared fix during the run | `test_activation_router`: the activation bundle reads a rule's metric inputs from its source; `rules.recovery_resilience` now names them (above). Re-run passed |
| Evaluation | 25 of 25 PASS, E2E and E2E-full PASS |
| Bench | 49 PASS, 1 FAIL, 4 INCONCLUSIVE (unchanged from COP 9); recovery_resilience 3 of 3 PASS; F2 FAIL as before (Risk Mitigation, open). No new live model call (recorded replies and deterministic reruns only) |

## Revision 1 (R-RR1, 2026-11-27)

Ground truth was written first (commit e441a92): 15 new scenarios and four superseded expectations, each superseded
expectation carrying purvi's decision as its reason.

### P-RR1: the Incident Response feedback edge

| # | Requirement (purvi) | Implemented as |
|---|---|---|
| 1 | Only verified findings from completed earlier cycles | `specs.FEEDBACK["recovery_resilience"] = ("incident_response",)` and `("incident_response", "recovery_resilience")` in `FEEDBACK_VERIFIED_ONLY`. `graph.prior_feedback` (shared, unchanged) withholds an unverified finding, one from the current cycle and one without a run_id. `cop_package.rr_earlier_incident` also rejects a finding that is not verified and SUBSTANTIVE |
| 2 | Cycle identity, session or incident association, timestamps | run_id required; decided_at required (REJECTED_NO_TIMESTAMP); association by the case session_id or by a recovery record's disruption_refs naming the finding (REJECTED_WRONG_SESSION otherwise) |
| 3 | The incident precedes the recovery observation | decided_at must be on or after the session's first undisputed disruption (REJECTED_STALE: it cannot name this disruption) and before the latest attempted restoration time (REJECTED_ORDER). With no restoration time it is LISTED_NO_RESTORATION and contextualizes nothing. No time threshold is used |
| 4 | Context, never proof | read after the four answers and never by them (`rules._rr_questions` calls it after the assessment; `decide_recovery_resilience(questions, earlier)` uses it only for the record). Harness: every p1 scenario reads the same as the run without feedback; an earlier incident with an attempted, unchecked restore reads INCONCLUSIVE, and with no records INCONCLUSIVE, never FAILED_RECOVERY |
| 5 | Never modify the earlier finding | only its identity fields are copied into `cop_specific.earlier_incident`; the harness checks the history object is unchanged after each cycle |
| 6 | Keep the current-cycle RR → IR edge | unchanged: `specs.CONSUMES["incident_response"]` still holds this COP; the harness checks Incident Response reads this cycle's finding |
| 7 | No same-cycle cycle, no self-validation | the edge is not in CONSUMES (topological order unchanged); a current-cycle edge back still closes a cycle. An earlier incident grounded on this COP's own earlier reading (failed_recovery or partial_recovery ground) is flagged `rests_on_this_cop` and never read as confirmation. Over three cycles, and with the same finding offered twice, each cycle reads as without feedback, Incident Response reads the same with and without it, and the memory payload does not accumulate (it records only the finding_id as context) |

Provenance kept: finding_id, run_id, decided_at, session_id, association, label, verified, grounds, containment plan
status, the first disruption and restoration times, status and reason. The shared feedback row (`consumption`,
cycle previous, source_run_id) records the delivery. INCIDENT_CONTEXT_NOT_READ now names why nothing was read when
no finding is accepted.

Shared changes: `specs.FEEDBACK`, `specs.FEEDBACK_VERIFIED_ONLY`, `docs/cops/cops_topology.json` (feedback link
incident_response → recovery_resilience), Incident Response's METRICS.yaml `deferred_downstream` status (WIRED).
Incident Response's semantics are unchanged.

### A: the disputed irreversible command (irreversible_disputed)

Finding: the disputed `rm -rf /ws/build` runs one minute after the session's restore step. The record's checkpoint
holds `app.py` and `db_schema`, and its three checks are unnamed. No record field names the resource the command
touched, so its effect on the restored state cannot be ruled out. The record's own restored_at
(2026-10-20T10:30:00Z) is after the session, so against the record the command reads BEFORE_RESTORATION.

Correction, the narrowest that keeps the checkpoint evidence:
- RR-2 stays RESTORED_VERIFIED and the label RECOVERED. The disputed command is still not an IRREVERSIBLE ground.
- `unresolved_effects` lists the command, its order against the restoration and `ruled_out: false`.
- Category RECOVERED_CHECKPOINT_ONLY; `recovery_status.recovery.verified_scope` reads "checkpoint only".
- Limitation IRREVERSIBLE_EFFECT_NOT_RULED_OUT.
- RR-4 is never STABILITY_CONFIRMED while an unresolved effect stands (MONITORING_REQUIRED, WATCH).

No ruled-out rule is implemented: with the current schema there is no field to rule it out. A record field naming the
restored resources would allow one (U-RR9).

### B: post-restore activity is not sustained stability

Finding: STABILITY_CONFIRMED rests on at least one successful undisputed step after the restoration, no recurrence and
no deviating telemetry. That is successful post-restore activity. No monitoring window is approved, so the claim of
sustained stability was unsupported in the explanation ("stable: ...").

Correction: the answer and its action stay as approved (STABILITY_CONFIRMED, NONE; U-RR6). The RR-4 basis gains
`stability_evidence` (kind POST_RESTORE_ACTIVITY, the successful steps, their span, sustained_stability
NOT_ASSESSED). `recovery_status.recovery.stability_scope` says the same. Limitation SUSTAINED_STABILITY_NOT_ASSESSED is
recorded. The explanation now reads "post-restore activity succeeded ...; sustained stability not assessed (no
approved monitoring window)". No window threshold is invented.

### Readings that changed

| Scenario | Before | After |
|---|---|---|
| irreversible_disputed | RECOVERED, WATCH, RECOVERED_VERIFIED | RECOVERED, WATCH, RECOVERED_CHECKPOINT_ONLY (label and action unchanged) |
| a_disputed_irreversible_later_work (new) | the rule before R-RR1 gives STABILITY_CONFIRMED, NONE | MONITORING_REQUIRED, WATCH |
| every RECOVERED with STABILITY_CONFIRMED | NONE | NONE, qualified as post-restore activity |

The other 26 original scenarios read exactly as before (label, action, category and the four answers compared
against commit 24cb1dd). No other COP's reading changed.

### Corrections after the first R-RR1 run

| # | What failed | Correction | Why |
|---|---|---|---|
| fix | p1_self_validation_guard `earlier_incident_rests_on_this_cop_flagged` | `rr_earlier_incident` reads Incident Response's grounds from its IR-1 basis | code slip: `incident_status.incident.grounds` lists only grounds that were not correlated; the failed_recovery ground was listed as correlated there |
| C-IR3 | Incident Response `topology_doc_matches_runtime_edges` (topology_consistent, r1_recovery_downstream_deferred) | the check also expects Incident Response's outgoing feedback edges, as feedback links | the check predates any outgoing feedback edge from Incident Response; marked in the Incident Response harness and DECISIONS.md. Ground truth unchanged |

### Remaining

| # | Question | Today |
|---|---|---|
| U-RR9 | A record field naming the restored resources, so a disputed irreversible command's effect could be ruled out | not in the schema; the effect is never ruled out |
| U-RR10 | A monitoring window for sustained stability | none approved; STABILITY_CONFIRMED means post-restore activity |
| U-RR7 | Confidence | uncalibrated, with its limitation (kept per A-RR1) |

### R-RR1 run record (2026-11-27)

| Check | Result |
|---|---|
| Recovery & Resilience harness | 469 of 469 (42 scenarios, 4 superseded with purvi's decisions), no model call |
| Accepted harnesses | IR 478/478 (7 superseded; C-IR3), RK 274/274, CT 221/221, RS 196/196, AD 252/252, PE 238/238, ID 204/204, AT 138/138, RM 133/133 |
| Topology and downstream tests | `test_cop_communication.py`, `test_cop_package_incident_response.py`, `test_cop_package_recovery_resilience.py`: 77 passed |
| Full suite | 999 passed, 1 skipped |
| Evaluation | 25 of 25 PASS |
| Bench | 49 PASS, 1 FAIL, 4 INCONCLUSIVE (unchanged); recovery_resilience and incident_response 3 of 3 each; F2 FAIL as before. No new live model call |
