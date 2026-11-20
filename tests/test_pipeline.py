"""Continuous pipeline (cops/runtime/pipeline.py): one session from ingestion to persisted, versioned decisions.

Rows are synthetic (activation_scenario), every COP run is the real graph in dry run, no model, local SQLite."""
import json, os, subprocess, sys

HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(HERE, "..", "collector"))
sys.path.insert(0, HERE)

import pytest

import signal_curate
import signal_metrics
import signal_traces
from cops.runtime import activation_scenario as AS
from cops.runtime import evaluation as E
from cops.runtime import pipeline as PL
from cops.runtime.router import Router
from test_runtime_persistence import sql_columns

ROOT = os.path.join(HERE, "..", "collector")
SID = "dbg-repeat"


def _tick(n):
    i = next(n)
    return f"2026-11-20T{11 + i // 60:02d}:{i % 60:02d}:00+00:00"


def make(tmp_path, name="p.sqlite", clock=None, **kw):
    path = str(tmp_path / name)
    clock = clock or (lambda it=iter(range(10_000)): _tick(it))
    src, sink = PL.LocalObservables(path), PL.LocalSink(path)
    pipe = PL.Pipeline(src, sink, AS.approved_registry(), clock=clock, synthetic=True, **kw)
    return src, sink, pipe, clock


def ingest(src, clock, rows):
    return src.ingest(PL._with_keys(AS.with_tenant(rows)), clock())


def session_parts():
    s = AS.repeated_failures()
    return s[:6], s[6:]


def counts(sink):
    return {t: len(sink.select(t)) for t in PL.PIPELINE_TABLES if t != "pipeline_runs"} | {
        "cop_decisions": len(sink.decisions.select("cop_decisions"))}


@pytest.fixture
def first(tmp_path):
    src, sink, pipe, clock = make(tmp_path)
    head, tail = session_parts()
    ingest(src, clock, AS.debugging_estate() + head)
    rep = pipe.run_once()
    return src, sink, pipe, clock, rep, head, tail


# ---------------------------------------------------------------- late evidence merge

def test_fill_missing_fills_only_missing_fields_and_reports_conflicts():
    stored = {"observable_id": "o", "dedup_key": "k", "collector": "local_hooks", "exit_code": None,
              "tool_duration_ms": 900, "command": "pytest"}
    patch, conflicts = signal_curate.fill_missing(stored, {"collector": "otel", "exit_code": 1, "tool_duration_ms": 950,
                                                           "command": "pytest", "observable_id": "other"})
    assert patch["exit_code"] == 1 and "tool_duration_ms" not in patch and "observable_id" not in patch
    assert patch["contributing"] == ["local_hooks", "otel"] and patch["collectors_agree"] is False
    assert conflicts == [{"observable_id": "o", "dedup_key": "k", "session_id": None, "field": "tool_duration_ms",
                          "kept_value": 900, "kept_source": "local_hooks", "incoming_value": 950,
                          "incoming_source": "otel"}]
    assert signal_curate.fill_missing(stored, {"command": "pytest", "tool_duration_ms": "900"}) == ({}, [])


def test_late_field_revises_the_stored_row_and_keeps_the_established_value(tmp_path):
    src, _, _, clock = make(tmp_path)
    head, _ = session_parts()
    ingest(src, clock, head)
    row = dict(next(r for r in head if r.get("command")))
    out = ingest(src, clock, [{**row, "collector": "otel", "tool_duration_ms": 950, "request_latency_ms": 120}])
    assert out == {"inserted": 0, "filled": 1, "unchanged": 0, "conflicts": 1}
    stored = next(r for r in src.rows() if r["observable_id"] == row["observable_id"])
    assert stored["tool_duration_ms"] == 900 and stored["request_latency_ms"] == 120 and stored["revised_at"]
    assert [c["field"] for c in src.conflicts()] == ["tool_duration_ms"]


# ---------------------------------------------------------------- one session end to end

def test_one_session_flows_to_traces_metrics_decisions_and_versions(first):
    src, sink, pipe, clock, rep, head, _ = first
    assert rep["status"] == "OK" and rep["baseline"]["refreshed"] == "first baseline"
    r = rep["sessions"][SID]
    assert r["status"] == PL.DONE and r["completed"] == {"runtime_monitoring": "SUFFICIENT",
                                                         "anomaly_detection": "INCONCLUSIVE"}
    assert sink.traces("trace_sessions", SID)[0]["steps"] == r["traces"]["steps"] > 0
    ev = sink.select("metric_events", session_id=SID)
    assert ev and {e["baseline_version"] for e in ev} == {rep["baseline"]["version"]}
    assert {e["metric_version"] for e in ev} == {signal_metrics.load_config()["metric_version"]}
    recs = sink.select("cop_activation_records", investigation_id=r["investigation_id"])
    assert {x["transition"] for x in recs} >= {"CANDIDATE", "ACTIVATED", "COMPLETED"}
    dv = sink.select("pipeline_decision_versions", session_id=SID)
    saved = sink.decisions.decisions(session_id=SID)
    assert {d["decision_id"] for d in dv} == {d["decision_id"] for d in saved}
    for d in dv:
        assert d["baseline_version"] == rep["baseline"]["version"] and d["mapping_version"]
        assert d["metrics_config_sha256"] and d["signal_metrics_sha256"] and d["route_key"]
        assert d["policy_version"] is None                        # no policy_store artifact in this world
    assert next(d for d in dv if d["cop"] == "runtime_monitoring")["package_version"]
    assert sink.checkpoint() == rep["watermark"] and sink.session_state(SID)["status"] == PL.DONE


def test_pipeline_decisions_equal_the_router_on_the_same_rows(first):
    """The pipeline hands the COP graph the metric events it computed; the result is what the router
    gets when CaseData computes them itself."""
    src, sink, pipe, clock, rep, head, _ = first
    rows = signal_traces.canonicalize(src.rows())
    at = rep["at"]
    factory = lambda cid, s, _r: E.make_runtime(cid, s, rows, {}, store=None, tenant=AS.S.TENANT,  # noqa: E731
                                                computed_at=at, synthetic=True,
                                                clock=lambda: signal_traces.ts(at))
    own = [r for r in rows if r["session_id"] == SID]
    out = Router(AS.approved_registry(), factory).route(own)[0]
    direct = {c: (x["finding"]["label"], x["finding"]["status"], x["finding"]["verified"])
              for c, x in out["results"].items()}
    piped = {d["cop"]: (d["interpretation"], d["decision_status"], d["verified"])
             for d in sink.decisions.decisions(session_id=SID) if d["run_id"] == f"route:{rep['sessions'][SID]['investigation_id']}"
             or d["run_id"].startswith(f"route:{rep['sessions'][SID]['investigation_id']}:root:")}
    assert direct == piped and len(direct) == 2, (direct, piped)


def test_rerun_without_new_evidence_changes_nothing(first):
    src, sink, pipe, clock, rep, head, _ = first
    before = counts(sink)
    again = pipe.run_once()
    assert again["sessions"][SID]["status"] == PL.UNCHANGED
    assert again["sessions"][SID]["metric_events_written"] == 0
    assert counts(sink) == before


# ---------------------------------------------------------------- late evidence and preservation

def test_late_rows_reevaluate_and_keep_earlier_decisions(first):
    src, sink, pipe, clock, rep, head, tail = first
    earlier = {d["decision_id"]: d for d in sink.decisions.decisions(session_id=SID)}
    ingest(src, clock, tail)
    later = pipe.run_once()
    r = later["sessions"][SID]
    assert r["status"] == PL.DONE and r["supersedes"] == rep["sessions"][SID]["investigation_id"]
    assert r["completed"]["behavior_analytics"] == "POOR_RECOVERY"
    now = {d["decision_id"]: d for d in sink.decisions.decisions(session_id=SID)}
    assert all(now[k] == v for k, v in earlier.items())          # earlier rows untouched
    assert len(now) > len(earlier)
    assert all(later["sessions"][s]["status"] == PL.UNCHANGED for s in later["sessions"] if s != SID)


def test_late_field_on_a_baseline_row_refreshes_the_baseline_and_reevaluates(first):
    src, sink, pipe, clock, rep, head, tail = first
    row = dict(next(r for r in head if r.get("command")))
    ingest(src, clock, [{**row, "collector": "otel", "tool_duration_ms": 950, "request_latency_ms": 120}])
    out = pipe.run_once()
    assert out["baseline"]["refreshed"] == "baseline observables revised"
    assert out["baseline"]["version"] != rep["baseline"]["version"]
    assert out["sessions"][SID]["status"] == PL.DONE
    assert out["sessions"][SID]["investigation_id"] != rep["sessions"][SID]["investigation_id"]
    assert len(sink.select("pipeline_baselines")) == 2


# ---------------------------------------------------------------- failures and retries

def test_a_failed_cop_holds_the_checkpoint_and_the_retry_reuses_its_ids(first):
    src, sink, pipe, clock, rep, head, tail = first
    calls = []

    def flaky(cop):
        if cop == "behavior_analytics" and not calls:
            calls.append(cop)
            raise RuntimeError("transient")
    pipe.run_hook = flaky
    ingest(src, clock, tail)
    held = pipe.run_once()
    assert held["status"] == "HELD" and held["held_by"] == [SID] and sink.checkpoint() == rep["watermark"]
    assert held["sessions"][SID]["status"] == PL.RETRY and held["sessions"][SID]["failed_cops"] == ["behavior_analytics"]
    ids = {d["decision_id"] for d in sink.decisions.decisions(session_id=SID)}
    retry = pipe.run_once()
    assert retry["status"] == "OK" and retry["sessions"][SID]["status"] == PL.DONE
    assert retry["sessions"][SID]["investigation_id"] == held["sessions"][SID]["investigation_id"]
    after = {d["decision_id"] for d in sink.decisions.decisions(session_id=SID)}
    assert ids < after and "behavior_analytics" in retry["sessions"][SID]["completed"]
    inv = retry["sessions"][SID]["investigation_id"]
    dv = [d for d in sink.select("pipeline_decision_versions") if d["investigation_id"] == inv]
    assert len(dv) == len({d["decision_id"] for d in dv})
    assert sink.checkpoint() == retry["watermark"] > rep["watermark"]


def test_a_session_that_keeps_failing_becomes_dead_and_releases_the_checkpoint(first, monkeypatch):
    src, sink, pipe, clock, rep, head, tail = first
    real = signal_traces.build

    def broken(rows):
        if any(r.get("session_id") == SID and r.get("observable_id") == tail[0]["observable_id"] for r in rows):
            raise OSError("store unavailable")
        return real(rows)
    monkeypatch.setattr(signal_traces, "build", broken)
    ingest(src, clock, tail)
    statuses = [pipe.run_once()["sessions"][SID]["status"] for _ in range(3)]
    assert statuses == [PL.FAILED, PL.FAILED, PL.DEAD]
    last = pipe.run_once()
    assert last["status"] == "OK" and last["dead"] == [SID] and sink.checkpoint() > rep["watermark"]
    assert "store unavailable" in sink.session_state(SID)["error"]


def test_mixed_metric_versions_never_reach_a_cop(first, monkeypatch):
    src, sink, pipe, clock, rep, head, tail = first
    real = signal_metrics.compute

    def stale(rows, cfg=None, **kw):
        ev, ss = real(rows, cfg, **kw)
        return [{**e, "metric_version": "2026-01-01.0"} if e["session_id"] == SID and i % 7 == 0 else e
                for i, e in enumerate(ev)], ss
    monkeypatch.setattr(signal_metrics, "compute", stale)
    n = len(sink.decisions.select("cop_decisions"))
    ingest(src, clock, tail)
    out = pipe.run_once()
    assert out["sessions"][SID]["status"] == PL.FAILED and "MixedVersions" in out["sessions"][SID]["error"]
    assert len(sink.decisions.select("cop_decisions")) == n


# ---------------------------------------------------------------- versions

def test_a_definition_change_refreshes_the_baseline_and_keeps_old_version_rows(first):
    src, sink, pipe, clock, rep, head, tail = first
    old = {e["metric_event_id"] for e in sink.select("metric_events", session_id=SID)}
    pipe.cfg = {**pipe.cfg, "metric_version": "2026-11-20.test"}
    out = pipe.run_once()
    assert out["baseline"]["refreshed"] == "metric definitions changed"
    rows = sink.select("metric_events", session_id=SID)
    assert old <= {e["metric_event_id"] for e in rows}
    assert {e["metric_version"] for e in rows} == {"2026-10-05.1", "2026-11-20.test"}
    dv = [d for d in sink.select("pipeline_decision_versions", session_id=SID)
          if d["investigation_id"] == out["sessions"][SID]["investigation_id"]]
    assert dv and {d["metric_version"] for d in dv} == {"2026-11-20.test"}


def test_catalogue_metrics_are_persisted_and_the_registry_keeps_every_entry(first):
    src, sink, pipe, clock, rep, head, tail = first
    reg = json.load(open(os.path.join(ROOT, "metric_registry.json")))["metrics"]
    implemented = {m["id"] for m in reg if m["status"] == "IMPLEMENTED"}
    assert len(implemented) == 76 and len(reg) == 244
    ids = {e["metric_id"] for e in sink.select("metric_events", session_id=SID)}
    assert implemented <= ids
    assert not ({m["id"] for m in reg if m["status"] == "NOT_IMPLEMENTED"} & ids)
    assert set(signal_metrics.METRIC_IDS) <= ids


# ---------------------------------------------------------------- durability and the command line

def test_state_survives_a_restart_and_the_scheduled_command_is_idempotent(first, tmp_path):
    src, sink, pipe, clock, rep, head, tail = first
    ingest(src, clock, tail)
    db = str(tmp_path / "p.sqlite")
    env = {**os.environ, "PYTHONPATH": ROOT}
    runs = [subprocess.run([sys.executable, "-m", "cops.runtime.pipeline", "run", "--db", db, "--once"], cwd=ROOT,
                           env=env, capture_output=True, text=True, timeout=600) for _ in range(2)]
    assert all(r.returncode == 0 for r in runs), runs[0].stderr[-2000:]
    reopened = PL.LocalSink(db)
    assert reopened.session_state(SID)["status"] == PL.DONE
    assert reopened.session_state(SID)["previous_investigation_id"] == rep["sessions"][SID]["investigation_id"]
    assert '"dbg-repeat": {"metric_events_written": 0' in runs[1].stdout and '"UNCHANGED"' in runs[1].stdout


# ---------------------------------------------------------------- Supabase: schema first

def _schema_rows(missing=()):
    return [{"table_name": t, "columns": list(c), "is_unique": True} for t, c in PL.REQUIRED_UNIQUE
            if (t, c) not in missing]


def test_preflight_refuses_a_schema_without_the_unique_keys():
    def get(table, q):
        if table == "pipeline_schema_check":
            return _schema_rows(missing={("observables", ("dedup_key",))})
        return []
    assert PL.verify_schema(get) == ["no unique index on observables(dedup_key)"]

    def no_view(table, q):
        raise OSError("404")
    assert "apply collector/pipeline_schema.sql" in PL.verify_schema(no_view)[0]
    assert PL.verify_schema(lambda t, q: _schema_rows() if t == "pipeline_schema_check" else []) == []


def _columns(tmp_path, name):
    """sql_columns on the file with comments removed (an inline comment would hide the next column)."""
    import re
    p = tmp_path / name
    p.write_text(re.sub(r"--[^\n]*", "", open(os.path.join(ROOT, name)).read()))
    return sql_columns(str(p))


def test_migration_matches_the_tables_the_pipeline_writes(tmp_path):
    cols = _columns(tmp_path, "pipeline_schema.sql")
    for t, spec in PL.PIPELINE_TABLES.items():
        if t == "metric_events":
            continue
        assert cols[t] == [c for c, _ in spec["columns"]], t
    base = _columns(tmp_path, "cops_schema.sql")["metric_events"]
    assert base + cols["metric_events"] == [c for c, _ in PL.METRIC_EVENT_COLUMNS]
    assert cols["observables"] == ["revised_at"]


class FakeSupabase:
    """PostgREST stand-in: select with eq filters, upsert on the conflict key, and the schema view."""

    def __init__(self, observables, schema=True):
        self.tables = {"observables": observables}
        self.schema, self.posts = schema, []

    def get(self, table, q):
        if table == "pipeline_schema_check":
            if not self.schema:
                raise OSError("404")
            return _schema_rows()
        rows = list(self.tables.get(table, {}).values() if isinstance(self.tables.get(table), dict)
                    else self.tables.get(table, []))
        for part in q.split("&"):
            if "=eq." in part:
                k, v = part.split("=eq.")
                rows = [r for r in rows if str(r.get(k)) == v]
        return [json.loads(json.dumps(r, default=str)) for r in rows]

    def post(self, table, rows, key):
        self.posts.append((table, len(rows), key))
        store = self.tables.setdefault(table, {})
        for r in rows:
            store[r[key]] = {**store.get(r[key], {}), **r}
        return len(rows)


def test_a_supabase_run_posts_every_table_once_the_schema_is_verified(tmp_path):
    head, _ = session_parts()
    rows = [{**r, "ingested_at": "2026-11-20T11:00:00+00:00", "revised_at": None}
            for r in PL._with_keys(AS.with_tenant(AS.debugging_estate() + head))]
    fake = FakeSupabase(rows, schema=False)
    clock = lambda it=iter(range(100)): _tick(it)               # noqa: E731
    pipe = PL.Pipeline(PL.SupabaseSource(fake.get), PL.SupabaseSink(fake.get, fake.post, str(tmp_path / "l.sqlite")),
                       AS.approved_registry(), clock=clock, synthetic=True)
    with pytest.raises(PL.SchemaNotVerified):
        pipe.run_once()
    assert fake.posts == []
    fake.schema = True
    rep = pipe.run_once()
    assert rep["sessions"][SID]["status"] == PL.DONE
    posted = {t for t, _, _ in fake.posts}
    assert posted >= {"metric_events", "trace_sessions", "trace_steps", "cop_decisions", "cop_activation_records",
                      "pipeline_decision_versions", "pipeline_session_state", "pipeline_checkpoints", "pipeline_runs",
                      "pipeline_baselines"}
    assert fake.tables["pipeline_checkpoints"]["observables"]["watermark"] == "2026-11-20T11:00:00+00:00"
    assert pipe.run_once()["sessions"][SID]["status"] == PL.UNCHANGED
