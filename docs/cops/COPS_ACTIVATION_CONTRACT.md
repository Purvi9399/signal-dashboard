# COPS activation contract: first vertical slice

Branch `feat/cops-activation-router` (local, not pushed). Scope: Runtime Monitoring (RM), Behavior Analytics (BA),
Anomaly Detection (AD) and Recovery & Resilience (RR), routed over coding-agent debugging sessions.

Not done in this slice, as you asked: no SQL integration, no adaptive policy learning, no model spend, no production
enforcement and no separate application.

## 1. What is reused and what is new

| Need | Existing component reused | New in this slice |
|---|---|---|
| Metric definitions | `signal_metrics.METRICS` (id, fn, basis, fields, applies) + `metrics_config.json` thresholds | `activation.MetricDefinition` wraps them with quality and provenance. `telemetry.unknown_share` is the only derived definition, because it is not in METRICS |
| COP–metric edges | `docs/cops/metric_cop_mappings.json` `pairs` (role, interpretation_rule, evidence_requirements); `specs.BY_KEY` behavioural question; `rules.RULES` | `activation.CopMetricEdge`, versioned `cop\|metric@specver`. It is DEFINED only from the mapping doc or from a metric literal the COP's rule function reads; otherwise it is UNDEFINED |
| COP dependencies | `specs.CONSUMES` and `topological_order` (the executed DAG) | `ActivationPolicy.depends_on` must be a subset of CONSUMES; cycles are rejected |
| Consultations | `referrals.py` (ReferralRequest/Response, `Tree` depth, run and time limits, `SCOPE_RECEIVERS`, `needs()`, `run_investigation`), `triage.SPECIALISTS` | `activation.ConsultationContract`. `referrals.Tree(consultations=...)` now opens only contracted referrals. With `None` it keeps the old behaviour |
| Running a COP | `graph.run_cop`, with verification, VS-01, triage and the authority gates unchanged | none. The router calls `graph.run_cop` or `referrals.run_investigation` |
| Decision linkage | `persistence.decision_id`, `SQLiteStore.decisions()`, `contracts.stable_id`/`digest` | `ActivationRecord.decision_id` on COMPLETED records |
| Sessions and runtime | `synthetic.Session`, `evaluation.make_runtime` (dry run, UnavailableProvider) | `activation_scenario.py` builds five debugging variants |
| Dashboard | `dashboard_export.py`, Page 1 view switch, contract-checked loader | `activation.json` (cops-activation/1) and the Activation view |

No second referral engine, rule engine, graph runner or decision store was added. `activation.py` holds the
contracts and the registry, and `router.py` holds the scheduling.

## 2. Exact files changed

New:
- `collector/cops/runtime/activation.py`: contracts (MetricDefinition, CopMetricEdge, Condition, ActivationPolicy, ConsultationContract, ActivationRecord), typed rules `rule:any_condition@1` and `rule:case_has_telemetry@1`, `policies_v1()`, `MappingBundle`, `validate_bundle`, `MappingRegistry` (propose, approve, activate, pinned, propose_from_feedback).
- `collector/cops/runtime/router.py`: `ActivationLog` (append-only, hash-chained, optional SQLite insert-only table `cop_activation_records`), `group_events`, `evaluate`, `Router.route`.
- `collector/cops/runtime/activation_scenario.py`: the five variants, the 7-session synthetic baseline, and trace writing with a replay check.
- `tests/test_activation_router.py`: 18 tests.
- `docs/cops/evaluation/activation/{mapping,normal_debugging,repeated_failures,missing_evidence,concurrent,consultation}.json`: recorded traces.
- `cops-dashboard/src/network/ActivationView.jsx`.
- `cops-dashboard/public/data/activation.json` (exported).
- This report.

Modified:
- `collector/cops/runtime/referrals.py`: `Tree(consultations=None)`. When it is set, `n_plan` skips a receiver without a contract and records the reason.
- `collector/cops/dashboard_export.py`: `build_activation()` writes `activation.json`.
- `cops-dashboard/src/data/contracts.js`: loads and validates the fourth file.
- `cops-dashboard/src/network/NetworkPage.jsx`: adds an "Activation" button to the view switch.
- `cops-dashboard/public/data/{network,decisions,retrieval}.json`: only the `git_commit` stamp changed.

## 3. Contracts and router behaviour

**Policies.** Each COP owns its policy, so `owner` must equal `cop`. A policy names a typed `rule_ref`, its conditions,
its required context, what to do when evidence is missing (`REPORT_UNKNOWN` or `ACTIVATE_TO_ASSESS`), its
`depends_on` list and its rationale.

**Validation.** `validate_bundle` rejects a bundle when:
- a policy owner is not the COP itself;
- a rule is unknown;
- a condition sits on an UNDEFINED or missing edge, or on a supporting-only edge (an undefined interpretation never becomes a trigger);
- required context is unknown;
- `depends_on` is outside CONSUMES, or the dependencies form a cycle;
- a consultation receiver is not consumed, is outside SCOPE_RECEIVERS or SPECIALISTS, or exceeds the depth or time limits.

**Governance.** `propose` stores an invalid bundle as REJECTED. `approve` needs a human approver who is not the
proposer. `activate` needs APPROVED. `propose_from_feedback` can only create a PROPOSED version, so feedback never
changes the active policy.

**Thresholds.** These are the v1 slice policies. The thresholds are the existing `metrics_config` states; no new
numbers were added.

| COP | Activates when | Missing evidence |
|---|---|---|
| RM | the case has telemetry (`rule:case_has_telemetry@1`) | ACTIVATE_TO_ASSESS |
| BA | insights.retry, insights.failed or insights.repetition ≥ EXCEPTION | REPORT_UNKNOWN |
| AD | insights.token_outlier, insights.duration_outlier, activity.action_rate_ratio, access.first_seen_mcp or path.transition_novelty ≥ EXCEPTION (retry and repetition are supporting only) | REPORT_UNKNOWN |
| RR | insights.reverted ≥ WARNING, or retry or failed ≥ EXCEPTION | REPORT_UNKNOWN |

**Routing steps.**
1. Events are grouped into one case per (tenant, session), and duplicate observable ids are dropped.
2. The investigation id is deterministic.
3. For each COP, in topological order, the router records CANDIDATE and then ELIGIBLE, NOT_ELIGIBLE or UNKNOWN. Every verdict carries reasons and an evidence snapshot.
4. A repeat of (case, COP, mapping version, evidence fingerprint) is recorded as SUPPRESSED with `duplicate_of`.
5. Eligible COPs run in dependency waves:
   - a COP that depends on another records WAITING first;
   - COPs in the same wave start concurrently, up to `MAX_PARALLEL=4` and within `referrals.MAX_RUNS` (consultation runs count against that budget);
   - a run over budget is SUPPRESSED;
   - a run that exceeds `referrals.TIMEOUT_S` is TIMED_OUT, and its dependents still proceed.
6. COMPLETED records carry the `decision_id` of the decision record the existing pipeline wrote.
7. Every record is chained to the previous one by `prev_record_id`, and its `record_id` is a hash of the content without wall time.
8. Passing `version=` pins the mapping for replay.

## 4. The multi-COP scenario (simulated)

Run it from `collector/`:

```
python3 -m cops.runtime.activation_scenario --out ../docs/cops/evaluation/activation
```

What is simulated:
- The session rows are synthetic, built with collector schema fields: `command`, `exit_code`, `success`, Edit plus `file_modify` with `content_hash` and `reverted_to_earlier`, `collector`, `contributing`, `collectors_agree` and `turn_id`.
- The baseline estate is 7 synthetic ordinary debugging sessions.
- AD has no model, so it stops at `llm_unavailable`.
- Every run is the real COP subgraph in dry run.
- The approver is a fixed test identity.

Results recorded on 2026-11-13:

| Variant | RM | BA | AD | RR | Notes |
|---|---|---|---|---|---|
| normal_debugging | ELIGIBLE → SUFFICIENT | NOT_ELIGIBLE (retry=1 is WARNING) | NOT_ELIGIBLE | NOT_ELIGIBLE | |
| repeated_failures | ELIGIBLE → SUFFICIENT | ELIGIBLE → POOR_RECOVERY | NOT_ELIGIBLE | ELIGIBLE → INCONCLUSIVE | RR waits for BA, then consults BA (ANSWERED_VERIFIED) |
| missing_evidence | ELIGIBLE → INSUFFICIENT | UNKNOWN | NOT_ELIGIBLE | UNKNOWN | UNKNOWN is neither run nor treated as quiet |
| concurrent | ELIGIBLE | ELIGIBLE | ELIGIBLE (token_outlier CRITICAL) | ELIGIBLE | AD and RR run in the same wave, concurrently |
| consultation | ELIGIBLE | NOT_ELIGIBLE | NOT_ELIGIBLE | ELIGIBLE → INCONCLUSIVE | RR consults BA through its typed contract |

For all five variants the hash chain verified, and replay with the pinned mapping version reproduced every record
except wall time.

## 5. Tests

`python3 -m pytest -q tests/test_activation_router.py` gives **18 passed**. The full suite, `python3 -m pytest -q`,
gives **517 passed, 1 skipped** (the baseline was 499 passed and 1 skipped).

The tests cover these cases:
- **Mapping validity:** a valid, versioned default bundle; an undefined or supporting interpretation cannot trigger; a dependency cycle is rejected; consultation contract limits; an unapproved mapping change cannot take effect.
- **Routing outcomes:** normal debugging; repeated failures; missing evidence leading to UNKNOWN; concurrent runs whose start times overlap; typed consultation; no referral is opened without a contract.
- **Records and limits:**
  - duplicate events and activations;
  - a timeout that lets dependents proceed;
  - the run budget;
  - gates preserved and decisions linked through `store.decisions(decision_id=...)`;
  - immutable chained records;
  - an insert-only SQLite log;
  - deterministic replay.

## 6. Unresolved gaps

- RR stays INCONCLUSIVE because no `recovery_records` artifact exists. AD stays INCONCLUSIVE because it has no model.
- RR consults BA even when BA's finding is already upstream. This is the existing `referrals.needs()` behaviour, because RR's VS-01 BA component stays unknown.
- `metric_cop_mappings.json` is stale for BA and RR. Their edges are DEFINED from the metric literals in `rules.py` (role `rule_input`), and the mapping doc should be updated to match.
- BA's `depends_on` names context_memory and workflow_orchestration, which are outside this slice. They are recorded as "consumed as absent".
- A timed-out thread is recorded and abandoned but not pre-empted.
- The activation log's SQLite table is local only and is not in the Supabase schema, because there is no SQL integration in this slice.
- The approver identity is a string, not an authenticated identity.
- `MAX_PARALLEL=4` is a new constant, not an existing budget.
- The seven-COP hierarchy is still MISSING.

## 7. Viewing the activation graph

```
npm run cops:export      # writes cops-dashboard/public/data/activation.json from the recorded traces
npm run cops:dev         # then open  #/network?view=activation
```

`#/network?view=activation&scenario=concurrent&at=11&acop=recovery_resilience` selects a scenario, the state after
record 11, and a COP. Each COP node shows three states, read only from the records:
- **defined:** the COP has a policy in the approved mapping;
- **eligible:** an ELIGIBLE record exists;
- **active:** an ACTIVATED record exists.

The slider steps through the recorded records in sequence. It does not animate or add transitions. Screenshots are in
`/mnt/project-files/pr4/demo/activation_screens/`.
