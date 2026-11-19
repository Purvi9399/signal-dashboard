# Runtime Monitoring

The reference COP package. `METRICS.yaml` is the machine-readable contract the runtime loads. This file, `INTERPRETATION.md`, `MEMORY.md`, `CONNECTIONS.md` and `DECISIONS.md` explain it. `GROUND_TRUTH.yaml` and `harness.py` test it.

## Identity

| | |
|---|---|
| Key | `runtime_monitoring` (`collector/cops/specs.py`) |
| Role in the topology | Collector: it consumes no other COP and bounds eleven |
| Overall question | Is the evidence required for this governance decision present, reliable and sufficiently complete? |
| Classification | Every operation is deterministic (`METRICS.yaml` `operations`) |
| COP label | SUFFICIENT, DEGRADED or INSUFFICIENT, decided by `rules.runtime_monitoring` (unchanged) |
| Permitted actions | NONE, WATCH, ALERT, REQUEST_CONTEXT, PASS_TO_COP |

## Purpose

Runtime Monitoring tells every other COP whether the telemetry under its reading can be trusted.

A verified INSUFFICIENT reading stops any downstream COP from reporting its quiet ("nothing to see") label:
- `graph._gate_required` withholds the label.
- Verifier DS-01 rejects it if one gets through.

Runtime Monitoring judges the evidence only. It never judges the agent's behaviour.

## Authority

- **May:** report, WATCH, ALERT, ask for context (the collector breakdown), and hand off to Audit & Traceability or Incident Response.
- **May not:**
  - change collector configuration;
  - discard or rewrite events;
  - instruct, pause or block an agent;
  - validate its own memory.

Action selection is `triage.LABEL_ACTIONS["runtime_monitoring"]`: DEGRADED gives WATCH and INSUFFICIENT gives ALERT, both at notification tier or below.

## Inputs and outputs

**Inputs**
- Observables rows for one session. Fields are listed per question.
- Metric events from `signal_metrics.compute(..., catalogue=True)`.
- `metrics_config.json`:
  - the approved `thresholds` and `expected_collectors`;
  - `evidence_limits` (none approved).
- The case's evaluation time (`computed_at`).
- Memory: `collector_reliability` records of the same tenant. These are read, but do not change the decision.

**Outputs**
- The COP finding (`graph.persist_and_publish`), unchanged.
- Per question, from `cop_package.assess_runtime_monitoring`:
  - the assessment (presence, reliability, sufficiency, evidence used, reasons);
  - the answer (outcome, basis, findings, not-assessed items).

  It is published in the finding as `cop_specific.evidence_assessment` and bounds the label (INTERPRETATION.md).

## The five questions

Shared rules apply to every question:
- **INCONCLUSIVE:** the outcome is INCONCLUSIVE whenever sufficiency is not SUFFICIENT. INSUFFICIENT means evidence is missing or comparison is impossible. INCONCLUSIVE means a blocking check failed, or a required check could not run, for example because no limit is approved.
- **Authority:** no question carries authority of its own. The COP label and its action are the only decision.
- **Independent verification:** the harness compares every answer with `GROUND_TRUTH.yaml`. That file is written from what each scenario's rows contain, never from Runtime Monitoring's output. The COP finding is checked by `verifiers.py` (EV-01/02, ID-01/02, SC-01/02, DS-01, VS-01, AU-01, MU-01). MC-01, the recomputation of cited metric values, does not run for this COP today (`DECISIONS.md` D6).

### RM-1 Are the expected telemetry collectors operating and providing the required data?

1. **What it asks:** whether every collector the tool's install should produce actually contributed rows.
2. **Metrics:** `telemetry.collector_coverage` (required). `collector_heartbeat` and `collector_errors` are optional and NOT_CALCULATED.
3. **Raw evidence:** `collector`, `contributing` and `tool` per row, plus `expected_collectors` for the tool.
4. **Observation:** the expected collectors, the collectors present, those missing, and the coverage value with its approved-threshold state.
5. **Interpretation:**
   - ALL_EXPECTED_COLLECTORS_CONTRIBUTED when none are missing.
   - EXPECTED_COLLECTORS_MISSING otherwise, listing the missing collectors.
   - "Contributed" means at least one row. A collector that stopped part-way is in the basis (`stopped`), from the rows and, when supplied, `collector_capability`. `collector_heartbeat` is still not built and is listed under `not_assessed`.
6. **Deterministic / LLM:** all deterministic.
7. **Context and memory:** none.
8. **Upstream:** none.
9. **Outcomes:** ALL_EXPECTED_COLLECTORS_CONTRIBUTED, EXPECTED_COLLECTORS_MISSING, INCONCLUSIVE.
10. **INCONCLUSIVE when:** no collector expectation applies to the tool, for example unattributed rows.
11. **Authority:** none of its own.
12. **Verification:** harness scenarios normal, abnormal_collector_silent, partial_collector_failure and unattributed_tool.

### RM-2 Do different telemetry sources agree about the same observed activity?

1. **What it asks:** where two or more sources saw the same event, whether they recorded the same thing.
2. **Metrics:** `telemetry.source_agreement` (required when applicable), and the CONFLICTING_SOURCES check, which must reach a verified outcome.
3. **Raw evidence:** `collector`, `contributing` and `collectors_agree` per row. `collectors_agree` is set by `signal_curate.py` when more than one collector contributed.
4. **Observation:** the sources present, the rows cross-checked, the rows that disagree with their ids, and the agreement share.
5. **Interpretation:** SOURCES_AGREE or SOURCES_DISAGREE. One source, or no event seen by two sources, is COMPARISON_NOT_POSSIBLE. It is never agreement.
6. **Deterministic / LLM:** deterministic. Deciding which source is right is a specialist referral that is recorded but not run (`CONNECTIONS.md`).
7. **Context and memory:** none.
8. **Upstream:** none.
9. **Outcomes:** SOURCES_AGREE, SOURCES_DISAGREE, INCONCLUSIVE.
10. **INCONCLUSIVE when:**
    - there is one source;
    - no event was seen twice;
    - `collectors_agree` was never read (EVIDENCE_MISSING).
11. **Authority:** none of its own.
12. **Verification:** normal, contradictory_evidence, abnormal_collector_silent (one source) and adversarial_replayed_agreement (duplicates are not counted).

### RM-3 Can observed actions be reliably linked to their originating turns, agents and execution lanes?

1. **What it asks:** whether each action can be traced to the turn, agent and lane that produced it.
2. **Metrics:** `telemetry.turn_linkage` (required) and `telemetry.lane_linkage` (required when subagents ran).
3. **Raw evidence:** `turn_id`, `agent_id`, `parent_agent_id`, `tool_use_id` and the observable types of action rows.
4. **Observation:** the share of steps with a turn, the steps without one, and lane linkage.
5. **Interpretation:**
   - ALL_ACTIONS_LINKED when turn linkage is 1 and lanes are linked or not applicable.
   - UNLINKED when it is 0.
   - PARTIALLY_LINKED otherwise.
   - These are definitional cut points, not thresholds.
6. **Deterministic / LLM:** deterministic.
7. **Context and memory:** none.
8. **Upstream:** none.
9. **Outcomes:** ALL_ACTIONS_LINKED, PARTIALLY_LINKED, UNLINKED, INCONCLUSIVE.
10. **INCONCLUSIVE when:**
    - no action was observed (INSUFFICIENT);
    - action rows from two sources disagree (CONFLICTING_ACTION_SOURCES);
    - provenance cannot be verified: a row's link to its source events (source_refs, or a dedup_key reconstructed from stored payloads) is UNRESOLVED or MISMATCHED, a derived_from parent is not a row of the session, a native transcript contradicts the row, no record-level join exists (SOURCE_JOIN_UNAVAILABLE), the raw store was not supplied, or provenance was never read. raw_ref names native tool evidence; a populated raw_ref is never verification.
11. **Authority:** none of its own.
12. **Verification:** negative_finding_complete_evidence (UNLINKED is a supported answer), partially_linked, unreliable_provenance and contradictory_evidence.

### RM-4 Is the telemetry sufficiently fresh and continuous to support a current governance decision?

1. **What it asks:** three things kept apart:
   - how late rows arrive (ingest latency);
   - how old the newest evidence is at decision time (evidence age);
   - whether expected observations are missing (continuity).
2. **Metrics:** `event_freshness_live` (required) and `missing_event_rate` (required when calls ran). Checks INGEST_LATENCY, STALE and DISCONTINUITY must be verified. INCOMPARABLE_TIMESTAMPS and TIMESTAMP_ORDER block.
3. **Raw evidence:** `occurred_at`, `captured_at`, `ingested_at`, `tool_use_id` and the observable types of request and result rows, plus the evaluation time.
4. **Observation:**
   - median ingest lag;
   - seconds from the newest `occurred_at` to the evaluation time;
   - the share of calls with no result.

   Each is compared with its limit in `evidence_limits`.
5. **Interpretation:**
   - CURRENT_AND_CONTINUOUS when all three are within their limits.
   - NOT_CURRENT_OR_CONTINUOUS otherwise, naming the failed aspects.
6. **Deterministic / LLM:** deterministic.
7. **Context and memory:** none.
8. **Upstream:** none.
9. **Outcomes:** CURRENT_AND_CONTINUOUS, NOT_CURRENT_OR_CONTINUOUS, INCONCLUSIVE.
10. **INCONCLUSIVE when:**
    - a limit is not approved (it is today, for all three);
    - ingest times or call ids are missing;
    - an observation is dated after the evaluation time;
    - timestamps cannot be compared or are out of order;
    - no evaluation time is given.
11. **Authority:** none of its own.
12. **Verification:** stale_at_decision_time, no_approved_limits, missing_evidence and adversarial_future_dated.

### RM-5 Is the available evidence sufficiently complete and trustworthy, or must the finding remain uncertain because of missing or unknown data?

1. **What it asks:** the overall evidence verdict, drawn from the session's own unknowns and from RM-1 to RM-4.
2. **Metrics:** `telemetry.unknown_share` (required), `telemetry.source_agreement` and `missing_event_rate` (required when applicable), and `collector_errors` (optional, NOT_CALCULATED).
3. **Raw evidence:** as for RM-1 to RM-4, plus `source_refs` or `dedup_key`, `derived_from`, `raw_ref` (native transcript) and `confidence` for provenance.
4. **Observation:** the unknown share with its approved-threshold state, the metrics that could not be judged, and the answers to RM-1 to RM-4.
5. **Interpretation:**
   - MUST_REMAIN_UNCERTAIN when any of these hold: the unknown share is not NORMAL; any earlier question is INCONCLUSIVE; or an earlier answer is EXPECTED_COLLECTORS_MISSING, PARTIALLY_LINKED, UNLINKED or NOT_CURRENT_OR_CONTINUOUS.
   - COMPLETE_AND_TRUSTWORTHY otherwise.
   - The answer lists every cause.
6. **Deterministic / LLM:** deterministic.
7. **Context and memory:** none. Baselines from earlier sessions affect it only through `telemetry.unknown_share`.
8. **Upstream:** none.
9. **Outcomes:** COMPLETE_AND_TRUSTWORTHY, MUST_REMAIN_UNCERTAIN, INCONCLUSIVE.
10. **INCONCLUSIVE when:**
    - sources disagree;
    - provenance fails or was never read;
    - the unknown share or the missing-result share cannot be computed.
11. **Authority:** none of its own.
12. **Verification:** normal, abnormal_collector_silent, insufficient_history, unreliable_provenance and adversarial_instruction_text.
