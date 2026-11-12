# COPS dashboard prototype

A standalone, two-page prototype in `cops-dashboard/`, separate from the Signal dashboard. The Signal dashboard's navigation is unchanged.

- **COP–Metric Network.** The 18 COPs, 26 metrics, 60 metric→COP edges and the COP–COP edges, with metadata for every edge.
- **COP Decision Explorer.** 99 recorded governance decisions. Each has a four-step explanation: Trigger, Investigation, Determination and Action.

Every rendered value is read from three versioned files written by one export, `collector/cops/dashboard_export.py`. The React code filters, positions and highlights those values. It does not compute or write any result. No model is called, no database is queried and nothing leaves the machine.

## Run it

```
npm install                       # once, if node_modules is absent
npm run cops:export               # rebuild cops-dashboard/public/data from the recorded sources
npm run cops:dev                  # http://localhost:5174
npm run cops:build && npm run cops:preview    # production build, http://localhost:4174
```

Running the export again on unchanged sources produces byte-identical files. `tests/test_dashboard_export.py::test_export_is_deterministic` checks this.

To regenerate the sources first (optional; all are offline):

```
cd collector
python3 -m cops.runtime.scenario --out ../docs/cops/evaluation/scenario
python3 -m cops.runtime.gateway_recordings --out ../docs/cops/evaluation/gateway
python3 -m cops.runtime.harbor_eval --jobs ../experiments/harbor/jobs --out <dir>   # needs the Harbor job dirs; copy traces to docs/cops/evaluation/harbor
```

## Source audit and integration map

```
recorded sources (repo)                          export                         contracts                    renderers
-----------------------                          ------                         ---------                    ---------
cops/specs.py SPECS, CONSUMES, edge_report  ─┐
runtime/support.py profile() (VS-01)         │
docs/cops/metric_cop_mappings.json           ├─ build_network() ─────────────► network.json   (cops-network/1)   ─► src/network/*
docs/cops/cops_topology.json (target doc)    │   + annotate_exercised()
signal_metrics.METRICS, metrics_config.json  │
runtime/verifiers.py MC-01, tools, triage,   │
authority.TAXONOMY, referrals.SCOPE_RECEIVERS│
docs/cops/cop_hierarchy.json (MISSING) ──────┘

docs/cops/evaluation/test_bench_results.json ─┐
docs/cops/evaluation/scenario/*.json          ├─ build_decisions() ───────────► decisions.json (cops-decisions/1) ─► src/decisions/*
docs/cops/evaluation/gateway/*.json  (new)    │
docs/cops/evaluation/harbor/h0*_trace.json ───┘

runtime/evidence_query.py (new) ────────────── build_retrieval() ────────────► retrieval.json (cops-retrieval/1)
```

`cops-dashboard/src/data/contracts.js` checks each file's contract version and required fields. It refuses to render a file that does not match.

### Findings that shape the prototype

| Area | Finding | Source |
|---|---|---|
| Seven-primary-COP hierarchy | **Not found.** I searched every branch and the git history of this repository, docs/cops (md, json, html), the images in the repo and /mnt/project-files. malhar-sovereign/project-signal is not reachable from this session. Nothing is inferred to replace it: the Hierarchy view shows MISSING and names the contract. | `docs/cops/cop_hierarchy.json` (placeholder, `cops-hierarchy/1`) |
| COP dependencies | `CONSUMES` is the executed graph, with 42 edges. Two declared hand-offs are not consumed. Referrals are recorded runtime events. | `collector/cops/specs.py`, `runtime/referrals.py` |
| Interpretation rules | 60 pairs. 23 have role UNDEFINED; most of these belong to LLM COPs that have no per-metric interpretation contract. The rules were extracted from `interpret.py` at 6a5c942, and `interpret.py` has changed since (3987b35). The export notes this on every edge. | `docs/cops/metric_cop_mappings.json` |
| Why a metric is relevant | This comes from the topology doc, which is a target-state document and not code. 4 of 60 edges have no entry. | `docs/cops/cops_topology.json` |
| Required/optional, evidence role | Read from the VS-01 component that weighs the metric. `required` all → required, quiet/attention → conditional, none → optional. Relevance is direct, supporting or contextual. | `runtime/support.py` |
| SQL / Supabase | **No LangGraph runtime COP executes a database query.** Runtime tools read `CaseData` in memory. Governance Intelligence's decision history reads a local SQLite store when one is passed. The only Supabase reads are the legacy `COPRunner` (`cops/reader.py:SupabaseBackend`), through REST GETs on metric_events, trace_steps and observables. These were not executed here. | `retrieval.json` `data_access_audit` |

## Page 1: COP–Metric Network

- **Nodes.** All 18 COPs from `SPECS` and the 26 metrics from `signal_metrics.METRICS`, grouped by id prefix. COP columns show dependency depth in `CONSUMES`, as a drawing aid. They are labelled as such and are not a hierarchy.
- **Relationship types.** Each edge's types are found by fixed rules, which the page shows. Every type carries the file and line that matched:
  - **observes**: the metric is in `spec.metric_keys`;
  - **interprets**: the mapping role is not UNDEFINED;
  - **validates**: a VS-01 component weighs the metric;
  - **triggers**: the role is `driver`;
  - **acts**: the rule text names an action.
- **Edge panel.** It shows:
  - metric ID and COP ID, and the relationship types;
  - why it is relevant, and how the COP interprets it;
  - required, optional or conditional, and the evidence role;
  - source collector (compute function, basis, required fields) and retrieval tool;
  - thresholds and spec version;
  - evidence source, including whether MC-01 recomputes the metric;
  - code references and limitations;
  - the recorded decisions that exercised the edge.
- **Missing metadata.** A missing field reads `UNDEFINED · missing contract: …` and is never left blank.
- **Interactions.** Select a COP to see its metrics and COP edges, or a metric to see its consuming COPs. Filter by COP, metric category, relationship type and COP–COP edge kind. Toggle between **Configured** and **Exercised in recorded runs**; the rule for "exercised" is printed. The **Hierarchy** view is separate.

## Page 2: COP Decision Explorer

The unit is one decision. The table is searchable and has these columns:

- Decision ID, COP, Trigger ID and Session IDs;
- Timestamp and Finding;
- Evidence status: coverage, then VS-01 support, as two separate pills;
- Selected action, Authority status and Execution status.

Clicking a session filters to every decision in that session. Sessions are keyed `estate/session_id`, because session ids repeat across separately generated estates. Examples:

- `scenario:successful/ca1` has 7 decisions: the AD root run, the RM, BA and WO receivers, the BA and AD reassessments, and the act phase.
- Each Harbor session has 3.

Each decision opens four steps:

1. **Trigger.** Id, kind, description, time, sessions, condition or threshold, and what was observed, with the trace pointer.
2. **Investigation.** It shows:
   - evidence coverage (COMPLETE, PARTIAL or ABSENT, with its rule), kept separate from support;
   - metrics and records inspected;
   - tools called, with their arguments and returns;
   - model steps, with a `simulated` flag;
   - COPs consulted, additional evidence requested and missing dependencies;
   - SQL retrieval, which is `false` with its basis;
   - what remained unavailable;
   - retrieval records: COP, tool, reason, scope, data source, returned refs, status, latency and effect;
   - resolution attempts, listed only where a runtime event exists. These are tool calls for retrievable items, referrals (sent, answered, timed out or not opened), requests for context and human escalation.
3. **Determination.** It shows:
   - the label and VS-01 decision;
   - support, against, unknown, quotient and the 1–5 grade, kept compact;
   - supporting and limiting evidence, and contradictions;
   - **changed after investigation**: before and after support, resolution and component changes;
   - dimensions, thresholds, checks, claims and verifier results, under an expander.
4. **Action.** Four separate boxes:
   - **Proposed by the COP**;
   - **Selected (triage)**, with the route;
   - **Authority**: whether the selected action was authorised, the stage, the final action, whether that was authorised, and any substitute;
   - **Execution and outcome**: status, mode and gateway lifecycle.

   Below them: approval and approval events, delivery (from `authority.TAXONOMY`), receipt, human task and router referral.

**Related decisions.** Each decision links to related ones: reassesses, acts on, answers a referral from, and referred to. These links come from run keys and referral ids.

**Coverage rule** (`dashboard_export.coverage`, read from the runtime's sufficiency record):
- ABSENT if a REQUIRED item is missing or nothing was validated;
- PARTIAL if any item is missing or unverified;
- otherwise COMPLETE.

## Seed data and execution modes

| Source | Decisions | Execution mode shown |
|---|---|---|
| 54-case bench (`test_bench_results.json`) | 54 | `recorded_llm_replay` (replies from the approved live run, replayed) or `deterministic` |
| Coding-agent scenario, 5 variants | 36 | `scripted_simulation`; act phase `scripted_simulation + real local gateway execution` |
| Gateway recordings (new) | 3 | scripted or deterministic COP + real local gateway (loopback HTTP, SQLite), mock tool runner |
| Harbor H01/H02 | 6 | real tool execution in Harbor containers by a scripted, model-free agent; AD has no model (`llm_unavailable`) |

**Clickable examples.** The Examples bar picks each one by matching recorded source fields, not by result:

- **Anomaly Detection abnormal** (bench): ANOMALOUS, SUPPORTED, ALERT, AUTHORISED_DRY_RUN.
- **Policy Enforcement abnormal** (bench): proposed BLOCK, selected BLOCK_OPERATION, REJECTED_BY_AUTHORITY (AU-01 `intervene_in_dry_run`), substitute ALERT, human route.
- **Workflow Orchestration abnormal**: the bench case is recorded **INCONCLUSIVE** and is shown as recorded. The STALLED example is the WO receiver in the scenario, triggered by Behaviour Analytics' referral.
- **Operational scenario**:
  - AD's initial ANOMALOUS (support 0.6909) refers to RM (INSUFFICIENT) and BA (POOR_RECOVERY);
  - BA refers on to WO (STALLED), and to CM (NOT_OPENED, outside the referral scope);
  - AD reassesses: support 0.6909 → 0.4727, CONFIRMED;
  - the act phase executes ALERT in the sandbox, with gateway PROPOSED → AUTHORISED → DELIVERED → APPLIED → VERIFIED.
- **Action lifecycles**:
  - ALERT: delivered;
  - REQUEST_APPROVAL: AWAITING_APPROVAL, approved by a named test identity, then VERIFIED;
  - HOLD_TOOL_CALL: the mock tool call is held while waiting for a decision;
  - review escalation: the contradictory variant;
  - approval denied: the denied_approval variant.

**Actions are not changed for display.** No action was replaced by WATCH, and no intervention was added where the runtime selected NONE or WATCH. `test_no_watch_substitution` checks the selected action against the bench trace.

## Evidence Query Service (prepared, not wired)

`collector/cops/runtime/evidence_query.py` is typed, read-only and scoped:

- **Query types.** There are seven: `decision_history`, `metric_baseline`, `behaviour_history`, `policy_versions`, `quota`, `agent_registry` and `context_snapshots`. There is no free-form query and no model-generated SQL. Unknown parameters are rejected.
- **Permissions.** Each COP may run only the types its assessment grants:
  - GI, BA, CM, AT, KPM and ALM: history;
  - PE: policy;
  - AD: baseline;
  - RM: quota.
- **Scope.** Every query must carry tenant, session and agent scope. Cross-tenant and out-of-session queries are refused.
- **Retrieval records.** Every call, including a refused one, returns a record with: requesting COP, query, reason, scope, data source, returned refs, status, latency, and an effect field for the caller.
- **Adapters.**
  - `InMemoryAdapter` reads the same `CaseData` the tools read.
  - `SupabaseAdapter` is a disabled stub that returns `DISABLED` and opens no connection.
- **Not wired in.** No COP calls the service yet, so no scoring, verifier or authority path changed. `retrieval.json` carries the data-access audit and the per-COP assessment (current tools, data source, `reaches_database: false`, proposed query types, gap).

## Changed files

New:

- `collector/cops/dashboard_export.py`: the export (network, decisions, retrieval).
- `collector/cops/runtime/decision_record.py`: `run_detail()` copies a COP run state into a JSON record.
- `collector/cops/runtime/gateway_recordings.py`: local ALERT, REQUEST_APPROVAL and HOLD_TOOL_CALL recordings.
- `collector/cops/runtime/evidence_query.py`: the Evidence Query Service, audit and assessment.
- `docs/cops/evaluation/gateway/{alert,request_approval,hold_tool_call}.json`.
- `docs/cops/cop_hierarchy.json`: the hierarchy contract placeholder (status MISSING).
- `cops-dashboard/`:
  - `index.html` and `vite.config.js`;
  - `public/data/{network,decisions,retrieval}.json`;
  - `src/main.jsx`, `App.jsx`, `nav.js`, `ui.jsx` and `index.css`;
  - `src/data/contracts.js`;
  - `src/network/{NetworkPage,Details,HierarchyView}.jsx` and `layout.js`;
  - `src/decisions/{DecisionsPage,DecisionDetail}.jsx`.
- `tests/test_dashboard_export.py` (14 tests) and `tests/test_evidence_query.py` (8 tests).
- `docs/cops/COPS_DASHBOARD_PROTOTYPE.md`: this file.

Modified:

- `collector/cops/runtime/scenario.py`: adds a `run_details` trace stage with the full run records, plus the baseline session ids.
- `collector/cops/runtime/harbor_eval.py`: adds `decision_id`, `detail`, `act_detail` and `baseline_session_ids` per record.
- `docs/cops/evaluation/scenario/*.json` and `docs/cops/evaluation/harbor/h0{1,2}_trace.json`: regenerated. Summary results are unchanged.
- `package.json`: adds the `cops:export`, `cops:dev`, `cops:build` and `cops:preview` scripts.
- `.gitignore`: adds `cops-dashboard/dist`.

Not changed: scoring formulas, VS-01, triage thresholds, authority contracts, bench labels, and the Signal dashboard's `src/`.

## Tests and build

- `python3 -m pytest -q`: **499 passed, 1 skipped**. That is the 477-test baseline plus 22 new tests.
- `npm run build` (Signal dashboard) and `npm run cops:build` (prototype) both succeed.
- `oxlint` reports only `only-export-components` warnings.
- No paid model calls, production interventions, pushes or merges.

## Known gaps

- **Hierarchy.** It stays MISSING until the original seven-primary-COP diagram is supplied. Then fill `docs/cops/cop_hierarchy.json` (status DOCUMENTED, source_document, levels) and re-run the export. The export rejects unknown COP ids.
- **Bench timing and multi-session evidence.**
  - Bench decisions have no per-decision timestamp. The bench run time is shown and labelled.
  - Bench decisions record no event time and no baseline session ids.
  - Multi-session evidence is linked only for the scenario and Harbor runs, as `baseline_session_ids` from `CaseData.other_sessions()`.
- **Scenario run records.** These lack per-metric state values and model hypotheses, which show as "not recorded". The bench records have both.
- **Retrieval latency.** Bench tool calls carry no latency, so it is marked unavailable.

## Demo script (about 5 minutes)

1. Run `npm run cops:dev`. The **COP–Metric Network** opens with all 18 COPs. Point out the red pills: 23 of 60 interpretations are UNDEFINED, and the hierarchy is MISSING.
2. Click **Hierarchy**. It shows the missing contract, where I searched, and every COP marked UNDEFINED. Nothing is invented.
3. Back on **Network**, click **Anomaly Detection**. Its seven metrics and COP edges light up. Click `insights.token_outlier` in the list. The panel shows the five relationship types with file:line sources, the interpretation (with the stale-extraction note), the thresholds, MC-01 recomputation, the limitations, and the decisions that exercised the edge.
4. Toggle **Exercised in recorded runs**. Line width is now the number of recorded decisions. Referral edges (AD→RM, AD→BA, BA→WO) appear because they were sent in recorded runs.
5. Open **COP Decision Explorer** and choose Session `scenario:successful/ca1`. One session has 7 decisions.
6. Open **Operational scenario: AD with referrals**. Investigation lists the referral attempts to RM and BA. Related decisions links to the receivers, and BA's run shows its own referral to WO and a CM referral NOT_OPENED.
7. Open **…its reassessment**. Under Determination, Changed after investigation shows support 0.6909 → 0.4727, CONFIRMED.
8. Open **…its local ALERT**. Action shows proposed ALERT, selected ALERT, AUTHORISED, and EXECUTED_SANDBOX with gateway PROPOSED → … → VERIFIED.
9. Open **Policy Enforcement abnormal**:
   - the COP proposed BLOCK and triage selected BLOCK_OPERATION;
   - the authority stage is REJECTED_BY_AUTHORITY, so the selected action was not authorised;
   - the final ALERT was authorised in dry-run, and nothing was executed.
10. Open **HOLD_TOOL_CALL**. The mock Write is WAITING_FOR_DECISION, the test reviewer approves the hold, the call stays unexecuted, and the gateway ends VERIFIED.

Screenshots (16, at 2× scale) are in `/mnt/project-files/pr4/demo/dashboard_screens/`.
