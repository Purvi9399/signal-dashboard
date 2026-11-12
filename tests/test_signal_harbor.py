"""Harbor -> Signal adapter (collector/signal_harbor.py) on artifacts from real Harbor trials.

The fixtures in tests/fixtures/harbor/ are copied unchanged from experiments/harbor/jobs/:
two trials of the scripted, model-free agent (H01, H02) and one oracle trial.
Tests cover mapping, idempotent ingestion, ordering, missing fields, session
attribution and corrupted artifacts. Corruptions are made on a temporary copy."""
import json, os, shutil, sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "collector"))

import signal_harbor as H
import signal_metrics as M

FIX = os.path.join(os.path.dirname(__file__), "fixtures", "harbor")
H01 = os.path.join(FIX, "h01-scripted-1", "h01-normal-debugging__QGttQMp")
H02 = os.path.join(FIX, "h02-scripted", "h02-repeated-failure-debugging__CetPFFv")
ORACLE = os.path.join(FIX, "h01-oracle", "h01-normal-debugging__7fbqxbh")


def mapped(d, op="dev-a"):
    return H.map_trial(H.load_trial(d), op)


def lines(d):
    with open(os.path.join(d, "agent", "signal_exec_log.jsonl")) as f:
        return [json.loads(x) for x in f if x.strip()]


@pytest.fixture
def copy_of(tmp_path):
    def make(src):
        dst = tmp_path / os.path.basename(os.path.dirname(src)) / os.path.basename(src)
        shutil.copytree(src, dst)
        return str(dst)
    return make


def rewrite_log(d, fn):
    recs = fn(lines(d))
    with open(os.path.join(d, "agent", "signal_exec_log.jsonl"), "w") as f:
        for r in recs:
            f.write(json.dumps(r) + "\n")


# ------------------------------------------------------------------ mapping

def test_each_command_maps_to_one_row_with_its_original_values():
    rows, rep = mapped(H02)
    log = lines(H02)
    calls = [r for r in rows if r["observable_type"] == "tool_request"]
    assert len(calls) == len(log) == 12
    for row, rec in zip(calls, log):
        assert row["command"] == rec["command"]
        assert row["exit_code"] == rec["exit_code"]
        assert row["occurred_at"] == rec["started_at"]               # original timestamp, untouched
        assert row["extra"]["ended_at"] == rec["ended_at"]
        assert row["tool_duration_ms"] == rec["duration_ms"]
        assert row["stdout"] == rec["stdout"] and row["stderr"] == rec["stderr"]
        assert row["sequence_num"] == rec["seq"]
        assert row["success"] is (rec["exit_code"] == 0)
        assert row["raw_ref"].endswith(f"signal_exec_log.jsonl#L{rec['seq']}")
        assert row["extra"]["call_id"] == rec["call_id"]
    assert rep["failed_commands"] == sum(1 for r in log if r["exit_code"] != 0) == 5
    assert rep["raw_counts"] == {"exec_log_records": 12, "atif_steps": 13, "atif_tool_calls": 12, "result_json": 1}
    assert rep["normalised_counts"] == {"session_start": 1, "user_prompt": 1, "tool_request": 12, "session_end": 1}


def test_trial_session_and_agent_identities_are_kept_apart():
    rows, rep = mapped(H01)
    res = json.load(open(os.path.join(H01, "result.json")))
    traj = json.load(open(os.path.join(H01, "agent", "trajectory.json")))
    assert rep["session_id"] == traj["session_id"] != res["id"]       # agent session is not the trial id
    for r in rows:
        assert r["session_id"] == traj["session_id"]
        assert r["request_id"] == res["id"]
        h = r["extra"]["harbor"]
        assert h["trial_id"] == res["id"] and h["job_id"] == res["config"]["job_id"]
        assert h["agent"] == {"name": "signal-scripted-debug", "version": "1.0.0"}
        assert r["operator_id"] == "dev-a" and r["extra"]["assigned"]["operator"] == "experiment"
        assert r.get("agent_id") is None                             # agent_id names subagent lanes in Signal
    calls = [r for r in rows if r["observable_type"] == "tool_request"]
    assert all(r["tool_use_id"].startswith(res["id"] + ":") for r in calls)


def test_playbook_labels_never_reach_a_row():
    rows, _ = mapped(H02)
    assert any(rec.get("intent") for rec in lines(H02))              # the raw log has them
    for r in rows:
        blob = json.dumps(r)
        assert "unproductive attempt" not in blob and "unchanged re-run" not in blob
        for k in H.PLAYBOOK_LABELS:
            assert k not in r and k not in r["extra"]


def test_tokens_are_not_invented():
    rows, rep = mapped(H02)
    for r in rows:
        for k in ("total_tokens", "input_tokens", "output_tokens", "cost_usd"):
            assert r.get(k) is None
        assert r.get("model") is None
    assert "tokens" in rep["missing"] and "model" in rep["missing"]
    ev, ss = M.compute(rows, computed_at="2026-11-12T00:00:00Z")
    tok = next(e for e in ev if e["metric_id"] == "insights.token_outlier")
    assert tok["value"] is None and tok["state"] == "UNKNOWN"         # unknown, not zero


def test_reported_tokens_are_mapped(copy_of):
    d = copy_of(H01)
    p = os.path.join(d, "result.json")
    res = json.load(open(p))
    res["agent_result"].update(n_input_tokens=1200, n_output_tokens=300, cost_usd=0.01)
    json.dump(res, open(p, "w"))
    rows, rep = mapped(d)
    end = next(r for r in rows if r["observable_type"] == "session_end")
    assert (end["input_tokens"], end["output_tokens"], end["total_tokens"], end["cost_usd"]) == (1200, 300, 1500, 0.01)
    assert "tokens" not in rep["missing"]


def test_metrics_read_harbor_rows():
    rows, _ = mapped(H02)
    ev, ss = M.compute(rows, computed_at="2026-11-12T00:00:00Z")
    by = {e["metric_id"]: e for e in ev}
    assert by["insights.failed"]["value"] == 5
    assert by["insights.retry"]["value"] == 5                          # the same pytest command after a failure
    assert by["telemetry.turn_linkage"]["value"] == 1.0
    assert by["telemetry.lane_linkage"]["state"] == "NOT_APPLICABLE"   # one main lane, no phantom subagent


# ------------------------------------------------------------------ missing and unobservable

def test_oracle_trial_reports_what_it_cannot_show():
    rows, rep = mapped(ORACLE)
    assert [r["observable_type"] for r in rows] == ["session_start", "session_end"]
    assert rep["raw_counts"]["exec_log_records"] == 0 and rep["raw_counts"]["atif_steps"] == 0
    assert "per_command_events" in rep["missing"] and "trajectory" in rep["missing"]
    assert rep["session_id"].startswith("harbor-trial:")
    assert rep["atif_validator"] is None                               # nothing fabricated


def test_verifier_outcome_is_reported_not_mapped():
    rows, rep = mapped(H01)
    assert rep["outcome"]["reward"] == 1.0
    assert not any("reward" in json.dumps(r) for r in rows)            # ground truth never becomes evidence


# ------------------------------------------------------------------ idempotency

def test_reingest_inserts_nothing(tmp_path):
    db = str(tmp_path / "h.sqlite")
    _, done, rej = H.ingest_paths([os.path.dirname(H01), os.path.dirname(H02)], "dev-a", H.IngestStore(db))
    assert not rej and sum(r["ingest"]["inserted"] for r in done) == 9 + 15
    _, again, _ = H.ingest_paths([os.path.dirname(H01), os.path.dirname(H02)], "dev-a", H.IngestStore(db))
    assert sum(r["ingest"]["inserted"] for r in again) == 0
    assert sum(r["ingest"]["duplicates"] for r in again) == 24
    assert len(H.IngestStore(db).rows()) == 24


def test_changed_content_under_a_stored_id_is_refused(tmp_path, copy_of):
    st = H.IngestStore(str(tmp_path / "h.sqlite"))
    rows, rep = mapped(H01)
    st.ingest(rows, rep)
    d = copy_of(H01)
    rewrite_log(d, lambda recs: [{**r, "stdout": r["stdout"] + "x"} if r["seq"] == 3 else r for r in recs])
    rows2, rep2 = mapped(d)
    with pytest.raises(H.HarborIngestError, match="different content"):
        st.ingest(rows2, rep2)
    assert len(st.rows()) == 9                                          # nothing partly written


def test_ids_are_deterministic():
    a, _ = mapped(H01)
    b, _ = mapped(H01)
    assert [r["observable_id"] for r in a] == [r["observable_id"] for r in b]
    assert len({r["observable_id"] for r in a}) == len(a)


# ------------------------------------------------------------------ ordering

def test_rows_are_in_sequence_with_original_times():
    rows, _ = mapped(H02)
    calls = [r for r in rows if r["observable_type"] == "tool_request"]
    assert [r["sequence_num"] for r in calls] == list(range(1, 13))
    assert [r["occurred_at"] for r in calls] == sorted(r["occurred_at"] for r in calls)
    assert rows[0]["observable_type"] == "session_start" and rows[-1]["observable_type"] == "session_end"


def test_out_of_order_lines_are_rejected(copy_of):
    d = copy_of(H02)
    rewrite_log(d, lambda recs: [recs[1], recs[0]] + recs[2:])
    with pytest.raises(H.HarborIngestError, match="sequence"):
        mapped(d)


def test_a_missing_command_record_is_rejected(copy_of):
    d = copy_of(H02)
    rewrite_log(d, lambda recs: recs[:4] + recs[5:])
    with pytest.raises(H.HarborIngestError, match="sequence"):
        mapped(d)


# ------------------------------------------------------------------ session attribution

def test_exec_log_from_another_run_is_rejected(copy_of):
    d = copy_of(H01)
    other = lines(H02)[0]["run_id"]
    rewrite_log(d, lambda recs: [{**r, "run_id": other} for r in recs])
    with pytest.raises(H.HarborIngestError, match="different runs"):
        mapped(d)


def test_one_foreign_line_is_rejected(copy_of):
    d = copy_of(H01)
    rewrite_log(d, lambda recs: recs[:-1] + [{**recs[-1], "run_id": "someone-else"}])
    with pytest.raises(H.HarborIngestError, match="names run"):
        mapped(d)


def test_trial_directory_must_be_the_trial_result_names(tmp_path):
    d = tmp_path / "job" / "h01-normal-debugging__RENAMED"
    shutil.copytree(H01, d)
    with pytest.raises(H.HarborIngestError, match="not the trial"):
        mapped(str(d))


def test_store_refuses_rows_for_another_session(tmp_path):
    st = H.IngestStore(str(tmp_path / "h.sqlite"))
    rows, rep = mapped(H01)
    rows[3] = {**rows[3], "session_id": "wrong"}
    with pytest.raises(H.HarborIngestError, match="sessions"):
        st.ingest(rows, rep)


def test_trajectory_disagreeing_with_exec_log_is_rejected(copy_of):
    d = copy_of(H01)
    rewrite_log(d, lambda recs: [{**r, "exit_code": 0} if r["seq"] == 2 else r for r in recs])
    with pytest.raises(H.HarborIngestError, match="exit code"):
        mapped(d)


# ------------------------------------------------------------------ corrupted data

def test_truncated_exec_log_line_is_rejected(copy_of):
    d = copy_of(H01)
    p = os.path.join(d, "agent", "signal_exec_log.jsonl")
    txt = open(p).read()
    open(p, "w").write(txt[: len(txt) - 40])
    with pytest.raises(H.HarborIngestError, match="not valid JSON"):
        mapped(d)


def test_corrupted_trajectory_is_rejected(copy_of):
    d = copy_of(H01)
    p = os.path.join(d, "agent", "trajectory.json")
    t = json.load(open(p))
    t["steps"][2]["step_id"] = 9
    json.dump(t, open(p, "w"))
    with pytest.raises(H.HarborIngestError, match="step ids"):
        mapped(d)


def test_bad_timestamp_is_rejected(copy_of):
    d = copy_of(H01)
    rewrite_log(d, lambda recs: [{**r, "ended_at": "yesterday"} if r["seq"] == 4 else r for r in recs])
    with pytest.raises(H.HarborIngestError, match="timestamp"):
        mapped(d)


def test_a_rejected_trial_does_not_stop_the_others(tmp_path, copy_of):
    bad = copy_of(H01)
    open(os.path.join(bad, "result.json"), "w").write("{not json")
    _, done, rej = H.ingest_paths([os.path.dirname(bad), os.path.dirname(H02)], "dev-a",
                                  H.IngestStore(str(tmp_path / "h.sqlite")))
    assert len(rej) == 1 and "not valid JSON" in rej[0]["error"]
    assert [r["trial_name"] for r in done] == ["h02-repeated-failure-debugging__CetPFFv"]


def test_a_trial_whose_agent_never_ran_yields_no_rows(tmp_path, copy_of):
    d = copy_of(ORACLE)
    p = os.path.join(d, "result.json")
    res = json.load(open(p))
    res["agent_execution"] = None
    res["exception_info"] = {"exception_type": "RuntimeError", "exception_message": "compose failed"}
    json.dump(res, open(p, "w"))
    rows, rep = mapped(d)
    assert rows == [] and "agent did not run" in rep["notes"][0]
    assert rep["outcome"]["exception"]["type"] == "RuntimeError"
    assert H.IngestStore(str(tmp_path / "h.sqlite")).ingest(rows, rep) == {"inserted": 0, "duplicates": 0}
