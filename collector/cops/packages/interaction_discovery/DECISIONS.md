# Interaction Discovery: decisions

## Made in this package (D2 and D3 accepted by purvi 2026-11-20; the rest proposed)

| # | Decision | Alternatives considered | Rationale |
|---|---|---|---|
| D1 | The five answers decide the label; the legacy rule `cops/interpret.py:_interaction_discovery` is no longer called by the runtime (it is unchanged, and the legacy runner still uses it) | bound the legacy label, as Runtime Monitoring does | The legacy rule reads five metrics; the questions read eighteen, including the graph metrics |
| D2 | NEW_ROUTE and PROPAGATING outrank PATH_PARTIAL; a partial graph stops only CONTAINED | the legacy order (PATH_PARTIAL first when lane linkage is EXCEPTION) | An incomplete graph cannot prove absence, but an observed route is still observed. PATH_PARTIAL is kept in `also_held` |
| D3 | Thin telemetry now gives PATH_PARTIAL from the questions, where the legacy CONTAINED was withheld as INCONCLUSIVE | keep INCONCLUSIVE | PATH_PARTIAL is this COP's label for "the network cannot be reconstructed". Bench case `interaction_discovery:missing` accepts both |
| D4 | Missing history makes the label INCONCLUSIVE, not PATH_PARTIAL; ID-3 is answered without history when the session has no interaction edge | PATH_PARTIAL; always require history | Novelty is undecided, the graph is not partial. With no edge there is no route that could be new |
| D5 | A route is a delegation or MCP edge, or a first-seen MCP server; a tool new to the operator is listed in ID-3 and does not make NEW_ROUTE | count new tools as routes | Same scope as the legacy rule (it promoted only `mcp:` routes) |
| D6 | Absent `agent_id` impairs ID-5 only when rows show delegation (a subagent event, `is_multi_agent`, or `delegation_depth` > 0) and no subagent lane exists | treat any absent `agent_id` as impairment | Main-agent rows carry no `agent_id` in this data, so the rule would flag every single-agent session |
| D7 | `interaction.mcp_servers`, `access.first_seen_mcp`, `insights.outside`, `path.transition_novelty` are optional | required | Each has a basis that is often absent (no MCP-capable collector, no paths); the answer says what could not be observed instead of becoming INCONCLUSIVE |
| D8 | CONFLICTING_ACTION_SOURCES blocks ID-2; provenance does not block any ID question | block on provenance too | Disagreeing collectors on an action mean the path is not known; provenance is Runtime Monitoring's question and is unavailable on real data |
| D9 | `route_record` payloads carry `routes`; Resource Management reads ID-2 | none | Without routes the memory read was empty; without ID-2 a NEW_ROUTE on a partial graph would have been used for attribution |
| D10 | A depth conflict (reported `delegation_depth` against the linked structure) impairs ID-5 | record only | Two records of the same delegation disagree |

## Needs your decision

| # | Question | Options |
|---|---|---|
| U2 | `interaction_unattributed_edges`: the registry says its definition is unconfirmed | define it; drop it from the mapping |
| U3 | Approved thresholds for depth and breadth | leave reported only; supply values |
| U4 | Should a CONTAINED session write a route record, so routes become known from memory as well as history? | no (current); yes |

## Not done, by instruction

No Supabase replay, collector change, dashboard work or self-evolution. No model call.
