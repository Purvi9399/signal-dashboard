"""End-to-end over synthetic observables: rows -> traces -> metrics -> three COPS."""
import os, sys, json
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "collector"))
import pytest
import signal_metrics
from cops.reader import DashboardMetricReader, MemoryBackend
from cops.interpret import RuleInterpreter, LLMInterpreter
from cops.runner import COPRunner
from cops.specs import SPECS, BY_KEY, ACTIVE

OPS = "ops@example.com"

def session(sid, day, tools, tokens=1000, collectors=("local_hooks", "otel"), extra=None, minutes=10, full=True):
    rows, n = [], 0
    def r(**k):
        nonlocal n; n += 1
        base = {"observable_id": f"{sid}-{n}", "session_id": sid, "tool": "claude-code",
                "collector": collectors[0], "contributing": list(collectors),
                "operator_email": OPS, "workspace": "/ws", "turn_id": f"{sid}-t1",
                "occurred_at": f"2026-09-{day:02d}T10:{n % 60:02d}:00Z"}
        base.update(k); rows.append(base)
    r(observable_type="session_start")
    r(observable_type="user_prompt", total_tokens=tokens)
    for i, t in enumerate(tools):
        r(observable_type="tool_request", tool_name=t, tool_use_id=f"{sid}-u{i}",
          tool_arguments={"i": i}, tool_duration_ms=200, success=True)
    if full and len(collectors) > 1:
        # fully instrumented: paths, a cross-checked event, the wrapper, the watcher
        r(observable_type="tool_request", tool_name="Read", file_path="/ws/app.py",
          tool_arguments={"p": "app.py"}, collectors_agree=True, outside_workspace=False)
        r(observable_type="session_start", collector="cli_wrapper")
        r(observable_type="file_modify", collector="fs_watcher", file_path="/ws/app.py",
          content_hash="h1", session_id=sid)
    r(observable_type="session_end", occurred_at=f"2026-09-{day:02d}T10:{minutes:02d}:59Z")
    for k in (extra or []):
        r(**k)
    return rows

NORMAL = ["Read", "Grep", "Read", "Edit", "Bash"]

def estate():
    rows = []
    for d in range(1, 8):
        rows += session(f"norm{d}", d, NORMAL)
    rows += session("spike", 9, ["WebFetch", "Bash", "WebFetch", "Bash", "WebFetch", "Bash"], tokens=40000)
    rows += session("leak", 10, ["WebFetch", "Write", "WebFetch", "Write", "WebFetch", "Write"], tokens=40000,
                    extra=[{"observable_type": "tool_request", "tool_name": "Read",
                            "file_path": "/ws/.env", "sensitivity_tier": 3, "secret_detected": True,
                            "tool_arguments": {"p": ".env"}}])
    rows += session("thin", 11, NORMAL, collectors=("cli_wrapper",))
    rows += session("partial", 12, NORMAL, full=False)
    return rows

@pytest.fixture(scope="module")
def run():
    events, sessions = signal_metrics.compute(estate(), computed_at="2026-10-05T00:00:00Z")
    runner = COPRunner(DashboardMetricReader(MemoryBackend(events, sessions)), RuleInterpreter(), run_id="t")
    return events, {s: runner.run_session(s) for s in ("norm7", "spike", "leak", "thin", "partial")}

def ev(events, sid, mid):
    return next(e for e in events if e["session_id"] == sid and e["metric_id"] == mid)

def test_every_session_gets_every_metric(run):
    events, _ = run
    per = {}
    for e in events:
        per.setdefault(e["session_id"], set()).add(e["metric_id"])
    assert all(len(v) == len(signal_metrics.METRIC_IDS) + 1 for v in per.values())

def test_repetition_compares_arguments(run):
    events, _ = run
    # five tool calls, two Reads in one turn with different arguments: not a repeat
    assert ev(events, "norm1", "insights.repetition")["value"] == 0

def test_missing_basis_is_unknown_not_normal(run):
    events, _ = run
    e = ev(events, "partial", "insights.stalled")       # no terminal wrapper: hangs are invisible
    assert e["state"] == "UNKNOWN" and "basis absent" in e["features"]["reason"]

def test_condition_that_never_arose_is_not_applicable(run):
    events, _ = run
    assert ev(events, "norm1", "telemetry.lane_linkage")["state"] == "NOT_APPLICABLE"   # no subagents
    assert ev(events, "norm1", "insights.retry")["state"] == "NOT_APPLICABLE"           # no commands

def test_approval_visibility_is_a_tool_capability():
    rows = session("cur", 12, NORMAL, extra=[{"observable_type": "tool_request", "tool_name": "Read",
                   "file_path": "/ws/.env", "sensitivity_tier": 3, "tool_arguments": {"p": 1}}])
    for r in rows: r["tool"] = "cursor"
    events, _ = signal_metrics.compute(rows)
    e = ev(events, "cur", "insights.sensitive_unasked")
    assert e["state"] == "UNKNOWN" and e["value"] == 1     # seen, but cannot tell if it was asked

def test_normal_session_is_quiet(run):
    _, out = run
    d = out["norm7"]
    assert d["runtime_monitoring"].interpretation == "SUFFICIENT"
    assert d["anomaly_detection"].interpretation == "NORMAL"
    assert d["incident_response"].action == "NONE"

def test_partial_instrumentation_degrades_but_does_not_alarm(run):
    _, out = run
    d = out["partial"]
    assert d["runtime_monitoring"].interpretation == "DEGRADED"
    assert d["anomaly_detection"].evidence_confidence == 0.6
    assert d["incident_response"].action == "NONE"

def test_spike_is_anomalous_but_not_an_incident(run):
    _, out = run
    d = out["spike"]
    assert d["anomaly_detection"].interpretation == "ANOMALOUS"
    assert "unusual is not prohibited" in d["anomaly_detection"].action_reason
    assert d["incident_response"].interpretation == "WATCH"

def test_novelty_pulls_only_the_lane(run):
    _, out = run
    ctx = out["spike"]["anomaly_detection"].context_used
    assert [c["key"] for c in ctx] == ["lane_path"] and ctx[0]["rows"] < 20

def test_anomaly_plus_secret_escalates(run):
    _, out = run
    d = out["leak"]["incident_response"]
    assert d.action == "ESCALATE" and d.severity == "HIGH"
    assert any(r.startswith("observable:leak-") for r in d.evidence_refs)

def test_thin_evidence_is_insufficient_and_lowers_confidence(run):
    _, out = run
    d = out["thin"]
    assert d["runtime_monitoring"].interpretation == "INSUFFICIENT"
    assert d["anomaly_detection"].evidence_confidence == 0.3
    assert d["incident_response"].action == "WATCH"     # can't see is not nothing happened
    assert "not absence of risk" in d["incident_response"].action_reason

def test_decisions_are_reproducible(run):
    events, sessions = signal_metrics.compute(estate(), computed_at="2026-10-05T00:00:00Z")
    r = COPRunner(DashboardMetricReader(MemoryBackend(events, sessions)), RuleInterpreter(), run_id="t")
    again = r.run_session("leak")
    _, out = run
    for k in again:
        assert again[k].inputs_hash() == out["leak"][k].inputs_hash()
        assert again[k].action == out["leak"][k].action

def test_every_decision_is_logged_with_timing(run):
    _, out = run
    for ds in out.values():
        for d in ds.values():
            row = d.row()
            assert not d.failed and row["evaluation_ms"] >= 0 and row["decision_id"]

def test_specs_only_reference_computed_metrics():
    known = set(signal_metrics.METRIC_IDS) | {"telemetry.unknown_share"}
    for s in SPECS:
        assert set(s.metric_keys) <= known, s.key
        assert s.interpretations and set(s.permitted_actions)
    assert len(SPECS) == 18 and set(ACTIVE) == {"runtime_monitoring", "anomaly_detection", "incident_response"}

def test_reader_refuses_unlisted_context():
    r = DashboardMetricReader(MemoryBackend([], []))
    _, log = r.read_context("s", None, ["all_observables"])
    assert log[0]["error"]

def test_llm_output_is_validated():
    spec = BY_KEY["anomaly_detection"]
    with pytest.raises(ValueError):
        LLMInterpreter.validate(spec, [], {"interpretation": "ANOMALOUS", "action": "BLOCK"})
    with pytest.raises(ValueError):
        LLMInterpreter.validate(spec, [], {"interpretation": "VIOLATION", "action": "WATCH"})


def test_result_and_permission_rows_are_not_repeats():
    rows = session("rep", 13, ["Read"])
    call = next(r for r in rows if r.get("tool_name") == "Read" and r.get("tool_use_id"))
    rows.append({**call, "observable_id": "rep-res", "observable_type": "tool_result"})
    rows.append({**call, "observable_id": "rep-perm", "observable_type": "permission_request"})
    rows.append({**call, "observable_id": "rep-dup", "observable_type": "tool_request"})   # same tool_use_id, re-emitted
    events, _ = signal_metrics.compute(rows)
    assert ev(events, "rep", "insights.repetition")["value"] == 0

def test_true_repeat_is_still_caught():
    rows = session("rep2", 14, [])
    for i in range(3):
        rows.append({"observable_id": f"r{i}", "session_id": "rep2", "tool": "claude-code",
                     "collector": "local_hooks", "observable_type": "tool_request", "turn_id": "T",
                     "tool_name": "Read", "tool_use_id": f"u{i}", "tool_arguments": {"p": "a.py"},
                     "occurred_at": f"2026-09-14T11:00:0{i}Z"})
    events, _ = signal_metrics.compute(rows)
    assert ev(events, "rep2", "insights.repetition")["value"] == 2


def test_command_steps_own_the_files_they_write():
    import signal_traces
    rows = session("inst", 15, [])
    rows.append({"observable_id": "c1", "session_id": "inst", "tool": "claude-code", "collector": "local_hooks",
                 "observable_type": "tool_request", "tool_name": "Bash", "command": "npm install",
                 "tool_use_id": "b1", "cwd": "/ws", "occurred_at": "2026-09-15T10:30:00Z"})
    rows.append({"observable_id": "f1", "collector": "fs_watcher", "observable_type": "file_create",
                 "file_path": "/ws/package-lock.json", "occurred_at": "2026-09-15T10:30:05Z"})
    _, _, fx = signal_traces.build(rows)
    e = next(x for x in fx if x["effect_id"] == "f1")
    assert e["attributed_to"] == "agent" and e["attributed_via"] == "command" and e["session_id"] == "inst"

def test_relative_step_path_matches_absolute_change():
    import signal_traces
    rows = session("rel", 16, [])
    rows.append({"observable_id": "w1", "session_id": "rel", "tool": "claude-code", "collector": "local_hooks",
                 "observable_type": "tool_request", "tool_name": "Write", "file_path": "src/a.py",
                 "tool_use_id": "w", "cwd": "/ws", "occurred_at": "2026-09-16T10:30:00Z"})
    rows.append({"observable_id": "f2", "collector": "fs_watcher", "observable_type": "file_modify",
                 "file_path": "/ws/src/a.py", "occurred_at": "2026-09-16T10:30:02Z"})
    _, _, fx = signal_traces.build(rows)
    assert next(x for x in fx if x["effect_id"] == "f2")["attributed_via"] == "path"

def test_terminal_twin_folds_into_agent_session():
    rows = session("agent1", 17, NORMAL, collectors=("local_hooks",), full=False)
    for r in rows: r["cwd"] = "/ws"
    rows += [{"observable_id": f"w{i}", "session_id": "wrap1", "tool": "claude-code", "collector": "cli_wrapper",
              "observable_type": "command", "command": "claude", "cwd": "/ws",
              "occurred_at": f"2026-09-17T10:0{i}:30Z"} for i in range(3)]
    sessions, extra = signal_metrics.build_sessions(rows, with_unlinked=True)
    assert [s.id for s in sessions] == ["agent1"] and extra["merged"] == {"wrap1": "agent1"}
    assert "cli_wrapper" in sessions[0].collectors

def test_transcript_only_record_is_held_out():
    rows = session("agent2", 18, NORMAL, full=False)
    rows.append({"observable_id": "t1", "session_id": "rollout-2026-agent2", "tool": "codex",
                 "collector": "fs_watcher", "observable_type": "transcript_appended", "total_tokens": 900,
                 "occurred_at": "2026-09-18T10:05:00Z"})
    sessions, extra = signal_metrics.build_sessions(rows, with_unlinked=True)
    assert "rollout-2026-agent2" in extra["unlinked"] and len(sessions) == 1
