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

## Made on 2026-11-20, reviewed by purvi at 08:52Z

purvi asked that D11 to D16 be kept, with their rationale and alternatives recorded. Those that need her explicit
architectural choice are marked, and none of the six is treated as approved by default.

| # | Decision | Alternatives considered | Rationale | Status |
|---|---|---|---|---|
| D11 | Every delegation and MCP link carries its basis: RECORDED, INFERRED, INFERRED_AGAINST_TIME_ORDER or UNLINKED (`cop_package.interaction_links`). An inferred link also names the handoff it was joined to and the agent that handoff names | report only the ratios | A relationship that can be inferred is not a verified one. The basis says which it is, and the handoff stays visible as an observed event | Reporting only. Kept |
| D12 | Superseded by U5 below. As first built, an INFERRED link was only a limitation and did not change ID-2 | as U5 | | Replaced |
| D13 | A failed TIMESTAMP_ORDER or INCOMPARABLE_TIMESTAMPS check is a gap in ID-2. When there is no evidence it is a limitation, not a gap | ignore the checks; block ID-2 on them | The checks compare a row's own timestamps and need no approved limit. STALE and INGEST_LATENCY are not read here, because no limit is approved | **Needs your choice.** It changes ID-2 answers, and through them the label (PATH_PARTIAL) |
| D14 | `limitations` is a field of the shared decision record (`decision.py`: `uncertainty.cop_limitations`), and is empty for any COP that does not set it | an extension field per COP | A decision record must show what it rests on without verifying it, in one place for every consumer | **Needs your choice.** It changes the record contract for all 18 COPs (the field is additive, and no COP's label reads it) |
| D15 | ID-3 reports cross-session continuity: whether an operator was recorded, the tool, and the number of earlier sessions | none | The comparison was implicit in `interaction_route_novelty` | Reporting only. Kept |
| D16 | The missing-input note of a NOT_CALCULATED metric comes from shared code (`cop_package._missing_inputs`), used by ID and by Audit & Traceability | a copy in each COP | Same behaviour in both | Refactor, no change in behaviour. Kept |

## Resolved on 2026-11-20 (08:52Z)

**U5, inferred handoff links: decided by purvi.** A recorded handoff without `parent_agent_id` is not a verified
parent-child relationship. It is kept as INFERRED, with its handoff, its timing and the limitation that it is not
verified.

The contract was checked before the change:
- ID-2 asks whether the relationships between the entities can be reconstructed.
- Its outcomes are FULLY_RECONSTRUCTED, PARTIALLY_RECONSTRUCTED and NOT_RECONSTRUCTABLE. PATH_PARTIAL means
  "the interaction network cannot be fully reconstructed".
- A delegation relationship is a parent-child link. So FULLY_RECONSTRUCTED needs every delegation link recorded,
  and a link that rests on timing alone is now a gap.

No outcome, label or label meaning was changed.

Consequences, all tested:
- A session that delegates without `parent_agent_id` is PATH_PARTIAL (WATCH), not CONTAINED. ID-5 reads the
  partial ID-2 as a cause.
- Audit & Traceability reads the ID-2 answer as a gap and labels that session PARTIAL. With `parent_agent_id`
  recorded, the same session is COMPLETE.
- Resource Management no longer attributes consumption to subagents whose links are inferred. The reading
  stands, at degraded confidence, as UNATTRIBUTED. Unless the collectors record `parent_agent_id`, this will be
  the usual result on real data.
- Ground truth: the expectations of known_delegation and contradictory_depth changed, each marked "U5".
  insufficient_history_with_routes, no_operator_with_routes, downstream_resource_attribution and
  downstream_audit_traceability_clean now record `parent_agent_id`, so that they still test what they were
  written for. Two scenarios were added: downstream_audit_traceability_inferred and
  downstream_resource_attribution_inferred.
- Before the ground truth was updated, the harness was run once on the old expectations with the new code. It
  showed the 18 expectations listed above moving, and no others.

## Needs your decision

**U6, the first-handoff fallback in `signal_traces.link_lanes`. Evidence only; the collector is unchanged.**

- **What it does today.** A subagent lane is joined to the latest handoff recorded before the lane's first step.
  If no handoff precedes the lane, the lane is joined to the session's first handoff, even one recorded later.
  The join uses time alone; it ignores the agent the handoff row names. Consecutive subagent_start rows also
  merge into one handoff step (`signal_traces.py:198`).
- **The test.** On 2026-11-20 the fallback was removed in the working tree only. The full suite, the three
  harnesses and the bench were run, and the line was then restored. `git status` shows `signal_traces.py`
  unchanged.
  - The suite gave 784 passed and 3 failed.
  - The ID harness met 184 of 187. Audit & Traceability met 138 of 138 and Runtime Monitoring 133 of 133.
  - The bench labels did not change.
  - Every failure is in the one scenario where a handoff follows its lane (handoff_after_lane_start and its
    downstream run). There, the lane becomes UNLINKED in ID-2 rather than INFERRED_AGAINST_TIME_ORDER, and
    Audit & Traceability reads "a lane with no parent" as BROKEN_TRACE instead of PARTIAL.
- **Would valid evidence be lost?**
  - No event is lost: the handoff stays a step in the main lane.
  - What is lost is a join that the timestamps contradict.
  - One case is at risk: clock skew between collectors, where the lane's rows come from a collector whose
    clock runs ahead of the handoff's. The fallback joins that lane correctly by accident; without it the lane
    is unlinked.
  - In the synthetic data, the handoff row names the subagent (`agent_id`), and that is evidence the fallback
    ignores.
  - How often the fallback fires on real data is not known. The 2026-11-19 replay found 22 sessions with
    subagent lanes, but it did not record which links used the fallback. Measuring it would need a read-only
    replay, which is not part of this work.
- **Options.**
  1. Keep the fallback (current). The package already marks such links INFERRED_AGAINST_TIME_ORDER and treats
     them as a gap, so nothing is silent at the COP level.
  2. Remove the fallback. Such lanes become unlinked, and Audit & Traceability reads them as BROKEN_TRACE.
  3. Join by the agent the handoff names, when it names one, and otherwise leave the lane unlinked, keeping the
     candidate handoff as evidence. This is a collector change.
- **Recommendation.** Option 1 for now. Option 3 needs your approval and a measurement on real data first. In
  every option the link stays INFERRED, because a handoff row that names an agent is still not a recorded
  `parent_agent_id`.

**U2, `interaction_unattributed_edges`.**
- **Today.** The registry proposes "edges with attribution = unknown" and says the definition is unconfirmed:
  the `attribution` column describes file effects, not interaction edges. The metric is kept in ID-5's
  mapping, is OPTIONAL and is reported NOT_CALCULATED with that missing input. It never affects an answer.
- **Alternatives.**
  - Define it: for example, as an edge whose source agent cannot be identified. That would partly overlap
    `interaction_orphaned_events`.
  - Drop it from the mapping.
- **Consequences.** Defining it adds a cause to ID-5. Dropping it breaks requirement 3, which keeps unbuilt
  metrics in the mapping.
- **Recommendation.** Keep it NOT_CALCULATED until you give a definition.

**U3, approved thresholds for depth and breadth.**
- **Today.** Delegation depth and the largest number of subagents under one parent are reported in ID-4 and
  never judged, because `metrics_config.json` has no approved value for either.
- **Alternatives.** You supply the values, or they stay unjudged.
- **Consequences.** Without values, deep or wide delegation can never make the label PROPAGATING. Only the
  approved measures (`interaction.subagent_lanes`, `interaction.mcp_servers`, `insights.outside`) can.
- **Recommendation.** Leave them unjudged until you choose values. I will not propose numbers.

**U4, route records for CONTAINED sessions.**
- **Today.** Like every COP, Interaction Discovery writes no memory for its quiet label. The routes of ordinary
  sessions are therefore known from the estate's history (5 earlier sessions of the same operator and tool),
  not from memory.
- **Alternatives.** Write a route record for every session.
- **Consequences.** Memory would grow with routine routes, and a record only becomes known after an independent
  reviewer validates it, so it would add review work. It would also break the shared rule that a quiet label
  writes nothing.
- **Recommendation.** No change.

## Not done, by instruction

No Supabase replay, collector change, dashboard work or self-evolution. No model call.
