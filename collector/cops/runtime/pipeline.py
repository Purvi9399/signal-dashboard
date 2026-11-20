"""
Continuous pipeline: newly ingested observables through traces, metric events and COP evaluations.

    observables whose ingested_at or revised_at is at or after the checkpoint
      -> affected sessions (canonical ids, signal_traces.canonical)
      -> signal_traces.build        the session's trace rows
      -> signal_metrics.compute     over the pinned baseline estate plus the session
      -> metric_events              changed rows only, each with baseline_version and inputs_digest
      -> router.Router.route        the approved activation mapping and the existing COP graph
                                    (evidence, VS-01, triage, authority gate), in dry run
      -> cop_decisions, cop_activation_records, pipeline_decision_versions
      -> pipeline_session_state, pipeline_runs, pipeline_checkpoints

Nothing here calculates a metric, builds a trace or interprets a COP. It decides which sessions to
process, pins the versions they are processed under, and records what happened.

Idempotency: a session's route key is a digest of its rows, its metric events and every definition
version. A session whose key is unchanged since its last completed run is not routed again. A retry
of the same key reuses the same investigation id (router salt), so decision and activation record
ids repeat instead of duplicating. New evidence or a new version gives a new key, a new investigation
and new decision rows; earlier rows are never updated.

The checkpoint advances only when no session in the window failed. Reprocessing a window is safe
because of the route keys, so the window is read inclusively. A session that fails max_attempts times
is DEAD: it no longer holds the checkpoint back, and it is listed in every run report.

    cd collector
    python3 -m cops.runtime.pipeline demo --out DIR          # one session end to end, local SQLite
    python3 -m cops.runtime.pipeline run --db FILE --once    # local source and sink
"""

import argparse, json, os, sqlite3, threading, time
from dataclasses import asdict
from datetime import datetime, timezone

import signal_curate
import signal_metrics
import signal_traces

from . import activation as A
from . import replay as RP
from .contracts import CONTRACT_VERSION, digest, stable_id
from .evaluation import TENANT, make_runtime
from .persistence import SQLiteStore, SupabaseRuntimeWriter
from .router import Router
from .tools import latest_policy
from .verifiers import REGISTRY_VERSION

CHECKPOINT = "observables"
DONE, UNCHANGED, RETRY, FAILED, DEAD = "DONE", "UNCHANGED", "RETRY", "FAILED", "DEAD"
OPEN = "OPEN"

METRIC_EVENT_COLUMNS = [
    ("metric_event_id", "uuid"), ("metric_id", "text"), ("metric_name", "text"), ("session_id", "text"),
    ("trace_id", "text"), ("tool", "text"), ("operator", "text"), ("workspace", "text"), ("value", "numeric"),
    ("threshold", "numeric"), ("direction", "text"), ("thresholds", "jsonb"), ("state", "text"),
    ("features", "jsonb"), ("evidence_refs", "text[]"), ("required_fields", "text[]"), ("basis", "text"),
    ("basis_present", "boolean"), ("source", "text"), ("metric_version", "text"),
    ("session_started_at", "timestamptz"), ("session_ended_at", "timestamptz"), ("computed_at", "timestamptz"),
    ("error", "text"), ("baseline_version", "text"), ("inputs_digest", "text"),
]

PIPELINE_TABLES = {
    "pipeline_checkpoints": {"key": "name", "columns": [
        ("name", "text"), ("watermark", "text"), ("run_id", "text"), ("updated_at", "timestamptz")]},
    "pipeline_runs": {"key": "run_id", "columns": [
        ("run_id", "text"), ("started_at", "timestamptz"), ("finished_at", "timestamptz"), ("status", "text"),
        ("watermark_from", "text"), ("watermark_to", "text"), ("sessions", "jsonb"), ("versions", "jsonb"),
        ("error", "text")]},
    "pipeline_session_state": {"key": "session_id", "columns": [
        ("session_id", "text"), ("status", "text"), ("attempts", "int"), ("inputs_digest", "text"),
        ("route_key", "text"), ("investigation_id", "text"), ("previous_investigation_id", "text"),
        ("baseline_version", "text"), ("metric_version", "text"), ("last_run_id", "text"),
        ("updated_at", "timestamptz"), ("error", "text")]},
    "pipeline_baselines": {"key": "baseline_version", "columns": [
        ("baseline_version", "text"), ("metric_version", "text"), ("metrics_config_sha256", "text"),
        ("signal_metrics_sha256", "text"), ("cut", "text"), ("sessions", "int"), ("observables_digest", "text"),
        ("reason", "text"), ("created_at", "timestamptz")]},
    "pipeline_decision_versions": {"key": "decision_id", "columns": [
        ("decision_id", "text"), ("investigation_id", "text"), ("run_id", "text"), ("case_id", "text"),
        ("session_id", "text"), ("cop", "text"), ("label", "text"), ("status", "text"), ("verified", "boolean"),
        ("route_key", "text"), ("inputs_digest", "text"), ("events_digest", "text"), ("metric_version", "text"),
        ("metrics_config_sha256", "text"), ("signal_metrics_sha256", "text"), ("signal_traces_sha256", "text"),
        ("baseline_version", "text"), ("policy_version", "text"), ("package_version", "text"),
        ("mapping_version", "text"), ("contract_version", "text"), ("verifier_registry", "text"),
        ("supersedes", "text"), ("evaluated_at", "timestamptz")]},
    "observable_conflicts": {"key": "conflict_id", "columns": [
        ("conflict_id", "text"), ("observable_id", "text"), ("dedup_key", "text"), ("session_id", "text"),
        ("field", "text"), ("kept_value", "jsonb"), ("kept_source", "text"), ("incoming_value", "jsonb"),
        ("incoming_source", "text"), ("status", "text"), ("seen_at", "timestamptz")]},
    "cop_activation_records": {"key": "record_id", "columns": [
        (c, "int" if c == "seq" else "jsonb" if c == "body" else "text")
        for c in ("record_id", "seq", "case_id", "investigation_id", "cop", "transition", "mapping_version",
                  "policy_id", "decision_id", "prev_record_id", "at", "body")]},
    "metric_events": {"key": "metric_event_id", "columns": METRIC_EVENT_COLUMNS},
}
TRACE_TABLES = {"trace_sessions": "session_id", "trace_steps": "step_id", "trace_effects": "effect_id"}

# Unique keys the pipeline relies on in Supabase; verify_schema checks each before a run.
REQUIRED_UNIQUE = sorted({("observables", ("dedup_key",)), ("source_events", ("source_ref",)),
                          ("cop_decisions", ("decision_id",))}
                         | {(t, (s["key"],)) for t, s in PIPELINE_TABLES.items()}
                         | {(t, (k,)) for t, k in TRACE_TABLES.items()})
REQUIRED_COLUMNS = {"observables": ("ingested_at", "revised_at"),
                    "metric_events": ("baseline_version", "inputs_digest")}


class SchemaNotVerified(RuntimeError):
    pass


class MixedVersions(RuntimeError):
    pass


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def _mark(r):
    return max(r.get("ingested_at") or "", r.get("revised_at") or "")


# ---------------------------------------------------------------- local tables

class Tables:
    """SQLite tables from a column spec; JSON and array columns stored as JSON text."""

    def __init__(self, path, spec, unique=()):
        self.path, self.spec = path, spec
        self.conn = sqlite3.connect(path, check_same_thread=False, timeout=30)
        self.lock = threading.Lock()
        with self.lock:
            for t, s in spec.items():
                cols = ", ".join(f"{c} TEXT" + (" PRIMARY KEY" if c == s["key"] else "") for c, _ in s["columns"])
                self.conn.execute(f"CREATE TABLE IF NOT EXISTS {t} ({cols})")
            for t, c in unique:
                self.conn.execute(f"CREATE UNIQUE INDEX IF NOT EXISTS {t}_{c}_key ON {t} ({c})")
            self.conn.commit()

    def _enc(self, table, row):
        out = []
        for c, p in self.spec[table]["columns"]:
            v = row.get(c)
            if v is not None and (p in ("jsonb", "text[]", "boolean", "int", "numeric")):
                v = json.dumps(v, default=str, sort_keys=True)
            out.append(v)
        return out

    def _dec(self, table, values):
        return {c: (json.loads(v) if v is not None and p in ("jsonb", "text[]", "boolean", "int", "numeric") else v)
                for (c, p), v in zip(self.spec[table]["columns"], values)}

    def upsert(self, table, rows, ignore=False):
        if not rows:
            return 0
        cols = [c for c, _ in self.spec[table]["columns"]]
        sql = (f"INSERT OR {'IGNORE' if ignore else 'REPLACE'} INTO {table} ({', '.join(cols)}) "
               f"VALUES ({', '.join('?' for _ in cols)})")
        with self.lock:
            cur = self.conn.executemany(sql, [self._enc(table, r) for r in rows])
            self.conn.commit()
        return cur.rowcount

    def select(self, table, **eq):
        cols = [c for c, _ in self.spec[table]["columns"]]
        where = " AND ".join(f"{k} = ?" for k in eq)
        with self.lock:
            cur = self.conn.execute(f"SELECT {', '.join(cols)} FROM {table}" + (f" WHERE {where}" if where else ""),
                                    tuple(eq.values()))
            return [self._dec(table, v) for v in cur.fetchall()]


# ---------------------------------------------------------------- sources

OBSERVABLE_SPEC = {
    "observables": {"key": "observable_id", "columns": [
        ("observable_id", "text"), ("dedup_key", "text"), ("session_id", "text"), ("ingested_at", "text"),
        ("revised_at", "text"), ("body", "jsonb")]},
    "observable_conflicts": PIPELINE_TABLES["observable_conflicts"],
}


class LocalObservables:
    """A local observables store with the curation rules for late evidence (signal_curate.fill_missing).
    Used by the tests and the demonstration; Supabase is read through SupabaseSource."""

    def __init__(self, path):
        self.t = Tables(path, OBSERVABLE_SPEC, unique=(("observables", "dedup_key"),))

    def ingest(self, records, at):
        out = {"inserted": 0, "filled": 0, "unchanged": 0, "conflicts": 0}
        for rec in records:
            key = rec.get("dedup_key")
            if not key:
                raise ValueError("an observable needs a dedup_key (signal_curate.merge_key)")
            have = self.t.select("observables", dedup_key=key)
            if not have:
                oid = rec.get("observable_id") or stable_id("observable", key)
                body = {**rec, "observable_id": oid}
                self.t.upsert("observables", [{"observable_id": oid, "dedup_key": key, "session_id": rec.get("session_id"),
                                               "ingested_at": at, "revised_at": None, "body": body}])
                out["inserted"] += 1
                continue
            row = have[0]
            patch, conflicts = signal_curate.fill_missing(row["body"], rec)
            if conflicts:
                self.t.upsert("observable_conflicts", [
                    {**c, "conflict_id": stable_id("conflict", key, c["field"], digest(c["incoming_value"])),
                     "session_id": c.get("session_id") or row["session_id"], "status": OPEN, "seen_at": at}
                    for c in conflicts], ignore=True)
                out["conflicts"] += len(conflicts)
            new = {k: v for k, v in patch.items() if k not in ("contributing", "collectors_agree")}
            if not new and not conflicts:
                out["unchanged"] += 1
                continue
            self.t.upsert("observables", [{**row, "body": {**row["body"], **patch}, "revised_at": at}])
            out["filled"] += bool(new)
        return out

    def rows(self):
        return [{**r["body"], "ingested_at": r["ingested_at"], "revised_at": r["revised_at"]}
                for r in self.t.select("observables")]

    def conflicts(self):
        return self.t.select("observable_conflicts")


class SupabaseSource:
    """Read-only. get is signal_traces.get (or a test double)."""

    def __init__(self, get):
        self.get = get

    def rows(self):
        return self.get("observables", "select=*&order=occurred_at.asc")


# ---------------------------------------------------------------- sinks

class _Sink:
    def current_baseline(self):
        rows = self.select("pipeline_baselines")
        return max(rows, key=lambda r: (r["created_at"] or "", r["baseline_version"])) if rows else None

    def checkpoint(self):
        r = self.select("pipeline_checkpoints", name=CHECKPOINT)
        return r[0]["watermark"] if r else None

    def set_checkpoint(self, watermark, run_id, at):
        self.upsert("pipeline_checkpoints", [{"name": CHECKPOINT, "watermark": watermark, "run_id": run_id,
                                              "updated_at": at}])

    def session_state(self, sid):
        r = self.select("pipeline_session_state", session_id=sid)
        return r[0] if r else None

    def states(self):
        return self.select("pipeline_session_state")

    def metric_digests(self, sid):
        return {r["metric_event_id"]: _event_digest(r) for r in self.select("metric_events", session_id=sid)}

    def put_traces(self, srows, steps, fx):
        for table, rows in (("trace_sessions", srows), ("trace_steps", steps), ("trace_effects", fx)):
            self.put_trace_rows(table, rows)


class LocalSink(_Sink):
    """Durable local sink: one SQLite file for pipeline tables, traces, metric events and the runtime's
    own tables (decisions, memory) through persistence.SQLiteStore."""

    def __init__(self, path):
        self.path = path
        self.decisions = SQLiteStore(path)
        spec = dict(PIPELINE_TABLES)
        spec.update({t: {"key": k, "columns": [(k, "text")] + ([("session_id", "text")] if k != "session_id" else [])
                         + [("body", "jsonb")]} for t, k in TRACE_TABLES.items()})
        self.t = Tables(path, spec)

    def preflight(self):
        return []

    def upsert(self, table, rows, ignore=False):
        return self.t.upsert(table, rows, ignore)

    def select(self, table, **eq):
        return self.t.select(table, **eq)

    def put_trace_rows(self, table, rows):
        k = TRACE_TABLES[table]
        self.t.upsert(table, [{k: r[k], "session_id": r.get("session_id"), "body": r} for r in rows if r.get(k)])

    def traces(self, table, sid):
        return [r["body"] for r in self.t.select(table, session_id=sid)]


class _MirroredStore:
    """The runtime store for a Supabase run: memory and decisions stay in the local SQLiteStore the runtime
    reads back; every saved decision is also posted through persistence.SupabaseRuntimeWriter."""

    def __init__(self, local, writer):
        self.local, self.writer = local, writer

    def save_result(self, r):
        self.local.save_result(r)
        self.writer.save_result(r)

    def __getattr__(self, name):
        return getattr(self.local, name)


class SupabaseSink(_Sink):
    """get and post are signal_traces.get and signal_traces.post, or test doubles. No run starts until
    verify_schema finds every unique key and column the pipeline relies on."""

    def __init__(self, get, post, local_path):
        self.get, self.post = get, post
        self.decisions = _MirroredStore(SQLiteStore(local_path), SupabaseRuntimeWriter(post))

    def preflight(self):
        problems = verify_schema(self.get)
        if problems:
            raise SchemaNotVerified("; ".join(problems))
        return problems

    def upsert(self, table, rows, ignore=False):
        if rows:
            cols = [c for c, _ in PIPELINE_TABLES[table]["columns"]]
            self.post(table, [{c: r.get(c) for c in cols} for r in rows], PIPELINE_TABLES[table]["key"])
        return len(rows)

    def select(self, table, **eq):
        from urllib.parse import quote
        q = "select=*" + "".join(f"&{k}=eq.{quote(str(v))}" for k, v in eq.items())
        return self.get(table, q)

    def put_trace_rows(self, table, rows):
        k = TRACE_TABLES[table]
        if rows:
            self.post(table, [r for r in rows if r.get(k)], k)


def verify_schema(get):
    """Problems with the Supabase schema the pipeline needs; [] when every unique key and column is there."""
    problems = []
    try:
        idx = get("pipeline_schema_check", "select=table_name,columns,is_unique")
    except Exception as e:                                  # noqa: BLE001 - reported, the run does not start
        return [f"pipeline_schema_check unreadable ({type(e).__name__}): apply collector/pipeline_schema.sql"]
    have = {(r["table_name"], tuple(r["columns"])) for r in idx if r.get("is_unique")}
    problems += [f"no unique index on {t}({', '.join(c)})" for t, c in REQUIRED_UNIQUE if (t, c) not in have]
    for t, cols in REQUIRED_COLUMNS.items():
        try:
            get(t, f"select={','.join(cols)}&limit=1")
        except Exception as e:                              # noqa: BLE001
            problems.append(f"{t} lacks one of {', '.join(cols)} ({type(e).__name__})")
    return problems


# ---------------------------------------------------------------- versions and guards

def _event_digest(e):
    return digest({k: e.get(k) for k in ("metric_id", "value", "state", "features", "evidence_refs",
                                         "metric_version", "baseline_version", "inputs_digest", "error")})


def check_versions(events, metric_version, baseline_version):
    """Every metric event a COP may read carries the current definition and baseline version."""
    bad = sorted({(e.get("metric_version"), e.get("baseline_version")) for e in events}
                 - {(metric_version, baseline_version)}, key=str)
    if bad:
        raise MixedVersions(f"metric events under {bad}; expected ({metric_version}, {baseline_version})")


def catalogue_to_persist():
    """Catalogue metrics the registry marks IMPLEMENTED (Block 2.1). NOT_IMPLEMENTED entries stay in the
    registry with their missing dependencies and produce no rows."""
    with open(os.path.join(os.path.dirname(signal_metrics.__file__), "metric_registry.json")) as f:
        reg = json.load(f)
    built = {m["id"] for m in reg["metrics"] if m["status"] == "IMPLEMENTED"}
    return built & {m[0] for m in signal_metrics.CATALOGUE}


# ---------------------------------------------------------------- the pipeline

class Pipeline:
    def __init__(self, source, sink, registry, cfg=None, clock=now_iso, artifacts=None, synthetic=False,
                 max_attempts=3, baseline_max_age_s=None, persist_catalogue=None, run_hook=None):
        self.source, self.sink, self.registry = source, sink, registry
        self.cfg = cfg or signal_metrics.load_config()
        self.clock, self.artifacts, self.synthetic = clock, artifacts or {}, synthetic
        self.max_attempts, self.max_age = max_attempts, baseline_max_age_s
        self.persist_catalogue = catalogue_to_persist() if persist_catalogue is None else set(persist_catalogue)
        self.catalogue_ids = {m[0] for m in signal_metrics.CATALOGUE}
        self.run_hook = run_hook

    # ------------------------------------------------------------ one run
    def run_once(self, refresh_baseline=False):
        at = self.clock()
        self.sink.preflight()
        bundle = self.registry.active()
        ver = RP.versions(self.cfg, sorted(p.cop for p in bundle.policies))
        wm = self.sink.checkpoint()
        run_id = stable_id("pipeline-run", wm or "", at)
        self.sink.upsert("pipeline_runs", [{"run_id": run_id, "started_at": at, "status": "RUNNING",
                                            "watermark_from": wm, "versions": ver}])
        try:
            report = self._run(at, run_id, wm, ver, bundle.version, refresh_baseline)
        except Exception as e:
            self.sink.upsert("pipeline_runs", [{"run_id": run_id, "started_at": at, "finished_at": self.clock(),
                                                "status": FAILED, "watermark_from": wm, "versions": ver,
                                                "error": f"{type(e).__name__}: {e}"}])
            raise
        self.sink.upsert("pipeline_runs", [{"run_id": run_id, "started_at": at, "finished_at": self.clock(),
                                            "status": report["status"], "watermark_from": wm,
                                            "watermark_to": report["watermark"], "versions": ver,
                                            "sessions": report["sessions"]}])
        return report

    def _run(self, at, run_id, wm, ver, mapping_version, refresh):
        all_rows = signal_traces.canonicalize(self.source.rows())
        top = max([_mark(r) for r in all_rows] + [wm or ""]) or None
        baseline, reason = self._baseline(ver, at, refresh, all_rows)
        if reason:
            affected = {r["session_id"] for r in all_rows if r.get("session_id")}
        else:
            ids = {r["session_id"] for r in all_rows if r.get("session_id") and (not wm or _mark(r) >= wm)}
            affected = ids | {s["session_id"] for s in self.sink.states() if s["status"] in (RETRY, FAILED)}
        shared = {}
        results = {}
        for sid in sorted(affected):
            st = self.sink.session_state(sid)
            try:
                results[sid] = self._session(sid, all_rows, baseline, ver, mapping_version, run_id, at, st, shared)
            except Exception as e:                          # noqa: BLE001 - recorded; the checkpoint holds
                n = ((st or {}).get("attempts") or 0) + 1
                status = DEAD if n >= self.max_attempts else FAILED
                self.sink.upsert("pipeline_session_state", [{**(st or {"session_id": sid}), "status": status,
                                                             "attempts": n, "last_run_id": run_id, "updated_at": at,
                                                             "error": f"{type(e).__name__}: {e}"}])
                results[sid] = {"status": status, "attempts": n, "error": f"{type(e).__name__}: {e}"}
        held = sorted(s for s, r in results.items() if r["status"] in (RETRY, FAILED))
        if not held and top:
            self.sink.set_checkpoint(top, run_id, at)
        dead = sorted(s["session_id"] for s in self.sink.states() if s["status"] == DEAD)
        return {"run_id": run_id, "at": at, "status": "HELD" if held else "OK", "watermark_from": wm,
                "watermark": top if not held else wm, "held_by": held, "dead": dead,
                "baseline": {"version": baseline["baseline_version"], "refreshed": reason},
                "sessions": results}

    # ------------------------------------------------------------ baseline
    def _baseline(self, ver, at, force, all_rows):
        cur = self.sink.current_baseline()
        defs = (ver["metric_version"], ver["metrics_config_sha256"], ver["signal_metrics_sha256"])
        reason = None
        if cur is None:
            reason = "first baseline"
        elif (cur["metric_version"], cur["metrics_config_sha256"], cur["signal_metrics_sha256"]) != defs:
            reason = "metric definitions changed"
        elif force:
            reason = "refresh requested"
        elif self.max_age and _age_s(cur["created_at"], at) > self.max_age:
            reason = f"older than {self.max_age}s"
        elif _baseline_digest(all_rows, cur["cut"]) != cur["observables_digest"]:
            reason = "baseline observables revised"
        if not reason:
            return cur, None
        cut = max([_mark(r) for r in all_rows] or [at])
        odig = _baseline_digest(all_rows, cut)
        bv = stable_id("baseline", *defs, cut, odig)[:18]
        row = {"baseline_version": bv, "metric_version": defs[0], "metrics_config_sha256": defs[1],
               "signal_metrics_sha256": defs[2], "cut": cut, "observables_digest": odig, "reason": reason,
               "sessions": len({r["session_id"] for r in all_rows if r.get("session_id")}), "created_at": at}
        self.sink.upsert("pipeline_baselines", [row])
        return row, reason

    # ------------------------------------------------------------ one session
    def _session(self, sid, all_rows, baseline, ver, mapping_version, run_id, at, st, shared):
        cut, bv = baseline["cut"], baseline["baseline_version"]
        own = [r for r in all_rows if r.get("session_id") == sid]
        if not own:
            return {"status": UNCHANGED, "reason": "no rows for this session"}
        in_base = lambda r: (r.get("ingested_at") or "") <= cut      # noqa: E731
        rows = [r for r in all_rows if r.get("session_id") != sid and in_base(r)] + own
        inputs = digest(sorted((json.dumps(r, sort_keys=True, default=str) for r in own)))
        if all(in_base(r) for r in own):                      # the session is part of the baseline estate
            if "all" not in shared:
                shared["all"] = signal_metrics.compute([r for r in all_rows if in_base(r)], self.cfg,
                                                       computed_at=at, catalogue=True)
            events, built = shared["all"]
            rows = [r for r in all_rows if in_base(r)]
        else:
            events, built = signal_metrics.compute(rows, self.cfg, computed_at=at, catalogue=True)
        mine = [{**e, "baseline_version": bv, "inputs_digest": inputs} for e in events if e["session_id"] == sid]
        check_versions(mine, ver["metric_version"], bv)
        core = [e for e in events if e["metric_id"] not in self.catalogue_ids]
        check_versions([{**e, "baseline_version": bv} for e in core], ver["metric_version"], bv)

        stored = self.sink.metric_digests(sid)
        keep = [e for e in mine if e["metric_id"] not in self.catalogue_ids or e["metric_id"] in self.persist_catalogue]
        changed = [e for e in keep if stored.get(e["metric_event_id"]) != _event_digest(e)]
        self.sink.upsert("metric_events", changed)

        tsessions, steps, fx = signal_traces.build(rows)
        srows = [r for r in signal_traces.session_rows(tsessions, steps, fx) if r["session_id"] == sid]
        self.sink.put_traces(srows, [s for s in steps if s["session_id"] == sid],
                             [e for e in fx if e.get("session_id") == sid])

        events_digest = digest(sorted((e["metric_id"], e["state"], e["value"]) for e in mine))
        route_key = digest({"inputs": inputs, "events": events_digest, "mapping": mapping_version,
                            **{k: v for k, v in ver.items()}})
        base = {"session_id": sid, "inputs_digest": inputs, "route_key": route_key, "baseline_version": bv,
                "metric_version": ver["metric_version"], "last_run_id": run_id, "updated_at": at, "error": None}
        out = {"metric_events_written": len(changed), "metric_events": len(keep),
               "traces": {"sessions": len(srows), "steps": sum(1 for s in steps if s["session_id"] == sid)}}
        if st and st.get("route_key") == route_key and st.get("status") in (DONE, DEAD):
            self.sink.upsert("pipeline_session_state", [{**st, "last_run_id": run_id, "updated_at": at}])
            return {**out, "status": UNCHANGED, "investigation_id": st.get("investigation_id")}

        tenant = next((r.get("tenant_id") for r in own if r.get("tenant_id")), TENANT)
        at_dt = signal_traces.ts(at)

        def factory(case_id, s, _rows):
            return make_runtime(case_id, s, rows, self.artifacts, store=self.sink.decisions, tenant=tenant,
                                cfg=self.cfg, computed_at=at, synthetic=self.synthetic, clock=lambda: at_dt,
                                events=core, sessions=built)

        router = Router(self.registry, factory, version=mapping_version, run_hook=self.run_hook)
        res = router.route([{**r, "tenant_id": tenant} for r in own], salt=route_key)[0]
        recs = res["records"]
        self.sink.upsert("cop_activation_records", [
            {"record_id": r.record_id, "seq": r.seq, "case_id": r.case_id, "investigation_id": r.investigation_id,
             "cop": r.cop, "transition": r.transition, "mapping_version": r.mapping_version, "policy_id": r.policy_id,
             "decision_id": r.decision_id, "prev_record_id": r.prev_record_id, "at": r.at,
             "body": json.loads(json.dumps(asdict(r), default=str))}
            for r in recs], ignore=True)

        policy = latest_policy(res["runtime"].case)
        prev = st.get("investigation_id") if st and st.get("investigation_id") != res["investigation_id"] else \
            (st or {}).get("previous_investigation_id")
        inv = res["investigation_id"]
        saved = self.sink.decisions.select("cop_decisions", "run_id LIKE ? AND session_id = ?", (f"route:{inv}%", sid))
        versions = [{
            "decision_id": d["decision_id"], "investigation_id": inv, "run_id": d["run_id"], "case_id": d["case_id"],
            "session_id": sid, "cop": d["cop"], "label": d["interpretation"], "status": d["decision_status"],
            "verified": d["verified"], "route_key": route_key, "inputs_digest": inputs, "events_digest": events_digest,
            "metric_version": ver["metric_version"], "metrics_config_sha256": ver["metrics_config_sha256"],
            "signal_metrics_sha256": ver["signal_metrics_sha256"], "signal_traces_sha256": ver["signal_traces_sha256"],
            "baseline_version": bv, "policy_version": (policy or {}).get("version"),
            "package_version": (ver["packages"].get(d["cop"]) or {}).get("version"),
            "mapping_version": mapping_version, "contract_version": CONTRACT_VERSION,
            "verifier_registry": REGISTRY_VERSION, "supersedes": prev, "evaluated_at": at}
            for d in sorted(saved, key=lambda d: d["run_id"])]
        self.sink.upsert("pipeline_decision_versions", versions)

        failed = sorted({r.cop for r in recs if r.transition == A.TIMED_OUT
                         or (r.transition == A.SUPPRESSED and any(str(x).startswith("run failed") for x in r.reasons))})
        n = ((st or {}).get("attempts") or 0) + 1 if failed else 0
        status = DONE if not failed else (DEAD if n >= self.max_attempts else RETRY)
        self.sink.upsert("pipeline_session_state", [{**base, "status": status, "attempts": n,
                                                     "investigation_id": res["investigation_id"],
                                                     "previous_investigation_id": prev,
                                                     "error": f"COP runs failed: {failed}" if failed else None}])
        return {**out, "status": status, "investigation_id": res["investigation_id"], "supersedes": prev,
                "completed": {r.cop: dict(r.detail).get("label") for r in recs if r.transition == A.COMPLETED},
                "decisions": [{"cop": v["cop"], "label": v["label"], "status": v["status"], "run_id": v["run_id"],
                               "decision_id": v["decision_id"]} for v in versions],
                "failed_cops": failed, "policy_version": (policy or {}).get("version")}


def _age_s(a, b):
    ta, tb = signal_traces.ts(a), signal_traces.ts(b)
    return (tb - ta).total_seconds() if ta and tb else float("inf")


def _baseline_digest(rows, cut):
    return digest(sorted((r.get("observable_id"), r.get("revised_at")) for r in rows
                         if (r.get("ingested_at") or "") <= cut))


# ---------------------------------------------------------------- demonstration

def _with_keys(rows):
    return [{**r, "dedup_key": r.get("dedup_key") or stable_id("dedup", r["observable_id"])} for r in rows]


def demo(out_dir, path=None):
    """One synthetic session through the pipeline: first ingest, an idle rerun, late rows, a late field
    and a conflict, a failed COP run and its retry. Writes demo.json and returns it."""
    from . import activation_scenario as AS
    os.makedirs(out_dir, exist_ok=True)
    path = path or os.path.join(out_dir, "pipeline_demo.sqlite")
    if os.path.exists(path):
        os.remove(path)
    ticks = iter(f"2026-11-20T11:{m:02d}:00+00:00" for m in range(0, 60))
    clock = lambda: next(ticks)                              # noqa: E731
    src, sink, reg = LocalObservables(path), LocalSink(path), AS.approved_registry()
    pipe = Pipeline(src, sink, reg, clock=clock, synthetic=True)
    steps = []

    def step(name, ingest=None, **kw):
        ing = src.ingest(_with_keys(AS.with_tenant(ingest)), clock()) if ingest else None
        rep = pipe.run_once(**kw)
        steps.append({"step": name, "ingest": ing, "run": rep})
        return rep

    estate = AS.debugging_estate()
    session = AS.repeated_failures()
    head, tail = session[:6], session[6:]
    step("1 estate and the first part of session dbg-repeat arrive", estate + head)
    step("2 nothing new: rerun is idempotent")
    step("3 the rest of the session arrives late", tail)
    late = dict(next(r for r in head if r.get("command")))
    exit_fill = {**late, "collector": "otel", "exit_code": None, "tool_duration_ms": 950, "success": None,
                 "request_latency_ms": 120}
    step("4 a second collector reports an earlier event: one field filled, one conflict", [exit_fill])
    calls = {"n": 0}

    def flaky(cop):
        if cop == "behavior_analytics" and calls["n"] == 0:
            calls["n"] += 1
            raise RuntimeError("simulated transient failure")
    pipe.run_hook = flaky
    step("5 more evidence arrives; behavioural analysis fails once", [{**dict(tail[-1]), "observable_id": "late-x",
                                                                        "dedup_key": None,
                                                                        "occurred_at": tail[-1]["occurred_at"].replace(":00Z", ":30Z")}])
    step("6 scheduled rerun retries the failed COP")
    sid = session[0]["session_id"]
    doc = {"session_id": sid, "simulated": "synthetic rows (activation_scenario), dry run, no model, local SQLite",
           "steps": steps,
           "decisions": sink.select("pipeline_decision_versions", session_id=sid),
           "cop_decisions": [{k: d[k] for k in ("decision_id", "run_id", "cop", "interpretation", "decision_status",
                                                "verified")} for d in sink.decisions.decisions(session_id=sid)],
           "metric_events": len(sink.select("metric_events", session_id=sid)),
           "conflicts": src.conflicts(), "state": sink.session_state(sid),
           "baselines": sink.select("pipeline_baselines"), "checkpoint": sink.checkpoint(),
           "runs": [{k: r[k] for k in ("run_id", "status", "watermark_from", "watermark_to")}
                    for r in sink.select("pipeline_runs")]}
    with open(os.path.join(out_dir, "demo.json"), "w") as f:
        json.dump(doc, f, indent=1, default=str)
    return doc


def main(argv=None):
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("demo")
    d.add_argument("--out", required=True)
    r = sub.add_parser("run")
    r.add_argument("--db", required=True)
    r.add_argument("--once", action="store_true")
    r.add_argument("--interval", type=float)
    r.add_argument("--refresh-baseline", action="store_true")
    r.add_argument("--baseline-max-age", type=float)
    a = ap.parse_args(argv)
    if a.cmd == "demo":
        doc = demo(a.out)
        for s in doc["steps"]:
            run = s["run"]
            print(f"{s['step']}: {run['status']}, baseline {run['baseline']}, "
                  f"{ {k: v['status'] for k, v in run['sessions'].items()} }")
        return
    from . import activation_scenario as AS
    pipe = Pipeline(LocalObservables(a.db), LocalSink(a.db), AS.approved_registry(),
                    baseline_max_age_s=a.baseline_max_age)
    while True:
        print(json.dumps(pipe.run_once(refresh_baseline=a.refresh_baseline), default=str)[:2000])
        if a.once or not a.interval:
            return
        time.sleep(a.interval)


if __name__ == "__main__":
    main()
