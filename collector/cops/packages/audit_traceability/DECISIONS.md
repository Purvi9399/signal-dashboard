# Audit & Traceability: decisions

## Made in this package (proposed)

| # | Decision | Alternatives considered | Rationale |
|---|---|---|---|
| D1 | The five answers decide the label; `rules._audit_meaning` is removed and the legacy rule `cops/interpret.py:_audit_traceability` is no longer called by the runtime (it is unchanged, and the legacy runner still uses it). Order: UNATTRIBUTABLE, BROKEN_TRACE, INCONCLUSIVE, PARTIAL, COMPLETE | keep the legacy baseline with `_audit_meaning` on top | The legacy rule reads four metrics; the questions read the trace, 20 built metrics, the checks, upstream answers and memory. A broken link outranks undecided attribution, as the replaced rule let decision linkage do |
| D2 | Reliability checks are read as answers, not as sufficiency gates. A check that could not run (single source, timestamps not supplied, raw store absent, no approved limit) is listed as unassessed | make them `verified` or `blocking` checks | As gates they would make AT-3 and AT-5 INCONCLUSIVE on most sessions for want of inputs no collector supplies today, which the 2026-11-19 instruction rules out |
| D3 | `audit_actor_attribution_rate` and `audit_action_attribution_rate` are reported, not judged | a rate below 1 makes AT-1 partial | `tools._actor_known`: a row without an operator belongs to its session's operator; the rates have no approved threshold |
| D4 | AT-1 is answered ACTOR_NOT_ESTABLISHED when only disk-change attribution is unassessed and actions carry no operator | INCONCLUSIVE | The actor fact does not depend on the file watcher |
| D5 | Each of the 29 registry metrics is assigned to one question by its registry definition (the registry assigns metrics to the COP only) | none | Needed for a question contract; listed in METRICS.yaml |
| D6 | A single conflicting action record impairs AT-5; only source agreement beyond its approved EXCEPTION state breaks the trace | any conflict breaks it | No threshold is invented; the replaced rule broke the trace on the metric's state |
| D7 | `trace_gap` payloads carry `gaps` (one code per unclean answer); memory is listed as recurring and never changes an answer | let a validated recurring gap change severity | Without gap codes the memory read had nothing to match; nothing approves memory changing a decision |
| D8 | An INCONCLUSIVE label from the questions carries reason code MISSING_EVIDENCE, and the thin-telemetry case is now withheld by the COP rather than by the evidence precheck (`tests/test_runtime_root_causes.py` updated) | keep the precheck as the only route | The COP now knows which question could not be answered |

## Needs your decision

| # | Question | Options |
|---|---|---|
| U1 | An upstream COP "that could not judge an action in the record" is read from `decision_status == BLOCKED_BY_EVIDENCE`, which only the legacy runner sets, so in the runtime it never fires | leave it; read it from an upstream INCONCLUSIVE or MISSING_DEPENDENCY finding instead |
| U2 | UNATTRIBUTABLE and BROKEN_TRACE take their severity from the four metrics' states, so they can be NONE or LOW (as before) | keep; set a floor |
| U3 | Governance Architecture receives the finding but its rule does not read it | leave; have it read AT-4's untraced items (interventions, policy versions, verifier coverage) as coverage gaps |
| U4 | Nine audit metrics are not built, including decision, intervention and policy-version traceability, which AT-4 asks about | define them; leave AT-4 on decision evidence links only |

## Not done, by instruction

No Supabase replay, collector change, dashboard work or self-evolution. No model call. Audit & Traceability is
not added to the replay's supported COPs.
