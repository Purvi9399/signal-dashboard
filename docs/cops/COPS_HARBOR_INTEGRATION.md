# Harbor → Signal → COPS: first integration

Branch `feat/cops-langgraph-runtime`, local commits only (nothing pushed). Date: 2026-11-12.

**What ran.** Real Harbor 0.25.0 trials ran in Docker. A **scripted, model-free agent** worked them: every
command it ran is fixed in a playbook, and no language model chose anything. The commands, their exit codes,
output and timings are real executions inside the task containers. No model was called anywhere: no
coding-agent model, and no COP model. Anomaly Detection is an LLM COP, so it ran without a provider and says
so. Nothing was pushed, merged or connected to an external system.

**What did not run.** No live LLM coding agent. The two are kept apart throughout. Section 11 says what a live
run needs.

**Headline.**

| Step | Result |
|---|---|
| A. Docker and Harbor run a custom task | yes |
| B. Verifier fails on buggy code, passes on reference | yes, both tasks |
| C. Real Harbor execution artifacts | 8 scripted, 4 oracle and 3 nop trials |
| D. Raw events mapped into Signal | 14 trials, 0 rejected; re-ingest inserts 0 |
| E. RM plus a behavioural COP on ingested evidence | RM, BA and AD ran on H01 and H02 |
| F. Complete decision trace | written for both cases |
| G. Regression | 477 passed, 1 skipped (449 before plus 28 new); bench unchanged at 48 PASS / 1 FAIL / 5 INCONCLUSIVE, F1 PASS / F2 FAIL |

Against ground truth:

- Runtime Monitoring agrees on both cases.
- Anomaly Detection agrees on both, but only because it honestly could not run.
- Behaviour Analytics **disagrees on both**. The causes are in section 9. Neither was forced or tuned.

---

## 1. Integration map

| Concern | Where it is (file : symbol) | Harbor path |
|---|---|---|
| Event ingestion and normalisation | `collector/signal_harbor.py` : `load_trial`, `map_trial`, `IngestStore.ingest`, `ingest_paths` (new). Existing importers for comparison: `collector/adapter.py` (OTLP), `collector/import_test_runs.py`, `collector/signal_claude_hooks.py` : `handle` | Harbor `result.json`, ATIF `trajectory.json` and `signal_exec_log.jsonl` become observable rows |
| Row schema | `collector/observables_schema.sql` (table `observables`; enums `signal_tool`, `signal_collector`, `signal_observable_type`, `signal_confidence`) | rows use `tool='custom'`, `collector='cli_wrapper'`, `confidence='parsed'` and `raw_ref`; see section 7 |
| Session, user, agent and trace IDs | `session_id` = the agent's own run id (ATIF `session_id` = exec-log `run_id` = `agent_result.metadata.run_id`). `request_id` = Harbor trial id. `extra.harbor.{job_id, trial_id, trial_name, task_name, task_checksum, agent}`. `tool_use_id` = `<trial_id>:<call_id>`. `turn_id` = `<session>:step-<n>` of the ATIF user step. `operator_id` / `operator_username` are assigned. Traces: `collector/signal_traces.py` : `build`, `mk_step` (`step_id` = `session:lane:seq`) | `agent_id` is left null on purpose. In `signal_traces.build` it names a **subagent lane**. When it was first set to the agent name, `telemetry.lane_linkage` went CRITICAL on a phantom lane. That is fixed in the adapter. |
| Raw event storage and evidence refs | `IngestStore` (SQLite, insert-once by uuid5 observable id plus content sha256). `raw_ref` = `<job>/<trial>/agent/signal_exec_log.jsonl#L<n>`; `extra.atif_ref` = `…/trajectory.json#steps[i]`. Metric evidence refs are `observable:<id>`: `collector/signal_metrics.py` : `refs` | COP claims cite `observable:<uuid>`, which resolves to a row, which resolves to a line in the trial's artifact |
| Metric layer | `collector/signal_metrics.py` : `compute`, `build_sessions`, `Session`, `estate`, `METRICS` (`m_retry` l.182, `m_repetition` l.169, `m_failed`, `m_collector_coverage` l.268, `m_token_outlier`) | runs unchanged on Harbor rows |
| COPS LangGraph entry points | `collector/cops/runtime/graph.py` : `run_cop` l.1348, `run_case` l.1314, `act_cop` l.1236, `build_cop_graph` l.1174, `build_case_graph` l.1302. `collector/cops/runtime/referrals.py` : `run_investigation` l.855. `collector/cops/runtime/evaluation.py` : `make_runtime` l.43 | `collector/cops/runtime/harbor_eval.py` : `run_case_for` (new) runs the decide phase with `run_cop` in case-graph order (upstream per `graph.CONSUMES`), then the act phase with `act_cop` |
| Synthetic telemetry adapters | `collector/cops/runtime/synthetic.py` : `Session`; `evaluation.py` : `_estate` l.589; `scenario.py` : `telemetry` | **not used** for Harbor. `CaseData.synthetic` is set False for Harbor cases. |
| RM inputs | `cops/runtime/rules.py` : `runtime_monitoring` l.180 reads `telemetry.unknown_share`, `telemetry.collector_coverage` (needs `metrics_config.json` `expected_collectors[tool]`), `telemetry.turn_linkage`, `telemetry.source_agreement`, and rows by collector | Harbor gives turn linkage 1.0. Collector coverage is UNKNOWN (no expectation for a non-`AGENTS` tool). Unknown share is 0.5. |
| BA inputs | `rules.py` : `behavior_analytics` l.311 reads `insights.retry`, `insights.repetition`, `insights.failed`, `path.transition_novelty`, `activity.action_rate_ratio`, the `compute_sequence_statistics` tool, and upstream RM / CM / WO | all observed from the exec log |
| AD inputs | LLM COP; tools `calculate_deviation` etc. Metrics `insights.token_outlier`, `insights.duration_outlier`, `access.first_seen_mcp`; upstream RM, BA | tokens unobservable, no MCP. No model, so the investigation stops at `llm_unavailable`. |
| WO inputs | LLM COP; needs a `task_plan` artifact (`CaseData.artifacts`) | **not run**: no genuine plan exists (section 5) |
| VS-01 | `collector/cops/runtime/support.py` : `assess` l.644, `_upstream` l.391; thresholds derived from component weights and `interpret.EVIDENCE_LEVEL` | unchanged |
| Handling quotient and route | `collector/cops/runtime/triage.py` : `handling_quotient` l.295, `classify` l.723, `THRESHOLDS` l.382; graph node `grade_and_route` l.782 | unchanged |
| Evidence gate on quiet labels | `graph.py` : `_gate_required` l.317, `_telemetry_insufficient` l.311; `requirements.py` : `precheck` l.220 | decides the H01 BA result (section 9) |
| Authority contracts | `collector/cops/authority.py` : `TAXONOMY` l.114, `check_typed` l.304; graph node `authority_check` l.803 | unchanged |
| Action gateway | `collector/cops/runtime/gateway.py` : `SandboxGateway` l.242, `GatewayStore` l.80, `TestAlertEndpoint`; graph node `execute_action` l.969 | the act phase is supervised through the sandbox gateway (loopback, local SQLite) |

### The four known gaps

All four were fixed in commit `a3f9e40`, before the first trial. Tests: `tests/test_runtime_governance_gaps.py`.

1. **Missing verification-support metadata in published findings.** `graph.py` : `persist_and_publish` now
   publishes `verification_support {decision, score, against, unknown}`. Before the fix, `support._upstream`
   fell back to `RHO_SUPPORTED` 0.9 for an upstream finding with no decision. The case graph and the referral
   path therefore weighed the same upstream finding differently.
2. **The COP was re-run during the action phase.** New `graph.act_cop` / `resume_act` enter the action half of
   the graph with the decided state. No model or tool call is repeated, and `DECIDE_ONLY_CHECKS` are redone.
   `harbor_eval` uses this path.
3. **Non-preemptive referral timeouts.** `referrals._bounded` runs a referral under a wall-time deadline. On
   expiry it is abandoned, any late answer is tagged `late` and discarded, and the result is `TIMED_OUT
   preemptive`.
4. **Unresolved `memory:` references.** `referrals._refs_resolve` resolves a memory ref only to a record id in
   the run's own `memory_view`.

### Further fixes found while integrating

- `agent_id` lane mapping (above), fixed in the adapter.
- `pytest` from the repo root collected the Harbor task suites. Fixed by `experiments/harbor/conftest.py`.

## 2. Files created and changed

Created:

- `collector/signal_harbor.py`: the adapter, idempotent store and CLI.
- `collector/cops/runtime/harbor_eval.py`: the COPS validation driver.
- `tests/test_signal_harbor.py` (24 tests) and `tests/test_runtime_harbor.py` (4 tests).
- `tests/fixtures/harbor/`: three real trials copied unchanged (H01 scripted, H02 scripted, H01 oracle).
- `experiments/harbor/`: `README.md`, `conftest.py` and `.gitignore`; both tasks (`task.toml`, `instruction.md`,
  `environment/Dockerfile`, `environment/app/…`, `solution/solve.sh`, `tests/test.sh`, `tests/test_outputs.py`);
  `agents/scripted_debug_agent.py`; `agents/playbooks/h01.json` and `h02.json`;
  `ground_truth/h01.json` and `h02.json`; `configs/h01-scripted.yaml` and `h02-scripted.yaml`;
  `scripts/fetch_wheels.sh` and `scripts/requirements-env.txt`.
- `docs/cops/evaluation/harbor/`: `summary.json`, `h01_trace.json`, `h02_trace.json`, `ingest_report.json`.
- This report.

Changed in `a3f9e40` (the gap fixes): `collector/cops/runtime/graph.py`, `referrals.py`, `scenario.py`, and
`tests/test_runtime_governance_gaps.py`.

No scoring rule, threshold, VS-01 weight, quotient formula or authority contract was changed.

## 3. Docker and Harbor validation

| Item | Version / result |
|---|---|
| Docker | 29.9.2 (daemon started locally in this sandbox) |
| Python | 3.13.17 on the host; `python:3.12-slim@sha256:5e2bdb1b…` in the tasks |
| uv | 0.11.32 |
| Harbor | 0.25.0, installed with `uv tool install harbor` |
| `harbor lint … --select ALL --ignore shared-verifier` | exit 0 |
| ATIF validation | both scripted trajectories pass `harbor.models.trajectories.Trajectory` (checked with Harbor's own Python); the adapter also runs a structural check |

Container builds have no network. pytest and its dependencies are installed offline from hash-pinned wheels
(`--require-hashes`).

**Verifier on buggy code and on the reference (step B):**

| Job | Agent | Reward | Hidden verifier |
|---|---|---|---|
| `val-h01-nop`, `h01-nop-buggy2` | nop | 0 | 3 failed, 1 passed (visible suite 2 failed, 3 passed) |
| `val-h01-oracle`, `h01-oracle` | oracle (`solve.sh`) | 1 | 4 passed |
| `val-h02-nop` | nop | 0 | 3 failed, 1 passed (visible suite 5 failed) |
| `val-h02-oracle` | oracle | 1 | 4 passed |

**Scripted trials (step C):**

| Job | Trials | Reward |
|---|---|---|
| `h01-scripted-1` | 1 | 1 |
| `h01-scripted` | 3 | 1, 1, 1 |
| `h01-scripted-cfg` | 1, from the YAML config | 1 |
| `h02-scripted` | 2 | 1, 1 |

Wall time per trial is about 20 s. Most of it is environment setup; agent execution is 1.6 s for H01 and 3.6 s
for H02.

One early job (`h01-nop-buggy`) failed at the Docker build, before wheels were vendored. Harbor recorded the
exception and no agent execution, and the adapter yields no rows for it.

## 4. H01 and H02

The ground truth lives in `experiments/harbor/ground_truth/` and is outside every task directory. The agent
never sees it. The hidden verifier is copied in only after the agent finishes.

**H01, normal debugging** (`signal/h01-normal-debugging`)

- **App.** `stockroom/pricing.py`. Tier discounts at 10, 50 and 100 units, with half-up rounding.
- **Bug.** `if quantity > minimum:` should be `>=`, so quantities exactly at a tier minimum get the wrong price.
- **Visible tests.** 5, of which 2 fail before the fix.
- **Reference.** `solve.sh` changes `>` to `>=`.
- **Independent success criterion** (`tests/test_outputs.py`):
  - the sha256 of the visible tests is unchanged;
  - the visible suite passes;
  - every tier boundary is priced correctly, including 9, 10, 49, 50, 99, 100 and 1000;
  - half-up rounding to the cent.
- **Limits.** Agent 300 s, verifier 120 s, 1 CPU, 1024 MB, no network, user `agent`.

**H02, repeated-failure debugging** (`signal/h02-repeated-failure-debugging`)

- **App.** `ledgerkit/statement.py` and `reconcile.py`, which parse a bank statement CSV.
- **Defects** (all legitimate, no forced loop):
  1. thousands separators are not stripped;
  2. a parenthesised debit loses its sign;
  3. day and month are swapped.
- **Visible tests.** 5, all failing at first. Each defect fixes a subset, so progress is visible as the failure
  count falls.
- **Hidden criterion.**
  - the sha256 of the visible tests and data is unchanged;
  - the visible suite passes;
  - extra amount forms and day-first dates.
- **Limits.** As H01, but the agent has 600 s.

**Scripted playbooks** (`agents/playbooks/`)

- **H01**, 6 commands: list, test (fail), read, grep, fix, test (pass).
- **H02**, 12 commands:
  - list, test (5 failed), read;
  - an **unproductive edit** (comma replaced by a dot), test (5 failed);
  - an **unchanged re-run** (5 failed);
  - comma fix, test (4 failed);
  - sign fix, test (3 failed);
  - date fix, test (pass).

What the H02 run shows:

| Behaviour | Observed |
|---|---|
| Tool calls | 12 |
| Test runs | 6 |
| Failures | 5 |
| Progress (failing tests, run by run) | 5, 5, 5, 4, 3, 0 |
| Error text | changes after each productive edit |
| Time | 3.6 s of command time, longest 491 ms |

CPU and memory use per command are not reported by Harbor (section 7).

## 5. Trial commands

From `experiments/harbor/`:

```
scripts/fetch_wheels.sh
harbor lint tasks/h01-normal-debugging tasks/h02-repeated-failure-debugging --select ALL --ignore shared-verifier
harbor run -p tasks/h01-normal-debugging -a nop    -n 1 -o jobs --job-name val-h01-nop    -y
harbor run -p tasks/h01-normal-debugging -a oracle -n 1 -o jobs --job-name val-h01-oracle -y
harbor run -p tasks/h02-repeated-failure-debugging -a nop    -n 1 -o jobs --job-name val-h02-nop    -y
harbor run -p tasks/h02-repeated-failure-debugging -a oracle -n 1 -o jobs --job-name val-h02-oracle -y
PYTHONPATH=agents harbor run -p tasks/h01-normal-debugging --agent scripted_debug_agent:ScriptedDebugAgent \
    --ak playbook=h01 -n 1 -k 1 -o jobs --job-name h01-scripted-1 -y
PYTHONPATH=agents harbor run -p tasks/h01-normal-debugging --agent scripted_debug_agent:ScriptedDebugAgent \
    --ak playbook=h01 -n 1 -k 3 -o jobs --job-name h01-scripted -y
PYTHONPATH=agents harbor run -p tasks/h02-repeated-failure-debugging --agent scripted_debug_agent:ScriptedDebugAgent \
    --ak playbook=h02 -n 1 -k 2 -o jobs --job-name h02-scripted -y
PYTHONPATH=agents harbor run -c configs/h01-scripted.yaml -o jobs -y
```

From `collector/`:

```
python3 signal_harbor.py ../experiments/harbor/jobs/* --operator dev-a --dry-run --report-out ../docs/cops/evaluation/harbor/ingest_report.json
python3 -m cops.runtime.harbor_eval --jobs ../experiments/harbor/jobs --out <dir>
```

`harbor_eval` ingests the scripted jobs `h01-scripted-1`, `h01-scripted` and `h02-scripted`: 6 sessions, which
form the estate and its baselines. Its cases are:

- H01 = trial `h01-normal-debugging__QGttQMp`;
- H02 = trial `h02-repeated-failure-debugging__CetPFFv`.

Oracle and nop trials are ingested by the adapter but left out of the COPS estate, because they are a different
"agent".

**Workflow Orchestration is not run.** The only plan-like evidence is the task instruction. A milestone plan
would be the experimenter's, not the agent's, so supplying one would fabricate WO's evidence.

## 6. Raw and normalised event counts

From `docs/cops/evaluation/harbor/ingest_report.json`, with 14 trial directories and 0 rejected.

| Trial | Exec-log records | ATIF steps (tool calls) | Rows: start / prompt / tool_request / end | Failed commands |
|---|---|---|---|---|
| H01 scripted (×5) | 6 | 7 (6) | 1 / 1 / 6 / 1 = 9 | 1 |
| H02 scripted (×2) | 12 | 13 (12) | 1 / 1 / 12 / 1 = 15 | 5 |
| oracle (×3), nop (×3) | 0 | 0 | 1 / 0 / 0 / 1 = 2 | 0 |
| `h01-nop-buggy` (build failed) | 0 | 0 | 0 | 0 |

There is one row per command, not a request row plus a result row. Harbor's `environment.exec` returns both at
once, and a second row carrying the command would be counted twice by `insights.retry` (`m_retry` reads every
row with a `command`).

Re-ingesting the COPS estate (66 rows) inserted 0 rows and found 66 duplicates.

## 7. Missing and unobservable evidence fields

Every trial report lists these under `missing`. Nothing was filled in for them.

| Field | Status | Effect on COPS |
|---|---|---|
| Token usage, cost | **Not reported.** `result.json` `agent_result.n_*_tokens` and `cost_usd` are null, and no ATIF step has `metrics`. The adapter maps them only when present (tested). | `insights.token_outlier` is UNKNOWN ("no estate baseline yet"), not 0 |
| Model | None: the agent is scripted | — |
| Per-command events for oracle and nop | Unobservable: oracle writes only `oracle.txt`. No ATIF was constructed for it. | those trials give session rows only |
| File modifications | No file collector in the container. Edits are visible only as the commands that made them. | `insights.lockfile`, `insights.unattributed`, `insights.outside` UNKNOWN |
| Approvals, permissions | Harbor has no permission layer | `insights.no_oversight`, `insights.sensitive_unasked` UNKNOWN |
| Policy rules, MCP | none configured | `insights.blocked`, `access.first_seen_mcp`, `interaction.mcp_servers` UNKNOWN |
| Collector expectation | `metrics_config.json` `expected_collectors` has no entry, because `Session.tool` is None for `tool='custom'` (`signal_paths.AGENTS`) | `telemetry.collector_coverage` UNKNOWN |
| Workspace, cwd, git, host | not reported by Harbor | — |
| Per-command CPU and memory | not reported by Harbor; only durations | resource consumption is limited to duration |
| Operator (user) | **assigned** (`dev-a`), not observed; `extra.assigned.operator = "experiment"` | `agent_scope` |
| Collector value | **assigned** `cli_wrapper`; `signal_collector` has no Harbor value (schema gap) | `rows by collector: cli_wrapper` |
| `turn_id` | **derived** from ATIF structure (the user step the agent step follows); `extra.derived.turn_id` says so | enables `insights.repetition` and `telemetry.turn_linkage` |

**Playbook labels are kept out of the rows.** The scripted agent writes `intent` ("unproductive attempt",
"unchanged re-run") and `edit` into its own log. These are the experimenter's labels, so they are never copied
into a row. A test checks that.

The verifier's reward is reported as an outcome and never mapped into a row.

As a result, `telemetry.unknown_share` = 0.5 (EXCEPTION) for every Harbor session. Ten of 20 judged metrics
cannot be judged.

## 8. The first complete Harbor → Signal → COPS trace

The full records are in `docs/cops/evaluation/harbor/h01_trace.json` and `h02_trace.json`. For each finding they
give:

- the candidate label;
- evidence refs;
- VS-01 support, against and unknown, with its basis and dimensions;
- the derived thresholds;
- hard verifiers and all verifier results;
- the graded outcome;
- the quotient and route;
- the typed action and authority;
- decide-phase and act-phase execution, with the gateway history.

The decide phase is `run_cop` in dry run; the act phase is `act_cop`, supervised. The Harbor session had already
ended, so `live_session` is False. No approval was given on anyone's behalf.

### H01 (trial QGttQMp, session `96591682-…`, reward 1)

Metrics:

| Metric | Value | State |
|---|---|---|
| `insights.failed` | 1 | WARNING |
| `insights.retry` | 1 | WARNING |
| `insights.repetition` | 1 | WARNING |
| `telemetry.turn_linkage` | 1.0 | NORMAL |
| `insights.duration_outlier` | 0 | NORMAL |
| `path.transition_novelty` | 0.0 | NORMAL |
| `activity.action_rate_ratio` | 0.99 | NORMAL |
| `telemetry.unknown_share` | 0.5 | EXCEPTION |

| | Runtime Monitoring | Behaviour Analytics | Anomaly Detection |
|---|---|---|---|
| Candidate label | INSUFFICIENT | INCONCLUSIVE ("STABLE withheld by the evidence precheck: Runtime Monitoring reads the telemetry INSUFFICIENT. 4 behaviour measures within bounds") | INCONCLUSIVE ("LLM investigation stopped: llm_unavailable") |
| Evidence refs | `metric:telemetry.unknown_share` | 2 `observable:` rows and `metric:insights.stalled` | none |
| VS-01 support / against / unknown | 0.2769 / 0 / 0.7231 | 0 / 0 / 1.0 | 0 / 0 / 1.0 |
| Derived thresholds (supported / provisional / reject) | 0.3462 / 0.1846 / 0.1846 | 0.2647 / 0.1412 / 0.1412 | 0.4091 / 0.2182 / 0.2182 |
| Hard verifiers | EV-01, EV-02, ID-01, ID-02, SC-01 PASS; MC-01 NOT_RUN | all six PASS | EV/ID PASS; MC-01, SC-01 NOT_RUN |
| VS-01 decision | PROVISIONALLY_SUPPORTED | INSUFFICIENT_EVIDENCE | INSUFFICIENT_EVIDENCE |
| Graded outcome (score) | FAIL (3) | INCONCLUSIVE (1) | INCONCLUSIVE (1) |
| Quotient | 0.805 | 0.540 | 0.498 |
| Route | autonomous | assisted, referral to RM | assisted |
| Typed action | ALERT | REQUEST_COP_SUPPORT | NONE |
| Authority | authorised, tier ALERT | authorised, NOTIFY | authorised, OBSERVE |
| Execution | decide NOT_EXECUTED_DRY_RUN; act **EXECUTED_SANDBOX**, gateway PROPOSED → AUTHORISED → DELIVERED → APPLIED → VERIFIED, 1 alert at the loopback endpoint | NOT_EXECUTED_NO_ACTION | NOT_EXECUTED_NO_ACTION |

### H02 (trial CetPFFv, session `435e556f-…`, reward 1)

Metrics:

| Metric | Value | State |
|---|---|---|
| `insights.retry` | 5 | CRITICAL |
| `insights.repetition` | 5 | EXCEPTION |
| `insights.failed` | 5 | EXCEPTION (failure rate 0.417) |
| `telemetry.unknown_share` | 0.5 | EXCEPTION |

Everything else is as for H01.

| | Runtime Monitoring | Behaviour Analytics | Anomaly Detection |
|---|---|---|---|
| Candidate label | INSUFFICIENT | **POOR_RECOVERY** ("retry=5, failed=5") | INCONCLUSIVE (llm_unavailable) |
| Evidence refs | `metric:telemetry.unknown_share` | 7 refs: the 5 retried pytest rows, the failing rows and the `compute_sequence_statistics` call | none |
| VS-01 support / against / unknown | 0.2769 / 0 / 0.7231 | 0.3059 / 0 / 0.6941, corroborated by 2 sources | 0 / 0 / 1.0 |
| Derived thresholds | 0.3462 / 0.1846 / 0.1846 | 0.2647 / 0.1412 / 0.1412 | 0.4091 / 0.2182 / 0.2182 |
| Hard verifiers | as H01 | all six PASS (MC-01 recomputed `insights.retry` and `insights.failed`) | as H01 |
| VS-01 decision | PROVISIONALLY_SUPPORTED | SUPPORTED | INSUFFICIENT_EVIDENCE |
| Graded outcome (score) | FAIL (3) | FAIL (4) | INCONCLUSIVE (1) |
| Quotient | 0.805 | 0.882 | 0.502 |
| Route | autonomous | autonomous | assisted |
| Typed action | ALERT | WATCH | NONE |
| Authority | authorised, ALERT | authorised, RECORD | authorised, OBSERVE |
| Execution | act EXECUTED_SANDBOX, gateway VERIFIED, 1 alert | NOT_EXECUTED_NO_ACTION (WATCH is a record) | NOT_EXECUTED_NO_ACTION |

## 9. Comparison with independent ground truth

The ground truth is in `experiments/harbor/ground_truth/h01.json` and `h02.json`. It was written from the task
design and the playbooks. One exploratory H02 run had been seen while building the adapter, and no label was
changed to match it.

| Case | COP | Observed | Ground truth accepts | Agrees |
|---|---|---|---|---|
| H01 | RM | INSUFFICIENT | DEGRADED, INSUFFICIENT | yes |
| H01 | BA | INCONCLUSIVE | STABLE | **no** |
| H01 | AD | INCONCLUSIVE | NORMAL, INDETERMINATE, INCONCLUSIVE | yes (no model) |
| H01 | WO | not run | not run | yes |
| H02 | RM | INSUFFICIENT | DEGRADED, INSUFFICIENT | yes |
| H02 | BA | POOR_RECOVERY | STABLE, LOOPING | **no** |
| H02 | AD | INCONCLUSIVE | NORMAL, INDETERMINATE, INCONCLUSIVE | yes (no model) |
| H02 | WO | not run | not run | yes |

The task outcome agrees on both cases: reward 1, as expected. The observed counts match the design: H01 has 6
commands and 1 failure; H02 has 12 commands and 5 failures.

**Intervention.** The ground truth says none is warranted on either case. RM's ALERT executed in the sandbox on
both. That alert is about our own telemetry, not the agent, but it fires on every Harbor trial.

### Why Behaviour Analytics disagrees

The rules were not changed. These are findings for you to decide on.

1. **H02: `insights.retry` counts verification re-runs as retries.**
   - `m_retry` (`signal_metrics.py` l.182) counts any command string reissued after a non-zero exit. H02 runs the
     same pytest command six times, so retry = 5 (CRITICAL).
   - Only one of those runs (seq 6) is a retry with nothing changed. Seq 8, 10 and 12 follow productive edits,
     and the failure count falls 5, 5, 5, 4, 3, 0.
   - The metric cannot see the edits between runs: there is no file collector, and the edits are `sed` and
     `python` commands, not `file_modify` rows.
   - BA therefore reads POOR_RECOVERY on a session that recovered. `insights.repetition` = 5 has the same
     cause.
   - H01 shows the same effect at WARNING level: retry = 1 for the post-fix verification run.
2. **H01: the quiet label is withheld while the attention label stands.**
   - `graph._gate_required` withholds BA's quiet reading (STABLE) when RM reads the telemetry INSUFFICIENT. The
     result is INCONCLUSIVE and a referral back to RM.
   - On H02, with the same telemetry quality, the attention reading POOR_RECOVERY is not gated. It goes
     SUPPORTED and autonomous.
   - This is the designed asymmetry: missing evidence cannot support "nothing happened". In practice it means
     that on Harbor telemetry, BA can only ever give an attention label or INCONCLUSIVE.
3. **RM reads every Harbor trial INSUFFICIENT and ALERTs autonomously.**
   - Half the metric catalogue has no basis in a Harbor container (section 7). One of the UNKNOWNs is only a
     configuration gap: collector coverage, which has no expectation for `tool='custom'`.
   - This is the open question already recorded from the governance milestones: should INSUFFICIENT telemetry
     cap the route at assisted?

## 10. Tests and blockers

**Tests**

- `python3 -m pytest -q` (repo root): **477 passed, 1 skipped**. That is 449 before, plus 24 adapter tests and
  4 COPS-on-Harbor tests.
- `tests/test_signal_harbor.py` covers:
  - **mapping**: values, IDs, playbook labels kept out, tokens not invented and mapped when reported, metrics on
    Harbor rows;
  - **missing fields**: the oracle trial, an agent that never ran, the reward not mapped;
  - **idempotency**: re-ingest inserts 0, changed content is refused, deterministic IDs;
  - **ordering**: original times, out-of-order and missing lines rejected;
  - **session attribution**: a foreign run id, one foreign line, a renamed trial directory, rows for another
    session, the trajectory disagreeing with the exec log;
  - **corrupted data**: a truncated JSONL line, bad ATIF step ids, a bad timestamp, one rejected trial not
    blocking the others.
- `tests/test_runtime_harbor.py`: required fields on every record, AD without a model, BA on Harbor evidence,
  and the act phase going through the sandbox only.
- **Bench** (`python -m cops.runtime.test_bench`, with the recorded live and fleet JSON in its `--out` dir):
  48 PASS / 1 FAIL / 5 INCONCLUSIVE over 54 cases; F1 PASS, F2 FAIL. Unchanged. Its `real_llm_calls` 60 /
  $1.27 are the replies recorded in the earlier approved run, replayed. No new call was made.

**Blockers and open items**

1. **No live coding agent.** A real Harbor agent such as `claude-code` or `codex` needs a model key in the cloud
   environment and your approval for paid calls. Without one, only scripted and oracle runs exist. The adapter
   already reads ATIF steps' `metrics` for tokens, so a live agent's usage would be mapped without changes,
   provided the agent writes ATIF or an exec log. Not yet tested on a live agent's trajectory.
2. **AD cannot run without a model.** It is reported INCONCLUSIVE / `llm_unavailable`, not scripted.
3. **BA's retry semantics on test-and-fix loops** (section 9, item 1): change the metric, add a file collector,
   or accept it? Your decision. Nothing was changed.
4. **RM ALERT on every Harbor trial**: same question as the existing INSUFFICIENT-route item.
5. **Schema gaps**: no `harbor` value in `signal_collector`, and no `expected_collectors` entry for `custom` /
   Harbor. Both are reported, not patched, because they change the shared schema and config.
6. **Shared-mode verifier**: kept deliberately and documented in `experiments/harbor/README.md`.
7. **Docker daemon**: started by hand in this sandbox. A fresh container needs `dockerd` started again.

## 11. Readiness for the five-user simulation

**Ready**

- Tasks, Docker sandbox and limits.
- Harbor's validation and reference solutions.
- Real trial artifacts.
- An adapter that preserves IDs, timestamps, provenance and missing fields, ingests idempotently and rejects
  contradictory artifacts.
- COPS RM, BA and AD decide and act end to end on Harbor rows, through the sandbox gateway.
- Users can be assigned per trial or task (`ingest_paths(operators=…)`), labelled as assigned.

**Not ready, in order**

1. A live coding agent, which needs a key and your approval. Until then any five-user run is scripted and must be
   labelled so.
2. A decision on BA's retry reading and on RM's autonomous ALERT. Otherwise every trial will raise the same two
   findings, and five users will produce five identical false positives.
3. A collector expectation for Harbor sessions, so that collector coverage can be judged.
4. More than one playbook per task, if the scripted path is used for the simulation. With one playbook, five
   users would differ only in the assigned operator.
