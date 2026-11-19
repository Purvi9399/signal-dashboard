# Runtime Monitoring: decisions

## Made

| # | Decision | Alternatives considered | Rationale | By |
|---|---|---|---|---|
| M1 | Presence, reliability and sufficiency stay separate, with no score | a confidence score; `evidence_sufficiency_score` from the mapping document | A score hides which of the three failed | purvi, 2026-11-18 |
| M2 | RM-2 restored as "Do different telemetry sources agree about the same observed activity?" | none | The workbook omits question 2 | purvi, 2026-11-19 |
| M3 | One source means comparison is not possible, never agreement | treat one source as agreeing | Agreement must be demonstrated | purvi, 2026-11-19 |
| M4 | RM-4 keeps ingest latency, evidence age and continuity apart, each against its own configurable limit; an absent limit leaves the check NOT_EXECUTED | one freshness figure; a default limit | Latency is not age; no universal threshold | purvi, 2026-11-19 |
| M5 | `collector_heartbeat` and `collector_errors` stay NOT_CALCULATED and are listed as not assessed | treat absence as healthy | Absence is not health | purvi, 2026-11-19 |
| M6 | A verified bad result is a SUFFICIENT answer with a finding, not INCONCLUSIVE | inconclusive on any failure | Sufficiency is separate from good or bad | proposed in Block 2.2 (choice 1); awaiting your confirmation |
| M7 | METRICS.yaml is the single source of the question contract; `evidence_assessment.QUESTIONS` is built from it | keep the Python dict | No duplicated logic | this package |
| M8 | YAML is read with PyYAML, already installed as a dependency of langchain-core (required by langgraph) | JSON | You asked for METRICS.yaml. Not pinned in `requirements-cops-runtime.txt` (see U7) | this package |
| M9 | The question answers do not change the COP label, action, finding or memory | wire them in now | You said not to change existing COP behaviour yet | purvi, 2026-11-19 |

## Unresolved: needs your decision

| # | Question | Evidence | Options |
|---|---|---|---|
| D1 | Should the five answers decide the COP label? | The harness finds rules.py too confident in two worlds: unreliable_provenance and partial_collector_failure are labelled SUFFICIENT. Against the existing bench's normal case (no `ingested_at` or `raw_ref`), RM-3 would be INCONCLUSIVE and RM-4 and RM-5 INSUFFICIENT | (a) keep rules.py and publish the answers beside it; (b) derive the label from the answers (needs a mapping you approve); (c) leave as is |
| D2 | Evidence limits: `max_ingest_lag_s`, `max_evidence_age_s`, `max_missing_event_rate` | None approved, so RM-4 is INCONCLUSIVE in every real session | values per estate, or leave unset |
| D3 | RM-5 rule: uncertain when any earlier question is INCONCLUSIVE or answered EXPECTED_COLLECTORS_MISSING, PARTIALLY_LINKED, UNLINKED or NOT_CURRENT_OR_CONTINUOUS, or the unknown share is not NORMAL | With this rule, every single-source session is uncertain, because RM-2 cannot be answered | accept; exclude RM-2's COMPARISON_NOT_POSSIBLE; another rule |
| D4 | Memory provenance lacks the cycle (`run_id`) and the evidence sources | `graph.validate_memory_update` is shared by all 18 COPs | add `run_id` and `metric_version` to provenance for every COP (additive, record ids unchanged), or keep |
| D5 | A re-run of a case writes a second `collector_reliability` record | The payload lineage carries per-run tool-call ids (`test_rerun_of_the_same_case_adds_a_second_record_known_defect`) | drop tool-call ids from the digested payload (shared code), or accept |
| D6 | MC-01, recomputing cited metric values from raw observables, does not run for Runtime Monitoring | verifier results show NOT_RUN | enable it for this COP, or accept |
| D7 | Fabricated `raw_ref` values pass the provenance check | Only presence is checked; the raw store is not read | resolve raw_ref against `source_events` (read-only), or accept |
| D8 | A collector that stops mid-session is not detected | `collector_heartbeat` needs `collector_capability.last_seen` as an input to compute() | build it from the existing table, or accept |
| D9 | RM-2 fields, RM-5's overlap with RM-2's conflict check, and the question-to-metric mapping | Proposed in Block 2.2, not yet confirmed | confirm or change |

## Not done, by instruction

- The other 17 COPs.
- Paid model calls.
- Threshold changes.
- Schema changes.
- Self-evolution.
- Pushes.
