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
