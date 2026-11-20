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

## Acceptance

On 2026-11-20 at 09:23Z, purvi approved Interaction Discovery as COP 2.

| Item | Detail |
|---|---|
| Commits | `a1050d6`, `3ac8d10`, `f774037`, `cacbead`, and the commit that records this decision |
| Harness at approval | 192 of 192 expectations |
| Full suite at review | 788 passed, 1 skipped |
| Her decisions | D13 approved, D14 approved; U2, U3, U4 and U6 kept as they are |

The decisions are recorded below.

## Made on 2026-11-20, reviewed by purvi at 08:52Z and decided at 09:23Z

| # | Decision | Alternatives considered | Rationale | Status |
|---|---|---|---|---|
| D11 | Every delegation and MCP link carries its basis: RECORDED, INFERRED, INFERRED_AGAINST_TIME_ORDER or UNLINKED (`cop_package.interaction_links`). An inferred link also names the handoff it was joined to and the agent that handoff names | report only the ratios | A relationship that can be inferred is not a verified one. The basis says which it is, and the handoff stays visible as an observed event | Reporting only. Kept |
| D12 | Superseded by U5 below. As first built, an INFERRED link was only a limitation and did not change ID-2 | as U5 | | Replaced |
| D13 | A failed TIMESTAMP_ORDER or INCOMPARABLE_TIMESTAMPS check is a gap in ID-2. When there is no evidence it is a limitation, not a gap | ignore the checks; block ID-2 on them | The checks compare a row's own timestamps and need no approved limit. STALE and INGEST_LATENCY are not read here, because no limit is approved | **Approved 2026-11-20.** A timestamp-order failure prevents ID-2 from being FULLY_RECONSTRUCTED |
| D14 | A structured `uncertainty.limitations` field in the shared decision record for all 18 COPs (`decision.py`). Each entry has `kind`, `question`, `subject`, `reason_code`, `detail`, `evidence_refs` and `source`, and `kind` is MISSING_EVIDENCE, NOT_CALCULATED, LIMIT_NOT_APPROVED or INFERRED_RELATIONSHIP. An inferred relationship always carries `verified: false`. A COP may report entries itself (`cop_specific.limitations`), and the record also derives entries for every COP from each question's unassessed metrics and checks and from `missing_evidence` | free text per COP (as first built); an extension field per COP | One place, in one shape, where every consumer can see what a decision rests on without having it, with the four cases kept apart | **Approved 2026-11-20.** It is additive: a record without the field still validates, `cop_limitations` keeps the COP's entries as text, and no label reads the field. The tests cover serialization, validation, downstream consumption and the four kinds |
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

## Decided on 2026-11-20 (09:23Z)

| # | Decision | Alternatives | Reason |
|---|---|---|---|
| U6 | **Keep the fallback; the collector is not changed.** A link that comes from the fallback is INFERRED, with relationship INFERRED, reason code FIRST_HANDOFF_FALLBACK, its handoff and lane timestamps, and step refs. Its limitation has `verified: false`. It is never reported as verified | remove the fallback; join by the agent the handoff names | Removing it loses nothing that is evidenced, but the alternatives are collector changes, and those are deferred |
| U2 | **Keep `interaction_unattributed_edges` NOT_CALCULATED,** in the mapping, with its missing definition documented. No substitute is used | define it; drop it | The registry definition is unconfirmed |
| U3 | **Keep depth and breadth thresholds unapproved.** The measurements are kept in ID-4's basis. Each is a LIMIT_NOT_APPROVED limitation saying it is reported, not judged, and cannot make PROPAGATING | supply values | No value is approved, and none may be invented |
| U4 | **Keep the existing quiet-session behaviour.** A CONTAINED finding writes no route record. Its execution evidence and decision record are still produced for audit | a route record for every quiet session | It adds review work and breaks the shared quiet-label rule |

The analysis behind these decisions, as presented for the review:

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

## ID-4 consistency check (2026-11-20, 09:32Z)

| # | Decision | Alternatives considered | Rationale |
|---|---|---|---|
| D17 | ID-4 judges only the propagation measures (`propagation_measures` in METRICS.yaml: `interaction.subagent_lanes` and `interaction.mcp_servers`) against their approved thresholds. `insights.outside` moves to `boundary_observations`, with its value, state, evidence refs and owner (Policy Enforcement). It is not judged, so an edit to /etc/hosts alone no longer gives BEYOND_APPROVED_BOUNDS or PROPAGATING | keep judging `insights.outside` (the legacy rule `cops/interpret.py:_interaction_discovery` did this); make ID-4 INCONCLUSIVE whenever depth or breadth is unjudged | See below |

**Rationale for D17:**

- **The question.** ID-4 asks how deeply and widely activity propagates across agents, tools and services.
- **The metric.** `insights.outside` is "Work outside the workspace" (`signal_metrics.m_outside`): file actions
  outside the workspace. Its approved threshold in `metrics_config.json` (exception 1) is for that workspace
  condition, which Policy Enforcement reads as a boundary crossing. It is not a limit on propagation.
- **Ownership.** The registry does not assign `insights.outside` to Interaction Discovery. It reaches this COP only
  through `specs.py` metric_keys (a code-only pair).
- **The approved measures.** The two propagation measures count the agents and services that activity reached,
  are assigned to this COP by the registry, and have approved thresholds (5/15/50 and 3/6/12).
- **No vocabulary change.** The question, outcome names and label order are unchanged.
- **Unjudged depth and breadth.** ID-4 keeps WITHIN_APPROVED_BOUNDS when the approved measures are within their
  bounds. The unjudged depth and breadth stay LIMIT_NOT_APPROVED limitations, which is where purvi's U3
  decision placed them. Making ID-4 INCONCLUSIVE instead would make every session PATH_PARTIAL, through the
  accepted label order, and would change what the labels mean. purvi may still choose that.

**Effects (all tested):**

- **propagating_outside_workspace** was ID-4 BEYOND / PROPAGATING. It is now ID-4 WITHIN / CONTAINED. Its expectations
  were changed before the corrected code was run, and the change is marked in GROUND_TRUTH.yaml.
- **downstream_policy_outside_workspace** (new): Policy Enforcement reads `insights.outside` itself. With no policy
  store, its candidate VIOLATION is rejected by its graded verification (VS-01) and gives INCONCLUSIVE with
  `policy_store` missing.
  - That is Policy Enforcement's existing behaviour.
  - My first expectation for this scenario said VIOLATION and was wrong about that gate. The first run showed it,
    and the expectation was corrected and marked.
- **several_concerns** (new): a new route, sixteen subagent lanes (EXCEPTION) and inferred links all hold at once.
  - The label is NEW_ROUTE, and `also_held` is [PROPAGATING, PATH_PARTIAL] in the accepted order.
  - All five answers stay in the decision record, each with its explanation, basis, metrics and evidence.

## Remaining limitations

- On real data, provenance stays unverified: the raw event store is not supplied, and the meaning of `raw_ref` is
  not defined.
- Delegation links are likely to be inferred on real data, unless the collectors record `parent_agent_id`. That
  makes ID-2 partial, and Resource Management will not attribute those sessions' consumption.
- Depth and breadth are not judged (U3). STALE and INGEST_LATENCY are not read, because no limits are approved.
- `interaction_unattributed_edges` is NOT_CALCULATED (U2).
- How often the first-handoff fallback fires on real data has not been measured (U6).

## Not done, by instruction

No Supabase replay, collector change, dashboard work or self-evolution. No model call.
