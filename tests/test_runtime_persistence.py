"""B1: the decision payload, the upsert keys and the schema agree, for real."""
import os, re, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "collector"))

import pytest

from cops.runtime import evaluation as E
from cops.runtime.persistence import (TABLES, SQLiteStore, SupabaseRuntimeWriter, decision_row,
                                      decision_id, side_rows)
from cops.models import COPDecision

ROOT = os.path.join(os.path.dirname(__file__), "..", "collector")


def sql_columns(path):
    """table -> [columns] from create-table statements and add-column alters."""
    text = open(path).read()
    out = {}
    for name, body in re.findall(r"create table if not exists (\w+) \((.*?)\n\);", text, re.S):
        cols = []
        for part in re.split(r",\s*(?![^()]*\))", body):
            part = part.strip()
            if not part or part.startswith("--") or part.lower().startswith(("primary key", "unique")):
                continue
            cols.append(part.split()[0])
        out[name] = cols
    for table, col in re.findall(r"alter table (\w+) add column if not exists (\w+)", text):
        out.setdefault(table, []).append(col)
    return out


@pytest.fixture(scope="module")
def result():
    """A real COP result from the graph, not a hand-built dict."""
    rt = E.make_runtime("t-persist", "c02", E._estate(E.spike_session("c02", 9)), {},
                        E.FX.Script({"anomaly_detection": E.ad_spike_steps()}))
    out = E.run(rt, ["runtime_monitoring", "anomaly_detection"])
    return rt, out["results"]["anomaly_detection"]


def test_schema_files_match_tables():
    base = sql_columns(os.path.join(ROOT, "cops_schema.sql"))
    run = sql_columns(os.path.join(ROOT, "cops_runtime_schema.sql"))
    want = [c for c, _ in TABLES["cop_decisions"]["columns"]]
    assert sorted(base["cop_decisions"] + run["cop_decisions"]) == sorted(want)
    for t in ("cop_llm_invocations", "cop_tool_calls", "cop_memory_records", "cop_memory_access_log"):
        assert run[t] == [c for c, _ in TABLES[t]["columns"]], t


def test_upsert_keys_are_primary_keys():
    text = open(os.path.join(ROOT, "cops_schema.sql")).read() + open(os.path.join(ROOT, "cops_runtime_schema.sql")).read()
    for t, spec in TABLES.items():
        assert re.search(rf"{spec['key']}\s+\w+\s+primary key", text), t


def test_decision_row_has_every_column_and_stable_id(result):
    _, r = result
    row = decision_row(r)
    assert set(row) == {c for c, _ in TABLES["cop_decisions"]["columns"]}
    # same uuid5 formula as cops.models.COPDecision, so legacy and runtime rows upsert together
    legacy = COPDecision(cop=r["cop"], cop_version="1", session_id=r["session_id"], run_id=r["run_id"],
                         behavioural_question="")
    assert row["decision_id"] == legacy.decision_id
    assert row["interpretation"] == "ANOMALOUS" and row["verified"] is True
    assert row["llm_calls"] == 3 and row["tool_calls"] == len(r["tool_calls"])


def test_supabase_writer_posts_validated_payloads(result):
    _, r = result
    sent = []
    w = SupabaseRuntimeWriter(lambda table, rows, conflict: sent.append((table, rows, conflict)))
    summary = w.save_result(r)
    tables = {t for t, _, _ in sent}
    assert {"cop_decisions", "cop_llm_invocations", "cop_tool_calls"} <= tables
    for t, rows, key in sent:
        assert key == TABLES[t]["key"]
        assert all(set(x) == {c for c, _ in TABLES[t]["columns"]} for x in rows)
    assert summary[0] == ("cop_decisions", 1, "decision_id")


def test_supabase_writer_refuses_unknown_columns_and_needs_a_poster():
    with pytest.raises(ValueError):
        SupabaseRuntimeWriter(None)
    with pytest.raises(ValueError):
        SupabaseRuntimeWriter.payload("cop_tool_calls", [{"tool_call_id": "x", "not_a_column": 1}])


def test_sqlite_round_trip_and_idempotent_upsert(result):
    rt, r = result
    st = SQLiteStore()
    st.save_result(r)
    st.save_result(r)                                      # upsert, not duplicate
    rows = st.select("cop_decisions")
    assert len(rows) == 1 and rows[0]["finding"]["label"] == "ANOMALOUS"
    assert len(st.select("cop_llm_invocations")) == 3
    assert all(x["simulated"] for x in st.select("cop_llm_invocations"))
