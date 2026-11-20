# Interaction Discovery

METRICS.yaml is the contract the runtime reads. This file explains it.

## Identity

| | |
|---|---|
| Key | `interaction_discovery` |
| Classification | deterministic (one of the eight) |
| Behavioural question | Who or what is interacting with whom, through what route, and how is the activity propagating? (`specs.py`) |
| Labels | CONTAINED, PROPAGATING, NEW_ROUTE, PATH_PARTIAL, or INCONCLUSIVE |
| Permitted actions | NONE, WATCH, PASS_TO_COP |
| Consumes | Runtime Monitoring |
| Consumed by | Agent Lifecycle Management, Policy Enforcement, Risk Mitigation, Audit & Traceability, Resource Management (`specs.CONSUMES`) |

## Purpose

Reconstruct which agents, subagents, tools and MCP servers interacted in a session, by which routes, how far
activity spread, and whether gaps in the telemetry stop that reconstruction. Structure only: whether a route is
permitted is Policy Enforcement's question, so a new route is handed there.

## Authority

It observes and passes findings on. It cannot block a route or change a policy. Every action it proposes goes
through triage and the authority check (`triage.py`, `graph.authority_check`); none executes.

## Inputs and outputs

- Rows: the case session's observables. Metrics: `signal_metrics.compute(..., catalogue=True)`.
- Tools, in order (`rules.DETERMINISTIC_TOOLS`): `build_interaction_graph`, `compare_known_routes`,
  `assess_interaction_questions` (the five answers, read against validated route memory and upstream Runtime
  Monitoring).
- Output: the finding, with `cop_specific.questions` (each question's answer, explanation, metrics, basis and
  evidence refs), `drivers`, `also_held`, `contrary`, `limitations`, `edges` and `new_routes`; and the common
  decision record (`cops/runtime/decision.py`), where `limitations` appear as structured entries in
  `uncertainty.limitations` (D14), and as text in `uncertainty.cop_limitations`.

## The five questions

Source: the workbook, sheet COMPLETE_DELEGATION_DATA, rows 43-48. Fields are the workbook's, as column names.

### ID-1 Which agents, subagents, tools and MCP servers are interacting during execution?

`interaction_unique_entities` and `interaction.subagent_lanes` (required); `interaction.mcp_servers` (optional:
unknown when no collector able to see MCP ran). ENTITIES_PARTIALLY_IDENTIFIED when MCP servers could not be
observed; otherwise ENTITIES_IDENTIFIED. The basis names the subagent lanes, tools and servers, the tools each
lane called (`tools_by_lane`), how identity is established (a subagent lane by `agent_id`; the main agent carries
none) and the provenance check outcomes.

### ID-2 Can we reconstruct the relationships and communication paths between these entities?

`interaction_edge_count` (required); `interaction_graph_completeness`, `interaction_orphaned_events`,
`telemetry.lane_linkage`, `telemetry.turn_linkage` (when applicable). Blocking check: CONFLICTING_ACTION_SOURCES.
NOT_RECONSTRUCTABLE when subagent rows exist and none has a resolvable parent; PARTIALLY_RECONSTRUCTED when any
ratio is below 1, when any subagent event is orphaned, when a delegation link rests on timing alone (no recorded
`parent_agent_id`) or was joined to a handoff recorded after the lane began, or when the timestamp order check
fails. Otherwise it is FULLY_RECONSTRUCTED.

Each link carries its basis (`cop_package.interaction_links`): RECORDED when the lane's rows name
`parent_agent_id`, or for an MCP link from `mcp_server`; INFERRED when only `signal_traces.link_lanes` joined the
lane to a handoff by time; INFERRED_AGAINST_TIME_ORDER when that handoff was recorded after the lane's first step
(`link_lanes` falls back to the first handoff); UNLINKED otherwise. An inferred link is not a verified one: the
handoff is an observed event, and the parent-child relationship is inferred. It is listed as a limitation in the
decision record and makes ID-2 partial (U5, decided 2026-11-20).
INCOMPARABLE_TIMESTAMPS and TIMESTAMP_ORDER are read with the outcome they have; when no row carries the three
timestamps they are EVIDENCE_MISSING, which is a limitation and not a gap (D13).

### ID-3 Have new or previously unseen interaction routes appeared?

`interaction_route_novelty` (when applicable; needs 5 earlier sessions of the operator and tool) and
`interaction_edge_count` (required); `access.first_seen_mcp` and `path.transition_novelty` (optional). A route
is a delegation or MCP edge (`signal_metrics.interaction_edges`) or a first-seen MCP server. Routes a validated
route record establishes are known. With no interaction edge the question is answered without history: no
route exists to be new. Tools new to the operator are listed, and do not make a route (D5). The basis reports
cross-session continuity: what the session is compared with, whether an operator was recorded and how many
earlier sessions were found. A session with no operator has no history, so ID-3 is INCONCLUSIVE when it has
routes.

### ID-4 How deeply and widely is activity propagating across agents, tools and services?

`interaction_propagation_depth`, `interaction.subagent_lanes` (required); `interaction_propagation_breadth`
(when applicable); `interaction.mcp_servers`, `insights.outside` (optional).

- BEYOND_APPROVED_BOUNDS when a propagation measure (`propagation_measures`: `interaction.subagent_lanes`,
  `interaction.mcp_servers`) is EXCEPTION or CRITICAL against its approved threshold.
- WITHIN_APPROVED_BOUNDS only when both propagation measures were evaluated (PRESENT with a threshold state) and
  neither is beyond its threshold (D18). The answer covers those two limits only: `evaluated_limits` names them.
- A propagation measure that could not be evaluated, such as `interaction.mcp_servers` with no MCP-capable
  collector, leaves ID-4 INCONCLUSIVE with reason NOT_EVALUATED, unless the other measure is already beyond.
- Depth and breadth have no approved threshold. They are measured and listed in `not_evaluated` with status
  NOT_EVALUATED, never judged, and recorded as LIMIT_NOT_APPROVED limitations with reason code NOT_EVALUATED.
- `insights.outside` counts file work outside the workspace. That is a workspace boundary, not propagation, so it
  is kept in `boundary_observations` with its value, state and evidence refs, and is not judged here (D17).
  Policy Enforcement reads the same metric itself. A reported `delegation_depth` that disagrees with the linked structure is recorded.

### ID-5 Are missing agent identities, lane links or interaction records preventing reliable reconstruction?

`telemetry.collector_coverage`, `telemetry.unknown_share` (required); `telemetry.source_agreement`,
`interaction_orphaned_events` (when applicable); `interaction_unattributed_edges` (optional, NOT_CALCULATED:
the missing input is an approved definition, U2). Depends
on ID-1, ID-2, ID-4. RECONSTRUCTION_IMPAIRED when any cause holds: an expected collector missing, unknown share
or source agreement not NORMAL, orphaned subagent events, delegation observed with no subagent identity, an
earlier graph question partial or unanswered, a depth conflict, or a verified upstream Runtime Monitoring that is
not SUFFICIENT or whose RM-3 is PARTIALLY_LINKED or UNLINKED.
