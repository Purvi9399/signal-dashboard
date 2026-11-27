---
name: cops-incident-response
description: Maintain or extend the Incident Response COP package (collector/cops/packages/incident_response) in signal-dashboard; use when changing its questions, metrics, grounds, correlation, severity, state, intervention, authority, the model read-back, upstream reads, incident_outcome memory, Risk Mitigation feedback, harness or ground truth.
---

# Incident Response COP package

These are maintenance instructions for Claude Code. They are not the runtime's source of truth. The runtime reads
`collector/cops/packages/incident_response/METRICS.yaml`, validated by `cop_package.validate`.

This is COP 9 in the build order. The workbook and `docs/cops/cop_catalogue.json` number it 6. Never infer build
order from that numbering.

## Where things are

- **Contract:** `METRICS.yaml` holds:
  - the five questions IR-1 to IR-5 (workbook COMPLETE_DELEGATION_DATA rows 94 to 102, metric list row 104);
  - the 34-metric mapping (14 NOT_CALCULATED, with notes) and `not_calculated` (the 3 spec missing_metrics);
  - `upstream` (incident, finding, abnormal, context and quiet labels per consumed COP);
  - `labels`, `responses`, `severity`, `model_role`, `deviation_states` and `operations` (only
    `containment_plan` is by llm).
- **Explanations:** `SPEC.md`, `INTERPRETATION.md`, `MEMORY.md`, `CONNECTIONS.md` and `DECISIONS.md`, in the same
  folder.
- **Ground truth:** `GROUND_TRUTH.yaml` (30 scenarios). The harness is `harness.py`.
- **Answers and label:** `collector/cops/runtime/cop_package.py`, Incident Response section: `_ir_upstream`,
  `_ir_grounds`, `_ir1` to `_ir5`, `observe_incident_response`, `assess_incident_response`,
  `decide_incident_response`, `_ir_limitations`, `_ir_status`, and `interpreted_incident_response` (the model
  read-back, registered in `graph.INTERPRETED`).
- **Rule:** `rules.incident_response` computes the legacy reading (the computational baseline,
  `cop_specific.legacy_reading`), then `_ir_questions` applies the package's decision. The package decides every
  reading except an intervention incident.
- **Tools:** `check_exposure` and `assess_incident_questions` (in `tools.UNSEQUENCED`).
- **Tests:** `tests/test_cop_package_incident_response.py`. Evaluation cases C05, C06, C10a/b, C15, D-IR and E2E.
  Bench cases `incident_response:*`.

## Rules

1. Change METRICS.yaml first, then the code. `cop_package.validate("incident_response")` must return `[]`.
2. **Grounds.** The grounds are the legacy rule's and nothing else:
   - Incident grounds: own exposure (insights.secrets or insights.sensitive_unasked in EXCEPTION or CRITICAL on
     undisputed rows), a verified PE VIOLATION, a verified RR FAILED_RECOVERY or IRREVERSIBLE.
   - Finding grounds: a verified AD ANOMALOUS, insights.blocked in a deviation state, a verified PE
     APPROVAL_REQUIRED, a verified RR PARTIAL_RECOVERY.
   - Everything else consumed is context, quiet or evidence quality. Never ground on a route, a consumption reading
     or a taxonomy family.
3. **Correlation.** Correlate by source items (`rk_source_items`; tool-call ids never count). Take grounds in a
   fixed order: own exposure, then the deny rule, then producers in `specs.topological_order`. A ground whose
   items are all already carried is correlated: listed, never a second incident. A case has one incident.
4. **Severity and response** are the rule's, as written in DECISIONS D-IR4 and D-IR5. REQUEST_APPROVAL wins over
   ESCALATE, and ESCALATE wins over ALERT. A pending block is read from the producer's own proposal (published
   `proposed_action.requested_action`).
5. **Authority.** Never propose PAUSE, BLOCK or INSTRUCT. A block is requested with REQUEST_APPROVAL and runs only
   after a person approves it. `incident_status.intervention.executed` stays empty.
6. **Evidence.** Insufficient evidence is its own reading (IR-1 INCONCLUSIVE, D-IR7). Partial evidence lowers
   confidence and never erases a ground. Disputed rows alone establish nothing. IR-3 is never RESOLVED until
   resolution records exist.
7. **The model** is called only for INCIDENT with IR-4 INTERVENTION_JUSTIFIED. It scopes the incident and plans
   containment. It never sets the label, severity or response. The incident and the plan are separate (D-IR21):
   a verified deterministic incident is published whatever the model does, and the plan reads VERIFIED,
   UNVERIFIED, REJECTED or UNAVAILABLE. Only the package's own evidence failing verification gives INCONCLUSIVE.
   Keep the rejected model output and reason. Tests and the harness use scripted replies (simulated) only.
8. **Memory and feedback.** Memory precedents and Risk Mitigation's previous-cycle feedback never change an answer.
   Risk Mitigation's current-cycle finding is never an input (no circular reasoning).
9. Never edit GROUND_TRUTH.yaml to match an output. Mark a correction made after a run in the harness and in
   DECISIONS.md, with why. A change that moves another COP's harness is marked in that harness too.
10. Do not invent metrics. An unbuilt metric stays NOT_CALCULATED with its missing input. Do not change VS-01, the
    requirements gate, triage, the verifiers, authority, `specs` or `cops/interpret.py` from this package; record
    the need in DECISIONS.md.
11. Keep `docs/cops/cops_topology.json` in step with this COP's edges in `specs.CONSUMES`, `specs.consumers` and
    `specs.FEEDBACK`.

## Checks before you report

```
cd collector
for p in incident_response risk_mitigation classification_tagging resource_management anomaly_detection \
         policy_enforcement interaction_discovery audit_traceability runtime_monitoring; do
  python3 -m cops.packages.$p.harness; done
python3 -m pytest -q ../tests
python3 -m cops.runtime.evaluation
python3 -m cops.runtime.test_bench && git checkout ../docs/cops/evaluation/test_bench_results.json
```

Run the suite and the bench one after the other, never together. Report the actual pass and fail counts, any label
that moved, and what needs the owner's decision.
