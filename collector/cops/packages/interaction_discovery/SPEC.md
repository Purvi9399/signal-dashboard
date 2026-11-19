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
  evidence refs), `drivers`, `also_held`, `contrary`, `edges` and `new_routes`; and the common decision record
  (`cops/runtime/decision.py`).

## The five questions

Source: the workbook, sheet COMPLETE_DELEGATION_DATA, rows 43-48. Fields are the workbook's, as column names.

### ID-1 Which agents, subagents, tools and MCP servers are interacting during execution?

`interaction_unique_entities` and `interaction.subagent_lanes` (required); `interaction.mcp_servers` (optional:
unknown when no collector able to see MCP ran). ENTITIES_PARTIALLY_IDENTIFIED when MCP servers could not be
observed; otherwise ENTITIES_IDENTIFIED. The basis names the subagent lanes, tools and servers.

### ID-2 Can we reconstruct the relationships and communication paths between these entities?

`interaction_edge_count` (required); `interaction_graph_completeness`, `interaction_orphaned_events`,
`telemetry.lane_linkage`, `telemetry.turn_linkage` (when applicable). Blocking check: CONFLICTING_ACTION_SOURCES.
NOT_RECONSTRUCTABLE when subagent rows exist and none has a resolvable parent; PARTIALLY_RECONSTRUCTED when any
ratio is below 1 or any subagent event is orphaned; otherwise FULLY_RECONSTRUCTED.

### ID-3 Have new or previously unseen interaction routes appeared?

`interaction_route_novelty` (when applicable; needs 5 earlier sessions of the operator and tool) and
`interaction_edge_count` (required); `access.first_seen_mcp` and `path.transition_novelty` (optional). A route
is a delegation or MCP edge (`signal_metrics.interaction_edges`) or a first-seen MCP server. Routes a validated
route record establishes are known. With no interaction edge the question is answered without history: no
route exists to be new. Tools new to the operator are listed, and do not make a route (D5).

### ID-4 How deeply and widely is activity propagating across agents, tools and services?

`interaction_propagation_depth`, `interaction.subagent_lanes` (required); `interaction_propagation_breadth`
(when applicable); `interaction.mcp_servers`, `insights.outside` (optional). BEYOND_APPROVED_BOUNDS when a judged
measure is EXCEPTION or CRITICAL against its approved threshold; depth and breadth have no approved threshold and
are reported, not judged. A reported `delegation_depth` that disagrees with the linked structure is recorded.

### ID-5 Are missing agent identities, lane links or interaction records preventing reliable reconstruction?

`telemetry.collector_coverage`, `telemetry.unknown_share` (required); `telemetry.source_agreement`,
`interaction_orphaned_events` (when applicable); `interaction_unattributed_edges` (optional, not built). Depends
on ID-1, ID-2, ID-4. RECONSTRUCTION_IMPAIRED when any cause holds: an expected collector missing, unknown share
or source agreement not NORMAL, orphaned subagent events, delegation observed with no subagent identity, an
earlier graph question partial or unanswered, a depth conflict, or a verified upstream Runtime Monitoring that is
not SUFFICIENT or whose RM-3 is PARTIALLY_LINKED or UNLINKED.
