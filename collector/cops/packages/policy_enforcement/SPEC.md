# Policy Enforcement

METRICS.yaml is the contract the runtime reads. This file explains it.

## Identity

| | |
|---|---|
| Key | `policy_enforcement` |
| Classification | deterministic (one of the eight) |
| Behavioural question | Does the observed behaviour violate an explicit permission, policy, entitlement, boundary, or approval requirement? (`specs.py`) |
| Labels | ALLOWED, VIOLATION, APPROVAL_REQUIRED, UNCERTAIN, or INCONCLUSIVE (unchanged) |
| Permitted actions | NONE, WATCH, ALERT, REQUEST_APPROVAL, BLOCK, PASS_TO_COP |
| Consumes | Runtime Monitoring, Interaction Discovery, Agent Lifecycle Management, Anomaly Detection (`specs.CONSUMES`); this package reads the first two |
| Consumed by | Incident Response, Risk Mitigation, Audit & Traceability, Knowledge & Policy Management |

## Purpose

Decide whether what the agent did, or proposes to do, is permitted under the approved policy in force and the
entitlement record. Observation and interpretation are kept apart in every answer:

| Layer | What it is | Where it is kept |
|---|---|---|
| Observed behaviour | metric values and states, and the session's operations | `basis.observed`, PE-1 `operations` |
| Potential policy relevance | an observation in a state the legacy interpreter acted on (EXCEPTION, CRITICAL) | `basis.policy_relevant` |
| Actual applicability | which rules of the approved version in force match each operation, and whether entitlements apply | PE-1 |
| Verified violation | an executed or proposed operation denied by that version, or an MCP server used without entitlement | PE-2 `violations`, PE-4 `routes` |
| Insufficient evidence | no approved version, no entitlement record where one applies, a basis missing | INCONCLUSIVE with `because` |

## Policy sources

Only the approved version in force (`tools.latest_policy`: the newest approved version of `policy_store`) and the
`entitlements` artifact. Rules are evaluated by `tools.evaluate_operation` (deny beats require_approval beats
allow; no rule is not permission), unchanged. Memory, upstream findings and metric states are never policy. With
no approved version, nothing is evaluated, no violation is claimed, and the decision is INCONCLUSIVE.

## Authority

It proposes ALERT, REQUEST_APPROVAL or BLOCK. Triage and the authority check decide what is recorded or executed
(`triage.py`, `graph.authority_check`, PE-01, AU-02). In dry run nothing executes.

## Inputs and outputs

- Rows: the case session's observables. Metrics: `signal_metrics.compute(..., catalogue=True)`.
- Tools, in order (`rules.DETERMINISTIC_TOOLS`): `evaluate_session_operations`, `check_entitlements`,
  `assess_policy_questions` (the five answers, read against the policy in force, entitlements,
  `enforcement_record` and `route_record` memory, and upstream Runtime Monitoring and Interaction Discovery).
- Output: the finding, with `cop_specific.questions`, `drivers`, `also_held`, `contrary`, `limitations`,
  `recipients`, and the fields earlier consumers read (`policy_version`, `denied`, `proposed`,
  `approval_needed`, `blocked_by_hook`); and the common decision record (`decision.py`).

## The five questions

Workbook COMPLETE_DELEGATION_DATA rows 119-127; metric list row 129.

### PE-1 Which policies, permissions and entitlements apply to the agent's intended action?

`policy_applicability_coverage`, `entitlement_mismatch_rate`, `policy_exception_rate` (optional, NOT_CALCULATED).
Read from the evaluation of every observed and proposed operation.

- POLICY_APPLIES: every operation matched a rule of the version in force.
- POLICY_PARTIALLY_APPLIES: some operation matched no rule (listed in `no_rule`).
- INCONCLUSIVE: no approved version (MISSING_REFERENCE policy_store), or an MCP server was used and no
  entitlement record exists (MISSING_REFERENCE entitlements).
- `conflicts` lists operations matched by rules with different effects and how they resolved.
- `policy.not_in_force` lists the versions not used and why (not approved, or superseded).

### PE-2 Is the agent attempting to access a resource or perform an operation outside its authorized boundaries?

`insights.outside`, `boundary_violation_count`, `unauthorized_action_attempts` (required); `insights.blocked`,
`insights.secrets`, `restricted_resource_access_rate`, `policy_violation_rate` (optional).

- OUTSIDE_AUTHORIZED_BOUNDARIES: an executed or proposed operation is denied, or an MCP server is used without
  entitlement (`violations`, each with its stage and rules).
- BOUNDARY_NOT_DEFINED: no deny, but some operation matched no rule.
- WITHIN_AUTHORIZED_BOUNDARIES: every operation is allowed or needs approval. A policy-relevant observation that
  the policy allows is kept in `permitted_relevant` (an edit outside the workspace allowed by a rule).
- A denied attempt a hook stopped (`policy_rule_matched`) did not execute and is kept in `attempts_stopped`, not
  counted as a violation (the existing rule's meaning, D-PE6).

### PE-3 Has the agent obtained the required approval before performing a sensitive or restricted action?

`insights.sensitive_unasked` (required); `approval_action_linkage`, `oversight.declined_inferred` (when
applicable); `oversight.fast_approvals`, `insights.no_oversight`, `approval_requirement_compliance` (optional).

- NO_APPROVAL_REQUIRED, APPROVAL_OBTAINED, APPROVAL_MISSING, from the operations the policy marks
  require_approval and the session's recorded approval.
- Approval is session-level, as `t_evaluate_session_operations` records it; APPROVAL_OBTAINED carries a
  limitation with `approval_action_linkage` (D-PE5).

### PE-4 Are newly discovered tools, MCP servers or interaction routes permitted under existing policies?

`access.first_seen_mcp`, `interaction.mcp_servers`, `interaction.subagent_lanes`, `path.transition_novelty`
(optional); `unapproved_tool_usage`, `unapproved_mcp_access`, `unauthorized_interaction_route_count` (optional,
NOT_CALCULATED).

- Routes come from Interaction Discovery's verified ID-3 answer, or, without it, from `access.first_seen_mcp`.
- A new route triggers an evaluation, never a violation. An MCP route is evaluated as the operation
  (mcp, server) and against entitlements. A delegation route has no operation in the policy model and is
  NOT_EVALUATED.
- NEW_ROUTES_NOT_PERMITTED (denied or not entitled) > NEW_ROUTES_UNDECIDED (no rule, or not evaluable) >
  INCONCLUSIVE (entitlement unknown) > NEW_ROUTES_PERMITTED. NO_NEW_ROUTES needs no policy.
- If MCP traffic has no basis and no route is known, INCONCLUSIVE: a new MCP route cannot be ruled out.

### PE-5 Should the action be allowed, denied, require approval or remain undecided because essential policy evidence is missing?

`telemetry.collector_coverage` (required); `telemetry.source_agreement`, `telemetry.turn_linkage`,
`telemetry.lane_linkage` (when applicable); `insights.unattributed`, `policy_evidence_sufficiency` (optional).
Depends on PE-1 to PE-4.

- DENY > REQUIRE_APPROVAL > UNDECIDED > INCONCLUSIVE > ALLOW over the answers (`held`).
- INCONCLUSIVE when a dependency is, or Runtime Monitoring verified the telemetry INSUFFICIENT.
- Validated `enforcement_record` precedents for the same operations are listed with SAME_POLICY or SUPERSEDED.
  They never change the decision.

## Metrics

31 registry metrics; all are mapped. 21 are built and read. 8 are NOT_CALCULATED (missing references
policy_registry, entitlements, policy_exceptions, allowlists, or an approved definition for
policy_evidence_sufficiency). 2 are not read: `telemetry.unknown_shared` is a suspected typo awaiting confirmation,
and `policy_decision_confidence` is a decision field. See METRICS.yaml.
