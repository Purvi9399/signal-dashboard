# Policy Enforcement: decisions

All proposed on 2026-11-20 and awaiting purvi's review. Nothing here changes a label name, the behavioural question,
a threshold, the policy evaluator (`tools.evaluate_operation`) or shared architecture.

## Made in this package

| # | Decision | Alternatives considered | Rationale |
|---|---|---|---|
| D-PE1 | With no approved policy version in force, PE-1, PE-2, PE-3 and PE-5 are INCONCLUSIVE (MISSING_REFERENCE policy_store); PE-4 is INCONCLUSIVE when there is a route to evaluate. Observed conditions are kept as policy-relevant; no violation is claimed. Status MISSING_DEPENDENCY | keep the condition reading of `interpret._policy_enforcement` (it gave ALLOWED with no rule evaluated, or a candidate VIOLATION that VS-01 rejected); UNCERTAIN | purvi 09:40Z: "With no policy source, invent nothing and claim no verified violation; preserve INCONCLUSIVE". The final label in these worlds was already INCONCLUSIVE through VS-01 or the requirements gate; it is now the COP's own answer |
| D-PE2 | Policy relevance is a metric in EXCEPTION or CRITICAL (`relevance_states`), the states the legacy interpreter acted on. It is an observation, never a decision | WARNING too; any non-zero count | no new cut-off; the thresholds are metrics_config.json's |
| D-PE3 | PE-4 evaluates Interaction Discovery's verified ID-3 routes (or, without that finding, `access.first_seen_mcp`). An MCP route is the operation (mcp, server) plus entitlement. A NEW_ROUTE finding that names no route makes PE-4 INCONCLUSIVE | treat NEW_ROUTE as a violation; ignore the label | purvi 09:40Z: a NEW_ROUTE triggers evaluation, never an automatic violation |
| D-PE4 | A delegation route is NOT_EVALUATED: the policy model has no delegation operation. It leaves PE-4 NEW_ROUTES_UNDECIDED, so UNCERTAIN, with a MISSING_EVIDENCE limitation NO_POLICY_OPERATION | evaluate it as action `*`; ignore delegation | inventing an operation name would invent policy semantics; "no rule is not permission" is the existing meaning |
| D-PE5 | Approval is read at session level, as `t_evaluate_session_operations` does (any permission_decision allow or approved). APPROVAL_OBTAINED carries a limitation SESSION_LEVEL_APPROVAL with `approval_action_linkage` | require linkage of the grant to the operation | keeps the existing meaning; linkage is measured and shown so purvi can decide |
| D-PE6 | A denied attempt that a hook stopped (`policy_rule_matched`) did not execute; it is kept in PE-2 `attempts_stopped` and is not a violation | a separate outcome; VIOLATION | the existing rule's meaning (`blocked_by_hook`), not changed silently |
| D-PE7 | Conflicting rules resolve by the existing precedence (deny beats require_approval beats allow). The conflict is listed in PE-1 and the contrary evidence, and Knowledge & Policy Management is notified | UNCERTAIN when no precedence is declared in the store | `evaluate_operation` is the approved evaluator; whether a conflict without declared precedence should stay undecided is purvi's call |
| D-PE8 | An MCP server used with no entitlement record, where the policy allows it, leaves PE-1, PE-2 and PE-4 INCONCLUSIVE (MISSING_REFERENCE entitlements) | ALLOWED on policy alone | `requirements.py` already makes entitlements required for ALLOWED when an MCP server is used |
| D-PE9 | PE-4 is INCONCLUSIVE when MCP traffic has no basis (`access.first_seen_mcp` or `interaction.mcp_servers` UNKNOWN) and no route is known | NO_NEW_ROUTES | a new MCP route cannot be ruled out without the basis (as D18 for Interaction Discovery) |
| D-PE10 | PE-5 is INCONCLUSIVE when a dependency is, or Runtime Monitoring verified the telemetry INSUFFICIENT. A verified deny still stands | INCONCLUSIVE overrides everything | positive evidence stands on partial evidence, as in Interaction Discovery (D2) |
| D-PE11 | Validated enforcement_record precedents for the same operations are listed (SAME_POLICY or SUPERSEDED) and never change the decision | use precedent to decide | memory is not a policy source; no self-validation |
| D-PE12 | Upstream findings are cited as claims only where they changed an answer (Interaction Discovery when PE-4 evaluated its routes; Runtime Monitoring when INSUFFICIENT) | cite every verified upstream | citing an unused finding added its lineage to this COP's and made Risk Mitigation count this COP as correlated (case C05 went LOW). Seen in the first suite run and fixed |
| D-PE13 | Attention metric states are claims only when no policy was evaluated. With a policy, the claims are the evaluation's | always cite them | the decision rests on the policy, not the metric; the same reason as D-PE12, and the earlier rule's claims |

## Ground truth corrected after the first run

- proposed_operation_denied, PE-1: expected POLICY_APPLIES, is POLICY_PARTIALLY_APPLIES. `sensitive_policy` allows
  writes under /ws/* only, and the session's Edit carries no file_path. A misreading of the world.
- mcp_not_entitled, label: expected VIOLATION, is INCONCLUSIVE. The five answers are as expected and the COP
  proposes VIOLATION; VS-01 rejects it (insufficient support: the policy allows every operation). The same world
  gave the same rejection at 946cfab. See U-PE1.
- incomplete_telemetry, PE-2: corrected before the first run, after reading the world's metric values.

## Earlier tests restated (behaviour changed by design, D-PE1)

- `tests/test_runtime_graph.py::test_policy_enforcement_paths`: with no policy, the COP no longer produces a
  candidate VIOLATION for VS-01 to reject. The test now checks PE-2 INCONCLUSIVE, no violation, and
  insights.secrets kept as policy-relevant.
- `tests/test_runtime_graph.py::test_policy_enforcement_never_turns_a_new_route_into_a_violation`: with a NEW_ROUTE
  and no policy the label is INCONCLUSIVE, not UNCERTAIN. Still no violation and no intervention.
- `tests/test_runtime_error_paths.py::test_deterministic_quiet_label_is_withheld_without_required_artifact`: for
  Policy Enforcement the quiet label is withheld by PE-5 itself, so there is no `gated_label`.
- Interaction Discovery harness, `policy_reads_outside` (its D19): reads PE-2 instead of the VS-01 text.

## For purvi's decision

| # | Question |
|---|---|
| U-PE1 | Should VS-01 accept an entitlement-only violation (MCP server used without entitlement while the policy allows it)? Today it rejects it as insufficient support |
| U-PE2 | D-PE5: should APPROVAL_OBTAINED require the grant to be linked to the operation (`approval_action_linkage`)? |
| U-PE3 | D-PE6: should a denied attempt stopped by a hook be its own outcome, or a violation? |
| U-PE4 | D-PE7: should conflicting rules with no declared precedence stay UNCERTAIN instead of resolving to deny? |
| U-PE5 | D-PE4: should delegation routes get a policy operation, and who defines it? |
| U-PE6 | `telemetry.unknown_shared`: confirm it means `telemetry.unknown_share` (then PE-5 reads it) |
| U-PE7 | References for the 8 NOT_CALCULATED metrics (policy_registry, entitlements, policy_exceptions, allowlists) and a definition of policy_evidence_sufficiency |

## Not done, by instruction

No Supabase replay, collector change, dashboard work or self-evolution. No model call. No change to the policy
evaluator, VS-01 or the requirements gate.
