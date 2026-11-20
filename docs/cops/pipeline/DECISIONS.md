# Continuous pipeline: decisions

2026-11-20. This is the first vertical slice under purvi's approved direction (10:54Z, decisions 1 to 5). The code is
`collector/cops/runtime/pipeline.py`, the migration is `collector/pipeline_schema.sql` (not applied), and the tests
are in `tests/test_pipeline.py`.

None of this changes a label, a behavioural question, a threshold, a metric definition, the legacy four-COP runner
or a COP package. Every decision below is proposed and awaits purvi's review.

## Made in this slice

| # | Decision | Alternatives considered | Rationale |
|---|---|---|---|
| D-CP1 | The pipeline only orchestrates. It reuses `signal_traces.build` and `session_rows`, `signal_metrics.compute`, `router.Router.route` (and through it the COP graph, VS-01, triage and the authority gate), `evaluation.make_runtime`, `persistence.SQLiteStore` and `SupabaseRuntimeWriter`, and `replay.versions` | an incremental metric engine; per-metric delta functions | your requirement: no second metric engine and no duplicated COP logic. A test checks that the pipeline's decisions equal the router's on the same rows |
| D-CP2 | A session is affected when any of its rows has `ingested_at` or `revised_at` at or after the checkpoint. The window is read inclusively | an exclusive watermark; a Redis queue as the delivery record | ties at the watermark cannot be lost, and reprocessing is idempotent (D-CP5). Redis stays optional (your decision 4) |
| D-CP3 | A baseline is a cut over `ingested_at`, stored in `pipeline_baselines` with a digest of its observables (id, revised_at). A session is computed over the baseline estate (other sessions' rows up to the cut) plus all of its own rows, so its own contribution to medians and counts is what a full run gives it | add an `est=` argument to `compute`; recompute the whole estate every run | no engine change. The engine's baseline code (for example `sessions_by_tool - 1`) assumes the session is in the estate, and this keeps that assumption |
| D-CP4 | Baselines refresh when there is none, when a definition changes (`metric_version`, metrics_config hash or signal_metrics hash), on request, after `baseline_max_age_s` (no default), or when a baseline observable was revised | refresh on a fixed timer only | your decision 1: periodically and after definition changes. A revised baseline row changes the estate, so a decision would otherwise rest on a baseline that no longer exists |
| D-CP5 | Idempotency comes from a route key per session: a digest of its rows, its metric events (id, state, value, catalogue included) and every version (metric, config, engine, traces, packages, mapping, contract, verifiers). An unchanged key is not routed again. The key is passed to the router as `salt` | a dedupe table of observable ids; the router's in-memory `done` map | the key covers late field values and version changes as well as new rows. `Router.route(..., salt=None)` keeps its earlier behaviour, and this is the only router change |
| D-CP6 | The route key leaves out the baseline version, so a refresh reroutes only the sessions whose metric events changed. Every decision still records the baseline it used | reroute every session on each refresh | a refresh would otherwise re-run every COP on every session |
| D-CP7 | COPs get the metric events computed in the same run (`make_runtime(events=...)`); they never read stored `metric_events`. `check_versions` refuses a session whose events carry another metric or baseline version | read `metric_events` back | stale or mixed rows cannot reach a COP. A test shows a mixed set is refused and no decision is written |
| D-CP8 | `metric_event_id` is unchanged (session, metric, metric_version). Within one version a row is rewritten only when its value, state, features, refs, baseline or inputs change; `baseline_version` and `inputs_digest` record what it came from. A new `metric_version` writes new rows and keeps the old ones | put the baseline in the id | an id per baseline grows the table on every refresh. Decisions stay reproducible from their recorded baseline and inputs |
| D-CP9 | Decisions are never updated. New evidence or versions give a new investigation id, so new run ids and new decision ids. `pipeline_decision_versions` records, per decision: route key, inputs and events digests, metric version, config, engine and traces hashes, baseline, policy, package, mapping, contract and verifier versions, and the investigation it supersedes | add version columns to `cop_decisions` | `cop_decisions` is shared with the legacy runner and its schema test. A separate table leaves it unchanged |
| D-CP10 | Late evidence (your decision 2): `signal_curate.fill_missing` fills only missing fields and never overwrites a non-null value. A differing value becomes an `observable_conflicts` row with both values and both collectors, status OPEN. A filled record gets `revised_at`, which makes its session affected again | merge by precedence; overwrite | your decision. Raw payloads already stay in `source_events` |
| D-CP11 | Failures are isolated per session. An exception marks the session FAILED; a COP run that fails or times out marks it RETRY, and the retry reuses the same investigation id. After `max_attempts` (3) the session is DEAD, stops holding the checkpoint, and is listed in every run report. The checkpoint advances only when no session is FAILED or RETRY | retry forever; skip failures silently | a transient failure is retried without duplicate rows; a poison session cannot block the stream. The value 3 is an operational setting, not a governance threshold, and is yours to confirm |
| D-CP12 | Supabase (your decision 3) goes through `SupabaseSource` (read only) and `SupabaseSink`. No run starts until `verify_schema` finds every unique key in the `pipeline_schema_check` view and the new columns. COP memory stays in the local SQLite store the runtime reads back; decisions are mirrored through `SupabaseRuntimeWriter` | trust the comment in `signal_curate.to_supabase` that a UNIQUE index exists | your requirement to verify before relying. Neither the `dedup_key` nor the `source_ref` index is in the repository's SQL |
| D-CP13 | Catalogue (your decision 5): the 76 registry entries marked IMPLEMENTED are written to `metric_events`. The registry keeps all 244 entries. NOT_IMPLEMENTED entries produce no rows and keep their `missing_data` | persist nothing until each metric is approved | I took Block 2.1's acceptance as their approval. It is listed below for you to confirm |
| D-CP14 | The policy version is `tools.latest_policy(case)`, or null when no `policy_store` artifact exists (true of the real data today) | read a version from the finding | it is the same source Policy Enforcement uses |
| D-CP15 | The pipeline needs an approved, active activation mapping. Until the registries are persisted (the evolution step), `run` and `demo` use `activation_scenario.approved_registry` (RM, BA, AD, R&R, approved by a fixed test identity) | build a production registry now | your ordering: make the pipeline reliable first, then evolution persistence |
| D-CP16 | Traces are built over the same rows as the metrics (baseline estate plus session) and filtered to the session | build over the session's rows only | wrapper linking across sessions then behaves as in the batch command |
| D-CP17 | Scheduling is `pipeline run --db FILE --interval S`. No Redis wake-up yet | Redis first | your decision 4 |

## Known limits

- **Possible missed rows.** The watermark has no overlap window. A row committed after a run read the table, but
  stamped with an `ingested_at` earlier than the new checkpoint, would be missed. The fix is an overlap window, which
  is safe because of D-CP5, but its size is a value for you to set.
- **Full reads each run.** Every run reads all observables, as the batch commands do, and recomputes the sessions in
  the last window (inclusive read). That is fine at the size of this slice; a filtered read is the next optimisation.
- **Production curation not wired.** `signal_curate.to_supabase` still skips a duplicate `dedup_key` without
  merging. `fill_missing` is in place for it but not yet used there; this slice exercises it through the local store.
- **Schema not verified live.** `pipeline_schema.sql` is not applied, and the live schema check has not run. Both
  need your approval.

## For purvi's decision

| # | Question |
|---|---|
| U-CP1 | Overlap window for the watermark: how many seconds? |
| U-CP2 | `max_attempts` = 3 before DEAD? |
| U-CP3 | Is Block 2.1's acceptance the approval for persisting the 76 catalogue metrics? |
| U-CP4 | Apply `pipeline_schema.sql` and run a read-only check of the live unique keys? |
| U-CP5 | Wire `fill_missing` into `signal_curate.to_supabase` as the next block? |
| U-CP6 | Periodic baseline refresh interval (none is set)? |
| U-CP7 | Add a `metric_version` filter to `cops/reader.py` for the legacy runner? Not done, because of the legacy-runner rule |
