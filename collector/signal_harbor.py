#!/usr/bin/env python3
"""
Signal Collect: Harbor trial importer.

Reads the artifacts one Harbor trial leaves on disk and turns what was actually
observed into Signal observable rows (the shape of observables_schema.sql), so
the metric layer and COPS read a Harbor trial like any other capture.

    jobs/<job>/<trial>/result.json                 Harbor: trial, job, task, agent, timings, reward
    jobs/<job>/<trial>/config.json                 Harbor: the trial's configuration
    jobs/<job>/<trial>/agent/trajectory.json       the agent's ATIF trajectory, when it writes one
    jobs/<job>/<trial>/agent/signal_exec_log.jsonl the agent's per-command log, when it writes one

What it maps, and from where:

    session_start / session_end   result.json agent_execution.started_at / finished_at
    user_prompt                   the ATIF step whose source is "user" (the task instruction)
    tool_request (one per call)   one exec-log record, cross-checked against the ATIF step that
                                  carries the same tool_call_id: command, exit code, stdout,
                                  stderr, duration, start and end times

One row per command, not a request row and a result row: Harbor's environment.exec
returns the request and its result together, and a second row carrying the same
command would be counted twice by insights.retry.

Nothing is invented. Fields Harbor and the agent did not report stay null and
are listed in the trial report under `missing`, with the reason. Token usage is
mapped only when the agent or Harbor reported it. Three values are assigned
rather than observed, and each row says so in extra.assigned / extra.derived:

    operator      the experiment names the (fictional) user; Harbor has no user
    collector     'cli_wrapper': the signal_collector enum has no Harbor value yet
    turn_id       the ATIF user step the agent step answers (structural, not reported)

Playbook annotations the scripted agent writes (intent, edit) are labels the
experimenter wrote, not observations, and are never copied into a row.

A trial is rejected, not partly ingested, when its artifacts disagree with each
other: unreadable JSON, a run id that differs between the exec log, the ATIF
trajectory and result.json, a trial directory that is not the trial result.json
names, a gap or repeat in the command sequence, or an ATIF step that does not
match its exec-log record.

    python3 signal_harbor.py <trial-or-job-dir>... --operator dev-a --db harbor.sqlite
    python3 signal_harbor.py <dir> --dry-run
"""

import argparse
import hashlib
import json
import os
import sqlite3
import sys
import uuid
from datetime import datetime, timezone

ADAPTER = "signal-harbor"
ADAPTER_VERSION = "1.0.0"
SCHEMA_VERSION = "signal-obs-v1"
NS = uuid.UUID("6f1c2d4e-9a7b-4c3e-8f21-5a0b7d9e3c11")      # namespace for derived observable ids
CLOCK_SKEW_S = 2.0                                          # tolerance for "inside agent execution"
PLAYBOOK_LABELS = ("intent", "edit", "say")                 # experimenter text, never mapped as observed

# observable columns this adapter can fill; everything else in the schema is
# never reported by Harbor and is not listed per trial
COLUMNS_REPORTED = {
    "observable_id", "schema_version", "occurred_at", "sequence_num", "tool", "tool_version",
    "surface", "session_id", "turn_id", "tool_use_id", "request_id", "agent_type",
    "operator_id", "operator_username", "observable_type", "status", "success", "error_type",
    "error_message", "prompt_text", "prompt_length", "model", "input_tokens", "output_tokens",
    "cache_read_tokens", "total_tokens", "cost_usd", "tool_name", "tool_category",
    "tool_arguments", "tool_duration_ms", "command", "exit_code", "stdout", "stderr",
    "output_size", "sandboxed", "collector", "confidence", "attribution", "raw_ref", "extra"}

# what the first Harbor trials cannot show, and why (reported, never filled in)
UNOBSERVABLE = {
    "file_modify": "no file collector inside the Harbor container; edits are only visible as the "
                   "commands that made them",
    "permission_*": "Harbor runs the agent without a permission layer; no approvals exist",
    "workspace/cwd/git_*": "Harbor does not report the agent's working directory or repository",
    "hostname/machine_id": "the container is ephemeral and Harbor does not report it",
    "mcp_*": "no MCP servers were configured for these trials",
}


class HarborIngestError(ValueError):
    """The trial's artifacts are unreadable or contradict each other."""


# ====================================================================== reading

def _read_json(path, what):
    try:
        with open(path) as f:
            return json.load(f)
    except FileNotFoundError:
        return None
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        raise HarborIngestError(f"{what} is not valid JSON ({path}): {e}") from e


def _read_jsonl(path, what):
    if not os.path.exists(path):
        return None
    out = []
    with open(path) as f:
        for n, line in enumerate(f, 1):
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError as e:
                raise HarborIngestError(f"{what} line {n} is not valid JSON ({path}): {e}") from e
            if not isinstance(rec, dict):
                raise HarborIngestError(f"{what} line {n} is not an object ({path})")
            rec["_line"] = n
            out.append(rec)
    return out


def ts(v, what):
    """Parse an ISO-8601 time, keeping the original string. A bad one rejects the trial."""
    if v is None:
        return None
    try:
        return datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except ValueError as e:
        raise HarborIngestError(f"{what}: unparseable timestamp {v!r}") from e


def validate_atif(traj, path):
    """Structural ATIF check: what this adapter relies on. When Harbor's own model is
    importable (the harbor package), the trajectory is validated against it as well."""
    if not isinstance(traj, dict):
        raise HarborIngestError(f"trajectory is not an object ({path})")
    for k in ("schema_version", "session_id", "agent", "steps"):
        if k not in traj:
            raise HarborIngestError(f"trajectory has no {k} ({path})")
    if not str(traj["schema_version"]).startswith("ATIF-"):
        raise HarborIngestError(f"trajectory schema_version {traj['schema_version']!r} is not ATIF")
    ids = [s.get("step_id") for s in traj["steps"]]
    if ids != list(range(1, len(ids) + 1)):
        raise HarborIngestError(f"trajectory step ids are not 1..n in order: {ids[:12]}")
    validator = "structural"
    try:                                                    # optional: Harbor's own pydantic model
        from harbor.models.trajectories import Trajectory    # type: ignore
    except Exception:
        Trajectory = None
    if Trajectory is not None:
        try:
            Trajectory.model_validate(traj)
        except Exception as e:
            raise HarborIngestError(f"trajectory fails Harbor's ATIF model: {e}") from e
        validator = "harbor.models.trajectories.Trajectory"
    return validator


def load_trial(trial_dir):
    trial_dir = os.path.abspath(trial_dir)
    result = _read_json(os.path.join(trial_dir, "result.json"), "result.json")
    if result is None:
        raise HarborIngestError(f"no result.json in {trial_dir}: not a Harbor trial directory")
    for k in ("id", "trial_name", "task_name"):
        if not result.get(k):
            raise HarborIngestError(f"result.json has no {k}")
    if os.path.basename(trial_dir) != result["trial_name"]:
        raise HarborIngestError(f"directory {os.path.basename(trial_dir)!r} is not the trial result.json "
                                f"names ({result['trial_name']!r}): artifacts may have been moved or mixed")
    traj_path = os.path.join(trial_dir, "agent", "trajectory.json")
    traj = _read_json(traj_path, "trajectory.json")
    validator = validate_atif(traj, traj_path) if traj is not None else None
    log = _read_jsonl(os.path.join(trial_dir, "agent", "signal_exec_log.jsonl"), "signal_exec_log.jsonl")
    return {"dir": trial_dir, "result": result,
            "config": _read_json(os.path.join(trial_dir, "config.json"), "config.json"),
            "trajectory": traj, "atif_validator": validator, "exec_log": log,
            "agent_files": sorted(os.listdir(os.path.join(trial_dir, "agent")))
            if os.path.isdir(os.path.join(trial_dir, "agent")) else []}


# ====================================================================== cross-checks

def _check_attribution(t):
    """Every artifact must name the same run. A mismatch means rows would be
    attributed to the wrong session, so the trial is rejected."""
    r, traj, log = t["result"], t["trajectory"], t["exec_log"]
    names = {}
    meta = ((r.get("agent_result") or {}).get("metadata") or {})
    if meta.get("run_id"):
        names["result.json agent_result.metadata.run_id"] = meta["run_id"]
    if traj is not None:
        names["trajectory.json session_id"] = traj["session_id"]
    for rec in log or []:
        names.setdefault("signal_exec_log.jsonl run_id", rec.get("run_id"))
        if rec.get("run_id") != names["signal_exec_log.jsonl run_id"]:
            raise HarborIngestError(f"exec log line {rec['_line']} names run {rec.get('run_id')!r}, "
                                    f"earlier lines {names['signal_exec_log.jsonl run_id']!r}")
    if len(set(names.values())) > 1:
        raise HarborIngestError(f"artifacts name different runs: {names}")
    if traj is not None and r.get("agent_info"):
        a, b = traj["agent"].get("name"), r["agent_info"].get("name")
        if a and b and a != b:
            raise HarborIngestError(f"trajectory agent {a!r} is not result.json agent {b!r}")
    return next(iter(names.values()), None)


def _check_sequence(log):
    seqs = [rec.get("seq") for rec in log]
    if seqs != list(range(1, len(seqs) + 1)):
        raise HarborIngestError(f"exec log sequence is not 1..n without gaps or repeats: {seqs[:20]}")
    ids = [rec.get("call_id") for rec in log]
    if len(set(ids)) != len(ids) or not all(ids):
        raise HarborIngestError("exec log call ids are missing or repeated")
    for rec in log:
        for k in ("command", "started_at", "ended_at"):
            if rec.get(k) in (None, ""):
                raise HarborIngestError(f"exec log line {rec['_line']} has no {k}")
        a, b = ts(rec["started_at"], f"line {rec['_line']} started_at"), ts(rec["ended_at"], f"line {rec['_line']} ended_at")
        if b < a:
            raise HarborIngestError(f"exec log line {rec['_line']} ends before it starts")
        if rec.get("exit_code") is not None and not isinstance(rec["exit_code"], int):
            raise HarborIngestError(f"exec log line {rec['_line']} exit_code {rec['exit_code']!r} is not an integer")


def _atif_calls(traj):
    """tool_call_id -> (step, call, observation result)."""
    out = {}
    for st in (traj or {}).get("steps", []):
        obs = {o.get("source_call_id"): o for o in ((st.get("observation") or {}).get("results") or [])}
        for c in st.get("tool_calls") or []:
            cid = c.get("tool_call_id")
            if cid in out:
                raise HarborIngestError(f"trajectory repeats tool_call_id {cid!r}")
            out[cid] = (st, c, obs.get(cid))
    return out


def _check_against_atif(log, calls):
    if set(calls) != {r["call_id"] for r in log}:
        a, b = sorted(set(calls)), sorted(r["call_id"] for r in log)
        raise HarborIngestError(f"trajectory tool calls {a[:8]} are not the exec log's {b[:8]}")
    for rec in log:
        st, c, o = calls[rec["call_id"]]
        if (c.get("arguments") or {}).get("command") != rec["command"]:
            raise HarborIngestError(f"{rec['call_id']}: trajectory and exec log disagree on the command")
        oe = (o or {}).get("extra") or {}
        if "exit_code" in oe and oe["exit_code"] != rec.get("exit_code"):
            raise HarborIngestError(f"{rec['call_id']}: trajectory exit code {oe['exit_code']!r}, "
                                    f"exec log {rec.get('exit_code')!r}")
        if st.get("timestamp") and st["timestamp"] != rec["started_at"]:
            raise HarborIngestError(f"{rec['call_id']}: trajectory and exec log disagree on the start time")


# ====================================================================== mapping

def _oid(trial_id, kind, key):
    return str(uuid.uuid5(NS, f"harbor:{trial_id}:{kind}:{key}"))


def _sha(row):
    return hashlib.sha256(json.dumps(row, sort_keys=True, default=str).encode()).hexdigest()


def _tokens(step):
    """ATIF step metrics, only when the agent reported them."""
    m = (step or {}).get("metrics") or {}
    got = {k: m.get(src) for k, src in (("input_tokens", "prompt_tokens"), ("output_tokens", "completion_tokens"),
                                         ("cache_read_tokens", "cached_tokens")) if m.get(src) is not None}
    if got:
        got["total_tokens"] = sum(v for k, v in got.items() if k in ("input_tokens", "output_tokens"))
    if m.get("cost_usd") is not None:
        got["cost_usd"] = m["cost_usd"]
    return got


def map_trial(t, operator, tenant=None):
    """Rows and a report for one loaded trial. Raises HarborIngestError on contradictions."""
    r, traj, log = t["result"], t["trajectory"], t["exec_log"]
    run_id = _check_attribution(t)
    if log is not None:
        _check_sequence(log)
        if traj is not None:
            _check_against_atif(log, _atif_calls(traj))
    calls = _atif_calls(traj)

    trial_id = r["id"]
    job_id = ((r.get("config") or {}).get("job_id") or (t["config"] or {}).get("job_id"))
    info = r.get("agent_info") or {}
    ag = (traj or {}).get("agent") or {}
    agent_name, agent_version = info.get("name") or ag.get("name"), info.get("version") or ag.get("version")
    model = ag.get("model_name") or ((info.get("model_info") or {}).get("name") if info.get("model_info") else None)
    scripted = bool((ag.get("extra") or {}).get("scripted") or
                    (((r.get("agent_result") or {}).get("metadata") or {}).get("scripted")))
    # the agent's own session id (ATIF / exec log). Harbor's trial id is kept separately.
    session_id = run_id or f"harbor-trial:{trial_id}"
    ex = r.get("agent_execution") or {}
    win = (ts(ex.get("started_at"), "agent_execution.started_at"), ts(ex.get("finished_at"), "agent_execution.finished_at"))
    rel = lambda p: os.path.relpath(p, os.path.dirname(os.path.dirname(t["dir"])))
    trial_rel = rel(t["dir"])
    harbor = {"job_id": job_id, "trial_id": trial_id, "trial_name": r["trial_name"], "task_name": r["task_name"],
              "task_checksum": r.get("task_checksum"), "agent": {"name": agent_name, "version": agent_version},
              "agent_import": ((r.get("config") or {}).get("agent") or {}).get("name"),
              "environment": ((r.get("config") or {}).get("environment") or {}).get("type")}
    base = {"schema_version": SCHEMA_VERSION, "tool": "custom", "tool_version": f"{agent_name}@{agent_version}",
            "surface": "cli", "session_id": session_id, "request_id": trial_id,
            # agent_id stays null: in Signal it names a subagent lane (signal_traces.build), and the
            # Harbor agent is the main agent. Its identity is kept in extra.harbor.agent and tool_version.
            "agent_type": "scripted" if scripted else "coding_agent",
            "operator_id": operator, "operator_username": operator, "collector": "cli_wrapper",
            "confidence": "parsed", "attribution": "agent", "sandboxed": True}
    assigned = {"operator": "experiment", "collector": "no Harbor value in signal_collector",
                "tool": "custom: Harbor agents are not in signal_paths.AGENTS"}
    rows, notes = [], []
    if not ex.get("started_at"):
        # the agent never ran (the environment failed first): nothing was observed, so no rows
        notes.append("agent did not run: " + str((r.get("exception_info") or {}).get("exception_type")))
        return rows, _report(t, rows, session_id, trial_id, job_id, run_id, scripted, model, False, notes,
                             trial_rel)

    def row(kind, key, at, seq, raw_ref, extra=None, **cols):
        out = {**base, "observable_id": _oid(trial_id, kind, key), "observable_type": kind,
               "occurred_at": at, "sequence_num": seq, "raw_ref": raw_ref,
               "extra": {"harbor": harbor, "adapter": f"{ADAPTER}@{ADAPTER_VERSION}", "assigned": assigned,
                         **({"tenant_id": tenant} if tenant else {}), **(extra or {})}, **cols}
        rows.append(out)
        return out

    row("session_start", "start", ex.get("started_at"), 0, f"{trial_rel}/result.json#agent_execution.started_at",
        status="ok", model=model)

    user_steps = [s for s in (traj or {}).get("steps", []) if s.get("source") == "user"]
    for s in user_steps:
        row("user_prompt", f"atif-step-{s['step_id']}", s.get("timestamp"), 0,
            f"{trial_rel}/agent/trajectory.json#steps[{s['step_id'] - 1}]",
            {"atif_step_id": s["step_id"]}, prompt_text=s.get("message"),
            prompt_length=len(s.get("message") or ""), turn_id=f"{session_id}:step-{s['step_id']}")

    reported_tokens = False
    for rec in log or []:
        st, c, o = calls.get(rec["call_id"], (None, None, None))
        # the turn: the latest user step before this agent step (structural, from ATIF)
        turn = None
        if st is not None:
            prior = [u["step_id"] for u in user_steps if u["step_id"] < st["step_id"]]
            turn = f"{session_id}:step-{prior[-1]}" if prior else None
        rc, err = rec.get("exit_code"), rec.get("exec_error")
        out, serr = rec.get("stdout") or "", rec.get("stderr") or ""
        trunc = bool(rec.get("stdout_truncated") or rec.get("stderr_truncated"))
        if err:
            status, success, etype, emsg = "error", False, "exec_error", err[:500]
        elif rc is None:
            status, success, etype, emsg = None, None, None, None
        elif rc == 0:
            status, success, etype, emsg = "ok", True, None, None
        else:
            status, success, etype = ("timeout", False, "timeout") if rc in (124, 137) else ("error", False, "nonzero_exit")
            emsg = (serr.strip()[-500:] or None)
        tok = _tokens(st)
        reported_tokens |= bool(tok)
        sa = ts(rec["started_at"], "started_at")
        inside = all(w is None for w in win) or (
            (win[0] is None or (sa - win[0]).total_seconds() >= -CLOCK_SKEW_S) and
            (win[1] is None or (win[1] - sa).total_seconds() >= -CLOCK_SKEW_S))
        if not inside:
            notes.append(f"{rec['call_id']} starts outside Harbor's agent_execution window")
        row("tool_request", rec["call_id"], rec["started_at"], rec["seq"],
            f"{trial_rel}/agent/signal_exec_log.jsonl#L{rec['_line']}",
            {"call_id": rec["call_id"], "ended_at": rec["ended_at"], "atif_step_id": (st or {}).get("step_id"),
             "atif_ref": f"{trial_rel}/agent/trajectory.json#steps[{st['step_id'] - 1}]" if st else None,
             "output_truncated": trunc, "inside_agent_execution": inside,
             "derived": ({"turn_id": "the ATIF user step this agent step follows"} if turn else {})},
            tool_use_id=f"{trial_id}:{rec['call_id']}", turn_id=turn, tool_name=rec.get("tool") or "bash",
            tool_category="shell", tool_arguments={"command": rec["command"]},
            tool_duration_ms=rec.get("duration_ms"), command=rec["command"], exit_code=rc,
            stdout=out, stderr=serr, output_size=None if trunc else len(out) + len(serr),
            status=status, success=success, error_type=etype, error_message=emsg, model=model, **tok)

    ar = r.get("agent_result") or {}
    trial_tok = {k: ar.get(src) for k, src in (("input_tokens", "n_input_tokens"), ("output_tokens", "n_output_tokens"),
                                                ("cache_read_tokens", "n_cache_tokens")) if ar.get(src) is not None}
    end_cols = {}
    if trial_tok and not reported_tokens:              # trial-level usage, only when Harbor reported it
        end_cols = {**trial_tok, "total_tokens": trial_tok.get("input_tokens", 0) + trial_tok.get("output_tokens", 0)}
        if ar.get("cost_usd") is not None:
            end_cols["cost_usd"] = ar["cost_usd"]
        reported_tokens = True
    row("session_end", "end", ex.get("finished_at"), (len(log) if log else 0) + 1,
        f"{trial_rel}/result.json#agent_execution.finished_at",
        {"exception": (r.get("exception_info") or {}).get("exception_type") if r.get("exception_info") else None},
        status="error" if r.get("exception_info") else "ok", **end_cols)

    # ordering: rows keep their original timestamps; report anything out of order
    order = [x["occurred_at"] for x in rows if x["observable_type"] == "tool_request"]
    if order != sorted(order):
        notes.append("command start times are not in sequence order; original timestamps kept")

    return rows, _report(t, rows, session_id, trial_id, job_id, run_id, scripted, model,
                         reported_tokens, notes, trial_rel)


def _report(t, rows, session_id, trial_id, job_id, run_id, scripted, model, reported_tokens, notes, trial_rel):
    r, log, traj = t["result"], t["exec_log"], t["trajectory"]
    tool_rows = [x for x in rows if x["observable_type"] == "tool_request"]
    missing = {
        "tokens": None if reported_tokens else "not reported: result.json agent_result token fields are null"
                  + (" and no ATIF step has metrics" if traj else ""),
        "model": None if model else ("no model: the agent is scripted" if scripted else "the agent named no model"),
        "per_command_events": None if log is not None else
            f"unobservable: this agent wrote no per-command log (agent/ holds {t['agent_files']})",
        "trajectory": None if traj is not None else "the agent wrote no ATIF trajectory; none was constructed",
        "turn_id": "derived from ATIF structure, not reported" if traj is not None else "unobservable",
        "operator": "assigned by the experiment; Harbor has no user identity",
        **UNOBSERVABLE,
    }
    raw = {"exec_log_records": len(log or []), "atif_steps": len((traj or {}).get("steps", [])),
           "atif_tool_calls": len(_atif_calls(traj)) if traj else 0, "result_json": 1}
    return {"trial_dir": trial_rel, "trial_id": trial_id, "trial_name": r["trial_name"], "job_id": job_id,
            "task_name": r["task_name"], "session_id": session_id, "agent_run_id": run_id,
            "agent": r.get("agent_info"), "scripted": scripted, "atif_validator": t["atif_validator"],
            "raw_counts": raw,
            "normalised_counts": {k: sum(1 for x in rows if x["observable_type"] == k)
                                  for k in ("session_start", "user_prompt", "tool_request", "session_end")},
            "failed_commands": sum(1 for x in tool_rows if x["success"] is False),
            "missing": {k: v for k, v in missing.items() if v},
            "notes": notes,
            # the verifier's verdict is ground truth for the experiment, never evidence for COPS
            "outcome": {"reward": ((r.get("verifier_result") or {}).get("rewards") or {}).get("reward"),
                        "exception": ({"type": r["exception_info"].get("exception_type"),
                                       "message": str(r["exception_info"].get("exception_message") or "")[:200]}
                                      if r.get("exception_info") else None),
                        "verifier": r.get("verifier")}}


def find_trials(path):
    """A trial directory, or every trial directory in a job directory."""
    path = os.path.abspath(path)
    if os.path.exists(os.path.join(path, "result.json")) and os.path.isdir(os.path.join(path, "agent")):
        return [path]
    return sorted(os.path.join(path, d) for d in os.listdir(path)
                  if os.path.isdir(os.path.join(path, d)) and os.path.exists(os.path.join(path, d, "result.json")))


# ====================================================================== idempotent store

class IngestStore:
    """Insert-once local store for Harbor rows, keyed by the derived observable id.
    Re-ingesting a trial inserts nothing. The same id with different content is a
    conflict and is refused: a stored observation is never overwritten."""

    def __init__(self, path=":memory:"):
        self.db = sqlite3.connect(path)
        self.db.executescript("""
            create table if not exists observables (observable_id text primary key, session_id text not null,
              trial_id text not null, occurred_at text, sequence_num integer, observable_type text not null,
              sha256 text not null, row_json text not null, ingested_at text not null);
            create table if not exists trials (trial_id text primary key, session_id text not null,
              report_json text not null, ingested_at text not null);""")

    def ingest(self, rows, report):
        now = datetime.now(timezone.utc).isoformat()
        sids = {x["session_id"] for x in rows}
        if rows and sids != {report["session_id"]}:
            raise HarborIngestError(f"rows name sessions {sorted(sids)}, the trial is {report['session_id']!r}")
        prior = self.db.execute("select session_id from trials where trial_id=?", (report["trial_id"],)).fetchone()
        if prior and prior[0] != report["session_id"]:
            raise HarborIngestError(f"trial {report['trial_id']} was ingested as session {prior[0]!r}, "
                                    f"now {report['session_id']!r}")
        inserted = dup = 0
        conflicts = []
        for x in rows:
            h = _sha(x)
            got = self.db.execute("select sha256 from observables where observable_id=?", (x["observable_id"],)).fetchone()
            if got is None:
                self.db.execute("insert into observables values (?,?,?,?,?,?,?,?,?)",
                                (x["observable_id"], x["session_id"], report["trial_id"], x["occurred_at"],
                                 x["sequence_num"], x["observable_type"], h, json.dumps(x, default=str), now))
                inserted += 1
            elif got[0] == h:
                dup += 1
            else:
                conflicts.append(x["observable_id"])
        if conflicts:
            self.db.rollback()
            raise HarborIngestError(f"{len(conflicts)} rows already stored with different content "
                                    f"(first {conflicts[0]}); nothing from this ingest was written")
        self.db.execute("insert or ignore into trials values (?,?,?,?)",
                        (report["trial_id"], report["session_id"], json.dumps(report, default=str), now))
        self.db.commit()
        return {"inserted": inserted, "duplicates": dup}

    def rows(self, session_id=None):
        q = "select row_json from observables" + (" where session_id=?" if session_id else "") + \
            " order by session_id, sequence_num, occurred_at"
        return [json.loads(r[0]) for r in self.db.execute(q, (session_id,) if session_id else ())]

    def reports(self):
        return [json.loads(r[0]) for r in self.db.execute("select report_json from trials order by trial_id")]


def ingest_paths(paths, operator, store=None, tenant=None, operators=None):
    """Map every trial under paths into the store. Rejected trials are reported, not ingested.
    operators, if given, maps trial name or task name to an operator (assigned, not observed)."""
    store = store or IngestStore()
    done, rejected = [], []
    for p in paths:
        for d in find_trials(p):
            try:
                t = load_trial(d)
                op = (operators or {}).get(t["result"]["trial_name"]) or \
                     (operators or {}).get(t["result"]["task_name"]) or operator
                rows, rep = map_trial(t, op, tenant)
                rep["ingest"] = store.ingest(rows, rep)
                done.append(rep)
            except HarborIngestError as e:
                rejected.append({"trial_dir": d, "error": str(e)})
    return store, done, rejected


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("paths", nargs="+")
    ap.add_argument("--operator", required=True, help="experiment-assigned user id (Harbor has none)")
    ap.add_argument("--tenant")
    ap.add_argument("--db", default=":memory:")
    ap.add_argument("--rows-out", help="write the normalised rows as JSONL")
    ap.add_argument("--report-out", help="write the per-trial reports as JSON")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    store, done, rejected = ingest_paths(a.paths, a.operator, IngestStore(":memory:" if a.dry_run else a.db), a.tenant)
    for rep in done:
        print(f"{rep['trial_name']:<44} session {rep['session_id'][:13]}  raw exec {rep['raw_counts']['exec_log_records']:>3} "
              f"atif {rep['raw_counts']['atif_steps']:>3}  rows {sum(rep['normalised_counts'].values()):>3}  "
              f"failed {rep['failed_commands']:>2}  inserted {rep['ingest']['inserted']:>3} dup {rep['ingest']['duplicates']:>3}")
    for x in rejected:
        print(f"REJECTED {x['trial_dir']}: {x['error']}")
    if a.rows_out:
        with open(a.rows_out, "w") as f:
            for x in store.rows():
                f.write(json.dumps(x, default=str) + "\n")
    if a.report_out:
        with open(a.report_out, "w") as f:
            json.dump({"trials": done, "rejected": rejected}, f, indent=1, default=str)
    return 1 if rejected else 0


if __name__ == "__main__":
    sys.exit(main())
