# Policy Enforcement: interpretation

## Pipeline

1. `signal_metrics.compute(..., catalogue=True)` computes every metric (no change to the engine).
2. `evidence_assessment.assess` gives each question its presence, reliability and sufficiency.
3. `tools.t_evaluate_session_operations` and `tools.t_check_entitlements` evaluate the session's observed and
   proposed operations against the approved version in force and the entitlement record (unchanged).
4. `cop_package.observe_policy_enforcement` answers each SUFFICIENT question from those evaluations, the metric
   values and states, memory and the upstream findings. Any other question is INCONCLUSIVE with its reasons.
5. `cop_package.decide_policy_enforcement` turns the answers into the label, action, severity, recipients,
   drivers and contrary evidence.
6. `rules.policy_enforcement` records one claim per answer, the evaluation's claims (denied, proposed denied,
   approval needed, no rule, not entitled, allowed), attention metric states, upstream findings and precedents.

Every step is deterministic. No model is called.

## How the label is decided

PE-5's answer gives the label (`labels` in METRICS.yaml). When PE-5 itself cannot be answered, the same order is
applied to PE-1 to PE-4, so a verified deny still stands.

| Order | Label | When (PE-5) | Action | Severity |
|---|---|---|---|---|
| 1 | VIOLATION | DENY: PE-2 OUTSIDE_AUTHORIZED_BOUNDARIES or PE-4 NEW_ROUTES_NOT_PERMITTED | ALERT; BLOCK when only a proposed operation is denied | HIGH |
| 2 | APPROVAL_REQUIRED | REQUIRE_APPROVAL: PE-3 APPROVAL_MISSING | REQUEST_APPROVAL | MEDIUM |
| 3 | UNCERTAIN | UNDECIDED: PE-1 POLICY_PARTIALLY_APPLIES, PE-2 BOUNDARY_NOT_DEFINED or PE-4 NEW_ROUTES_UNDECIDED | WATCH | LOW |
| 4 | INCONCLUSIVE | a question needed for the decision is INCONCLUSIVE, or telemetry is verified INSUFFICIENT | WATCH | NONE |
| 5 | ALLOWED | every question found the operations permitted | NONE | NONE |

Labels, actions, confidences (0.85, or 0.4 for UNCERTAIN) and the order are those of the existing evaluated-policy
path in `rules.policy_enforcement`. Every lower decision that also holds is kept in `also_held` and the contrary
evidence, and all five answers stay in the decision record (scenario several_findings).

Recipients: VIOLATION and APPROVAL_REQUIRED notify Incident Response and Audit & Traceability, with Risk Mitigation
when `insights.secrets` is policy-relevant; UNCERTAIN notifies Audit & Traceability; a rule conflict or an
operation with no rule also notifies Knowledge & Policy Management.

## What changed from the existing rule

- With no approved policy version, the condition reading of `cops/interpret.py:_policy_enforcement` is no longer
  the decision. It could give ALLOWED with no rule evaluated, or a candidate VIOLATION that VS-01 then rejected.
  Now every policy question is INCONCLUSIVE (policy_store missing), the observed conditions are kept as
  policy-relevant, and no violation is claimed (D-PE1).
- A new route from Interaction Discovery is evaluated route by route in PE-4 (D-PE3).
- With an approved policy, the decisions are those of the existing rule, decomposed into the questions.

## Verification boundary

Graded verification (VS-01, `support.py`) still checks the candidate. An entitlement-only violation, where every
operation is allowed by the policy, is rejected as insufficient support (scenario mcp_not_entitled). That was
already so before this package and is listed for decision (D-PE7).
