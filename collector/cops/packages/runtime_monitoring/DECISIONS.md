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
| M9 | ~~The question answers do not change the COP label, action, finding or memory~~ Superseded by M10 | wire them in now | You said not to change existing COP behaviour yet | purvi, 2026-11-19 |
| M10 | The five answers bound the COP label: worse of the threshold label and the worst question class's ceiling (INTERPRETATION.md). Questions never raise to INSUFFICIENT | (b) derive the label from the answers alone; (a) publish beside it only | You asked that the answers influence the finding and that no blocking failure give SUFFICIENT. INSUFFICIENT stays tied to approved thresholds | purvi asked 2026-11-19; the mapping is proposed, see D1 |
| M11 | The answers run as the deterministic tool `assess_runtime_evidence`, so the claims that move the label cite a recorded tool result that SC-01 re-reads | compute inside rules.py with no tool record | Reuses the tool and claim mechanism; no new layer | this change |
| M12 | Raw refs resolve against the case artifact `source_events`; without it the resolution is NOT_EXECUTED (RAW_STORE_NOT_SUPPLIED), never passed | treat a non-empty raw_ref as authentic | You asked that a missing raw record never be treated as authentic | purvi asked 2026-11-19 |
| M17 | Typed provenance: raw_ref is native tool evidence; rows link to source events through source_refs or a dedup_key rebuilt from the stored payload with signal_curate.merge_key; each link is VERIFIED, UNRESOLVED, UNAVAILABLE or MISMATCHED; a row without native evidence no longer fails provenance | resolve raw_ref against source_events (M12 as built) | Real raw_ref values are transcript paths that never occur in source_events (11,383 of 11,383 on 2026-11-19) | purvi asked 2026-11-19 |
| M13 | A collector stop is read from the rows (an event type it reported earlier, later seen only by others) and, when supplied, from `collector_capability` (`silent`, or `last_seen` before the session ended) | wait for `collector_heartbeat` | Uses data that exists; no threshold | this change |
| M14 | Memory payload lineage names tools, not this run's tool call ids; the run id, evaluation time, metric version, full lineage and sources go in provenance | keep tool call ids in the payload | The same logical finding gets the same record id, so a re-run is KEPT, not duplicated | purvi asked 2026-11-19 |
| M16 | An input no row carries, or an event kind that never arrived without a declared capability, is "not supplied" and only qualifies the label (was D10) | declare result capability per tool; treat every gap as missing evidence | Fixtures and loaders that never selected a column are not read as degraded telemetry; no other COP changes | purvi, 2026-11-19 |
| M15 | MC-01 recomputes `telemetry.collector_coverage` and `telemetry.source_agreement` from raw rows; RM cites the values of its five metrics as claims | leave MC-01 NOT_RUN | turn and lane linkage come from the trace builder and unknown share from metric states, so they have no independent raw recomputation | this change |

## Unresolved: needs your decision

| # | Question | Evidence | Options |
|---|---|---|---|
| D1 | Confirm the aggregation mapping (M10) | Harness 133/133; bench unchanged (48 PASS, 1 FAIL, 5 INCONCLUSIVE); full suite passes | accept; make EVIDENCE_MISSING a qualification only; let a question raise to INSUFFICIENT |
| D2 | Evidence limits: `max_ingest_lag_s`, `max_evidence_age_s`, `max_missing_event_rate` | None approved, so RM-4 is INCONCLUSIVE in every real session | values per estate, or leave unset |
| D3 | RM-5 rule: uncertain when any earlier question is INCONCLUSIVE or answered EXPECTED_COLLECTORS_MISSING, PARTIALLY_LINKED, UNLINKED or NOT_CURRENT_OR_CONTINUOUS, or the unknown share is not NORMAL | With this rule, every single-source session is uncertain, because RM-2 cannot be answered | accept; exclude RM-2's COMPARISON_NOT_POSSIBLE; another rule |
| D4 | Done in shared code (M14): provenance now has `run_id`, `evaluated_at`, `metric_version`, `lineage` and `sources` for every COP | `graph.validate_memory_update` is shared by all 18 COPs | confirm, or limit it to Runtime Monitoring |
| D5 | Done in shared code (M14): payload lineage names tools, so record ids are stable across re-runs for every COP | Record ids of every COP's memory differ from those written before this change. Only in-memory and SQLite test stores hold any | confirm |
| D6 | Done (M15) for coverage and agreement | turn linkage, lane linkage and unknown share are not recomputed | confirm |
| D7 | Code done (M12). Nothing loads `source_events` into a real case yet, so real runs report RAW_STORE_NOT_SUPPLIED | Real `raw_ref` is the transcript path, so it resolves to the file, not to the line. `file_origin` is the realpath; a raw_ref through a symlink will not resolve | approve a read-only loader of `source_events` for the case session; decide whether file-level resolution is enough |
| D8 | Code done (M13) | Nothing loads `collector_capability` into a real case yet. A collector that stops and whose event types nobody else reports is still not seen from rows alone | approve a read-only loader of `collector_capability`; `collector_heartbeat` stays NOT_CALCULATED |
| D9 | RM-2 fields, RM-5's overlap with RM-2's conflict check, and the question-to-metric mapping | Proposed in Block 2.2, not yet confirmed | confirm or change |
| D11 | The three labels cannot say whether DEGRADED is an observed condition or weak evidence | The class is published beside the label in `cop_specific.evidence_assessment` | keep the three labels with the class; or add a label such as UNVERIFIED |

## Not done, by instruction

- The other 17 COPs.
- Paid model calls.
- Threshold changes.
- Schema changes.
- Self-evolution.
- Pushes.
- Loading `source_events` or `collector_capability` from Supabase.
