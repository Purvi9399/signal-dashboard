# Classification & Tagging: decisions

Proposed on 2026-11-26 for purvi's review. Conditionally accepted on 2026-11-27 (A-CT1); revision 1 (D-CT17 to
D-CT19, R-CT1) accepted on 2026-11-27 (A-CT2), the final acceptance of COP 7. The open limitations below stay open. Nothing here changes a label name, the behavioural question, a threshold,
the taxonomy, `specs.metric_keys`, the legacy rule in `cops/interpret.py`, the metrics engine, VS-01, triage, the
verifiers or the model prompt. The shared changes are the three upstream edges in D-CT9 and the taxonomy check in
the requirements gate (D-CT17), both on purvi's instruction, and the matching edges in `cops_topology.json`
(D-CT19).

## Acceptance

| # | Date | What purvi decided |
|---|---|---|
| A-CT1 | 2026-11-27 | COP 7 conditionally accepted. U-CT1, U-CT2: keep the conservative rules; no category mappings and no observational metric as a trigger without approval. U-CT3: tell unrelated collector disagreement from disagreement on the family indicator; directly disputed evidence cannot by itself establish a verified family (D-CT18). U-CT4: the shared requirements gate checks taxonomy approval and version validity, other COPs unchanged (D-CT17). U-CT5: INCONCLUSIVE approved for missing required classification evidence. U-CT6: reconcile `cops_topology.json` with the approved upstream; validate order, no cycles, downstream (D-CT19). U-CT7: keep the four unavailable metrics with their missing dependencies. U-CT8: specs.py stays the question contract. U-CT9: keep the diagnostic baseline comparison, no label change. U-CT10: keep the confidence formula, marked uncalibrated. Final review before COP 8 |
| A-CT2 | 2026-11-27 | Revision 1 accepted (8babdef, c6314e6): final acceptance of COP 7. Preserved: U-CT11 (cops_topology.json differs from the runtime outside this COP), U-CT12 (no taxonomy effective date, expiry or supersession), and the documented limitations U-CT1, U-CT2, U-CT7 (four NOT_CALCULATED metrics), U-CT9 and U-CT10 (confidence uncalibrated); a row not cross-checked counts as undisputed. COP 8 authorized |

## Made in this package

| # | Decision | Alternatives considered | Rationale |
|---|---|---|---|
| D-CT1 | Question ids CT-1 to CT-5, from workbook COMPLETE_DELEGATION_DATA rows 107, 109, 111, 113, 115; metric list row 117 (26 metrics, all category E, IMPLEMENTED_EXISTING) | CL-1 to CL-5 | the workbook's own prefix; no clash with another package |
| D-CT2 | Only the four `specs.metric_keys` indicate a family, with the legacy mapping: insights.outside and insights.sensitive_unasked BOUNDARY, insights.secrets INTEGRITY, insights.blocked OVERSIGHT, at EXCEPTION or CRITICAL. The other 22 assigned metrics are observed and reported. WARNING is recorded and indicates nothing | count no_oversight, fast_approvals, declined_inferred (OVERSIGHT) or first_seen_mcp (BOUNDARY) | purvi: do not invent or redefine classification rules. No approved rule maps the other metrics to a family; U-CT2 |
| D-CT3 | CT-4 (behavioural, execution, integrity or resource concerns) is answered and never counted: no approved rule maps a concern to a family. A CONCERNS_OBSERVED CT-4 over no family reads UNCLASSIFIED with the concern named as unmapped | map concerns to LOOPS, COST or OUTCOME | the spec's LOOPS, OUTCOME and COST have no approved evidence rule; U-CT1 |
| D-CT4 | One candidate family (indicated and defined in the approved taxonomy) is classified by the package without a model; `decided_without_model` lets `graph.evidence_precheck` publish it, as Anomaly Detection | call the model for every event (the runtime before this package) | the approved rule is sufficient for one family; purvi: deterministic where approved rules are sufficient |
| D-CT5 | Two or more candidates are AMBIGUOUS: the model interprets the event against the taxonomy's definitions, and its verified label is read back (`interpreted_classification_tagging`) only when it is one of the candidates. A label outside the candidates, or UNCLASSIFIED over indicated families, is INCONCLUSIVE (INTERPRETATION_OUTSIDE_CANDIDATES, or DS-03) with the rejected candidate kept | the legacy precedence order decides (BOUNDARY before INTEGRITY before OVERSIGHT) | evaluation case C08 shows the order is wrong for a fake credential in a test fixture (accepted ground truth INTEGRITY, order gives BOUNDARY). Which family describes it is a reading of the definitions against the event. The precedence reading stays the computational baseline (`basis.precedence_reading`) |
| D-CT6 | An indicated family whose id is not in the approved taxonomy is not a candidate; it is a limitation (`family_not_defined`) | name it anyway | the taxonomy gates every label (`requirements.GATES_ALL_LABELS`); a label outside the approved vocabulary is not a classification |
| D-CT7 | Partial input (workbook row 11, "missing sensitivity/file metadata can degrade classification"): a family question with a counted metric unobserved and none indicated is INCONCLUSIVE; with no family indicated and any family question unanswered, CT-5 is UNDETERMINED (INCONCLUSIVE 0.0). An indicated family stands on partial evidence, with `families_not_evaluated` listed | UNCLASSIFIED (legacy) | a quiet reading of unobserved metadata is not evidence of no family. Changes a label (below) |
| D-CT8 | An unapproved taxonomy artifact: `requirements.py` treats any artifact as present, so the package stops it (CT-5 UNDETERMINED, taxonomy NOT_SUPPLIED) | change the gate | the gate is shared; changing it needs approval. U-CT4 |
| D-CT9 | `specs.TOPOLOGY_CONSUMES["classification_tagging"]` adds policy_enforcement, interaction_discovery and resource_management (`specs.CONSUMES` is now AD, ALM, PE, ID, ResM). In the topological order C&T and Resource Management swap places | read them out of band | purvi named these upstream findings. Context only: none indicates a family. VS-01's decision was the same on every harness world with five and with the two canonical upstream COPs (`LEGACY_COMPARISON.json`). Not yet in `cops_topology.json`: U-CT6 |
| D-CT10 | Upstream findings never decide a family or a confidence. Each is listed in CT-5 `basis.upstream` with verification status, lineage and `counted_as`. Correlated is `rules.independent_upstream` plus an overlap between the producer's `BY_KEY[k].metric_keys` and this COP's counted metrics; Policy Enforcement reads the same four metrics and is always correlated. Unverified findings (Anomaly Detection without a model) are "not used" | count a verified upstream as independent evidence | purvi: avoid double-counting correlated evidence; do not treat an upstream label as verified without checking provenance |
| D-CT11 | Entity association: the indicating rows are the entities; a subagent lane is linked to its parent only through a verified Interaction Discovery finding whose ID-2 is present and FULLY_RECONSTRUCTED with a RECORDED link for that lane, as Resource Management D-RS18. Otherwise NOT_LINKED, a limitation, no discount | link by recorded parent_agent_id alone; infer from timing | the same rule Resource Management uses; an unverified parent-child relationship is not an association |
| D-CT12 | Confidence over the classified family only: min(0.9, 0.65 + 0.1 n), n its indicating measures. An indicated family that is not a candidate does not raise it | the legacy n over every hot measure | the legacy rule counted measures of a family it then did not name. Changes a confidence (below). U-CT10 (confidence not calibrated) |
| D-CT13 | Source disagreement (telemetry.source_agreement deviating) is a limitation and contrary evidence; the family indicator stands | INCONCLUSIVE | the indicating row is observed by a collector; no approved rule makes disagreement on another row void it. U-CT3 |
| D-CT14 | Memory: classification_example records listed only when VALIDATED, as comparison, never proof; nothing changes a decision. Writes as `graph.validate_memory_update` (shared) | use precedents to break ties | no self-validation |
| D-CT16 | `assess_classification_questions` takes no place in the tool-call sequence (`tools.UNSEQUENCED`), as `assess_anomaly_questions` (D-AD9) | number it like the other baseline tools | the first suite run showed the replay of bench case `classification_tagging:abnormal` rejected by ID-02 and SC-01 because every id the recorded reply cites had moved by one; a numbering artefact, not a finding |
| D-CT17 | Revision 1 (U-CT4). `requirements.AVAILABLE_IF["taxonomy"]` reads `tools.taxonomy_problem`: the taxonomy counts only when `approved` is set and `version` is a non-empty string. A present but unapproved or unversioned taxonomy is MISSING_DEPENDENCY at the precheck, and the reason says why (`requirements.UNAVAILABLE_WHY`, taxonomy only). `t_query_taxonomy` uses the same check. The taxonomy carries no effective date or expiry, so none is checked | check approval in the package only (as before) | purvi asked for the shared gate. Only the taxonomy rule changed: every other artifact's availability and detail, and an absent taxonomy's detail, are as before. Before this, an approved taxonomy without a version passed the gate and went to the model (`llm_unavailable` without a provider) |
| D-CT18 | Revision 1 (U-CT3). For CT-1 to CT-3, the rows behind the indicating metrics are checked for `collectors_agree` false. When every one is disputed, the family has no independent support: the question is INCONCLUSIVE (code CONFLICTING_SOURCES), the family is listed in CT-5 `basis.disputed`, is not a candidate and is a limitation. One undisputed indicating row supports it: the family stands, with the disputed rows as contrary evidence. Disagreement on rows that indicate no family is unrelated: CT-5 `basis.disagreement` lists it as such and the family stands, as before. A row not cross-checked (`collectors_agree` absent) is not disputed | void the family on any disagreement; ignore it (as before) | purvi: unrelated disagreement must not remove a verified family; disputed classification evidence cannot establish one without independent support. With CT-1 unanswered and nothing else indicated, CT-5 is UNDETERMINED by the existing rule (U-CT5 approved) |
| D-CT19 | Revision 1 (U-CT6). `docs/cops/cops_topology.json` carries Classification & Tagging's runtime edges: cop_links policy_enforcement, interaction_discovery and resource_management to classification_tagging, and classification_tagging to incident_response (an approved TOPOLOGY_CONSUMES edge); `reads_decisions_from` lists the five producers in `specs.CONSUMES` order; the producers' `downstream` lists include it. Only these edges were changed | reconcile every COP's edges | purvi: only the necessary topology correction. The document's other differences from the runtime (below, U-CT11) are not Classification & Tagging's and are left for approval |
| D-CT15 | The CT-5 basis records observed, inferred and insufficient evidence apart (`evidence_status`); the model's interpretation under `basis.interpretation` with its verification status | one combined list | purvi's point 4 |

## Package defects found by the harness

| # | What | Fix |
|---|---|---|
| D1 | CT-1 to CT-3 detail text named the state but not the metric | `_ct_family` names each metric with its state |
| D2 | Policy Enforcement's finding was counted as independent, because its lineage holds tool calls rather than metric names | `_ct_upstream` also compares the producer's `metric_keys` with this COP's counted metrics |

## Labels that changed against the legacy rule

The legacy rule (`interpret.classification_tagging`) on the harness worlds, against this package
(`/mnt/project-files/pr4/verification/classification_tagging/LEGACY_COMPARISON.json`). Every other world keeps its
label and confidence.

| World | Legacy rule | Package | Decision |
|---|---|---|---|
| no_taxonomy | UNCLASSIFIED 0.7 | INCONCLUSIVE (MISSING_DEPENDENCY) | requirements gate, unchanged; the runtime already gated it |
| taxonomy_not_approved | UNCLASSIFIED 0.7 | INCONCLUSIVE | D-CT8 |
| overlap_without_model | BOUNDARY 0.85 | INCONCLUSIVE | D-CT5 |
| family_not_in_taxonomy | OVERSIGHT 0.75 | INCONCLUSIVE | D-CT6 |
| metadata_missing_quiet | UNCLASSIFIED 0.7 | INCONCLUSIVE | D-CT7 |
| overlap_one_family_undefined | INTEGRITY 0.85 | INTEGRITY 0.75 | D-CT12 |

Against the runtime before this package: every Classification & Tagging case went to the model, so without a
provider every reading was INCONCLUSIVE (`llm_unavailable`). One-family and no-family events now get their label
without a model; AMBIGUOUS events still need it. Evaluation case C08 still reads INTEGRITY through the model.

## Open for purvi

| # | Question | Current behaviour |
|---|---|---|
| U-CT1 | **Kept (A-CT1).** LOOPS, OUTCOME and COST are in the spec's vocabulary with no approved evidence rule. Which metrics, if any, indicate them? | never produced; concerns observed in CT-4, unmapped |
| U-CT2 | **Kept (A-CT1).** Should insights.no_oversight, oversight.fast_approvals, oversight.declined_inferred (OVERSIGHT) or access.first_seen_mcp (BOUNDARY) indicate a family? | observed, not counted |
| U-CT3 | **Resolved (A-CT1, D-CT18).** Should collector disagreement make a family indicator INCONCLUSIVE? | unrelated disagreement: the indicator stands, limitation says unrelated; disagreement on every indicating row: the question is INCONCLUSIVE and the family not a candidate |
| U-CT4 | **Resolved (A-CT1, D-CT17).** The requirements gate accepts an unapproved taxonomy artifact. Should the gate check approval? | the gate checks approval and version |
| U-CT5 | **Approved (A-CT1).** Partial metadata now reads INCONCLUSIVE where the legacy rule read UNCLASSIFIED (D-CT7). Keep? | INCONCLUSIVE |
| U-CT6 | **Resolved (A-CT1, D-CT19).** The three added upstream edges (D-CT9) are in `specs.TOPOLOGY_CONSUMES` but not in `cops_topology.json`. Add them there? | in `cops_topology.json` too; order acyclic |
| U-CT7 | **Kept (A-CT1).** The spec's four missing_metrics (actor_type, resource_type, incident_family, classification_confidence) are not registry metrics. Register them, or drop them from the spec? | NOT_CALCULATED with missing inputs |
| U-CT8 | **specs.py kept (A-CT1).** The workbook words the behavioural question "What kind of governance event has occurred, and how should it be categorized?"; specs.py "What kind of governance event is this, and which entities should remain associated with it?" Which is canonical? | specs wording, unchanged |
| U-CT9 | **Kept as diagnostic (A-CT1).** CT-4 reads insights.token_outlier, duration_outlier and action_rate_ratio self-inclusive, while Anomaly Detection and Resource Management read them leave-one-out. Adopt leave-one-out here? | self-inclusive; CT-4 is never counted, so no label depends on it |
| U-CT10 | **Kept, uncalibrated (A-CT1).** Confidence is the legacy formula, not calibrated against reviewed outcomes | min(0.9, 0.65 + 0.1 n), uncalibrated |
| U-CT11 | `cops_topology.json` differs from the runtime graph outside this COP: 16 `specs.CONSUMES` edges are not in its cop_links (for example runtime_monitoring to its nine consumers, interaction_discovery to agent_lifecycle_management and incident_response, incident_response to governance_intelligence and risk_mitigation), and it holds the two feedback edges (risk_mitigation to incident_response, workload_optimization to resource_management) as current-cycle links. Reconcile? | unchanged; the document is labelled target state |
| U-CT12 | The taxonomy has no effective date, expiry or supersession record, so version validity is a non-empty version only. Define taxonomy versioning? | non-empty version checked |

## Ground truth revision

| # | Date | What | Why |
|---|---|---|---|
| R-CT1 | 2026-11-27 | GROUND_TRUTH.yaml extended and committed (8babdef) before the revision-1 code: taxonomy_unapproved_with_indicator, taxonomy_version_missing, taxonomy_valid_gate_passes, unrelated_disagreement_verified, indicator_disputed, indicator_disputed_with_support, disputed_family_beside_another, topology_consistent. No earlier scenario changed | purvi's A-CT1 (U-CT3, U-CT4, U-CT6). First revised run: 221 of 221 |

## Readings that changed in revision 1

Every earlier harness scenario keeps its label, confidence and five answers. One keeps its label with a
different status:

| World | 6c42ca5 | Revision 1 | Why |
|---|---|---|---|
| taxonomy_not_approved | INCONCLUSIVE, status INCONCLUSIVE, CT-5 UNDETERMINED (the package stopped it) | INCONCLUSIVE, status MISSING_DEPENDENCY (the gate stops it; the model is not reached) | D-CT17. C-CT2 accepted either stop, so the check still holds |

The new worlds, run against the code at 6c42ca5:

| World | 6c42ca5 | Revision 1 |
|---|---|---|
| taxonomy_unapproved_with_indicator | INCONCLUSIVE (package) | INCONCLUSIVE, MISSING_DEPENDENCY |
| taxonomy_version_missing | went to the model; INCONCLUSIVE only because no provider (`llm_unavailable`) | INCONCLUSIVE, MISSING_DEPENDENCY, no model |
| indicator_disputed | BOUNDARY 0.75 | INCONCLUSIVE 0.0 |
| disputed_family_beside_another | AMBIGUOUS, went to the model (`llm_unavailable`) | INTEGRITY 0.75, no model |
| unrelated_disagreement_verified, indicator_disputed_with_support, topology_consistent | BOUNDARY 0.75 | BOUNDARY 0.75 |

## Harness corrections after the first run

First run 156 of 165. GROUND_TRUTH.yaml is unchanged; every correction is marked in `harness.py`.

| # | What | Why |
|---|---|---|
| C-CT1 | `action_none` reads `proposed_action["action"]` | the field is a dict; a check implementation error |
| C-CT2 | The taxonomy gate is satisfied by the precheck (MISSING_DEPENDENCY) or by package CT-5 UNDETERMINED with taxonomy NOT_SUPPLIED | an unapproved artifact passes `requirements.py`; the package stops it (D-CT8) |
| C-CT3 | A rejected model label may be rejected by the package read-back or by DS-03; the status may be INCONCLUSIVE or REJECTED_BY_VERIFIER; the shared UNVALIDATED candidate record is allowed | DS-03 already rejects a quiet label over an indicated family (shared behaviour) |
| C-CT4 | The metadata_missing_with_secret world's secret row carries no file_path | the scenario is a cli_wrapper session without file metadata; the world built it with one |
| C-CT5 | `_case` takes the session id, for the memory world's m0 case | the m0 case was built as s1 |

After D1, D2 and C-CT1 to C-CT5: 165 of 165.
