"""Catalogue metrics in signal_metrics.py: formulas, aggregation and data quality.
Rows are synthetic, shaped like the observables table."""
import copy, os, random, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "collector"))

import pytest

import signal_metrics as M
from cops.runtime import evaluation as E, synthetic as S

OP = "dev1@tenant-a.example"


def row(sid, n, minute, **k):
    base = {"observable_id": f"{sid}-{n}", "session_id": sid, "tool": "claude-code", "collector": "local_hooks",
            "contributing": ["local_hooks"], "operator_email": OP, "workspace": "/ws", "turn_id": f"{sid}-t1",
            "occurred_at": f"2026-10-09T10:{minute:02d}:00Z"}
    base.update(k)
    return base


def call(sid, n, minute, tool="Read", **k):
    k = {"tool_use_id": f"{sid}-u{n}", **k}
    return row(sid, n, minute, observable_type="tool_request", tool_name=tool,
               tool_arguments={"n": n}, tool_duration_ms=200, success=True, **k)


def run(rows, cfg=None):
    events, _ = M.compute(rows, cfg=cfg, computed_at="2026-10-10T00:00:00Z", catalogue=True)
    return {(e["session_id"], e["metric_id"]): e for e in events}


def one(rows, sid, mid, cfg=None):
    return run(rows, cfg)[(sid, mid)]


def basic(sid="s1", **k):
    return [row(sid, 1, 0, observable_type="session_start"),
            row(sid, 2, 1, observable_type="user_prompt", total_tokens=100),
            call(sid, 3, 2), call(sid, 4, 3, tool="Edit", file_path="/ws/a.py"),
            row(sid, 5, 9, observable_type="session_end")]


# ---------------------------------------------------------------- the existing metrics are untouched

def test_existing_metric_events_identical_with_and_without_catalogue():
    rows = E._estate(S.normal_session("c1", 9))
    plain, _ = M.compute(copy.deepcopy(rows), computed_at="t")
    both, _ = M.compute(copy.deepcopy(rows), computed_at="t", catalogue=True)
    assert both[:len(plain)] == plain
    assert {e["metric_id"] for e in both[len(plain):]} == set(M.CATALOGUE_IDS)
    assert not set(M.CATALOGUE_IDS) & set(M.METRIC_IDS + ["telemetry.unknown_share"])


# ---------------------------------------------------------------- formulas and aggregation

def test_plan_progress_and_unresolved_items():
    sid = "p1"
    rows = basic(sid) + [row(sid, 10 + i, 4, observable_type="plan_created", plan_item=f"step {i}") for i in range(3)] \
        + [row(sid, 20 + i, 5, observable_type="plan_completed", plan_item=f"step {i}") for i in range(2)]
    ev = run(rows)
    assert ev[(sid, "task_progress")]["value"] == 0.667
    assert ev[(sid, "unresolved_items")]["value"] == 1
    assert ev[(sid, "unresolved_items")]["features"]["open"] == ["step 2"]


def test_rework_counts_files_changed_in_more_than_one_turn():
    sid = "w1"
    rows = basic(sid) + [call(sid, 10, 4, tool="Edit", file_path="/ws/a.py", turn_id=f"{sid}-t2"),
                         call(sid, 11, 5, tool="Edit", file_path="/ws/b.py", turn_id=f"{sid}-t2")]
    f = one(rows, sid, "workflow_rework_rate")
    assert f["value"] == 0.5 and f["features"]["changed_in_later_turn"] == ["/ws/a.py"]


def test_tokens_are_summed_per_turn_and_compared_turn_on_turn():
    sid = "k1"
    rows = basic(sid) + [row(sid, 10, 4, observable_type="model_request", total_tokens=400, turn_id=f"{sid}-t2"),
                         row(sid, 11, 5, observable_type="model_request", total_tokens=100, turn_id=f"{sid}-t2")]
    ev = run(rows)
    assert ev[(sid, "token_consumption_per_session")]["value"] == 600
    assert ev[(sid, "token_consumption_per_turn")]["value"] == 500
    assert ev[(sid, "token_expansion_per_turn")]["value"] == 5.0


def test_missing_events_are_requests_without_a_result():
    sid = "m1"
    rows = basic(sid) + [row(sid, 10, 4, observable_type="tool_result", tool_use_id=f"{sid}-u3")]
    f = one(rows, sid, "missing_event_rate")
    assert f["value"] == 0.5 and f["evidence_refs"] == [f"observable:{sid}-4"]
    assert one(rows, sid, "audit_missing_event_rate")["value"] == 0.5


def commands(sid, seq):
    return [row(sid, 10 + i, 2 + i, observable_type="shell_request", tool_name="Bash", tool_use_id=f"{sid}-c{i}",
                command=c, exit_code=code, collector="cli_wrapper") for i, (c, code) in enumerate(seq)]


def test_recovery_metrics_follow_each_command():
    sid = "r1"
    rows = basic(sid) + commands(sid, [("pytest", 1), ("pytest", 1), ("pytest", 0), ("make", 1), ("pytest", 0)])
    ev = run(rows)
    assert ev[(sid, "recovery_failure_rate")]["value"] == 0.5
    assert ev[(sid, "recovery_failure_rate")]["features"]["failures_not_retried"] == 1
    assert ev[(sid, "recovery_time")]["value"] == 90.0
    assert ev[(sid, "recovery_retry_recurrence")]["value"] == 1


def test_concurrency_counts_overlapping_steps():
    sid = "c2"
    rows = basic(sid) + [call(sid, 10, 4, tool="Grep", tool_use_id="x1"),
                         call(sid, 11, 5, tool="Grep", tool_use_id="x2"),
                         row(sid, 12, 7, observable_type="tool_result", tool_name="Grep", tool_use_id="x1"),
                         row(sid, 13, 6, observable_type="tool_result", tool_name="Grep", tool_use_id="x2")]
    assert one(rows, sid, "resource_concurrency_level")["value"] == 2


def test_grant_is_linked_only_to_a_later_action_with_its_tool_use_id():
    sid = "g1"
    rows = basic(sid) + [row(sid, 10, 1, observable_type="permission_granted", tool_use_id=f"{sid}-u3"),
                         row(sid, 11, 5, observable_type="permission_granted", tool_use_id="nothing")]
    assert one(rows, sid, "approval_action_linkage")["value"] == 0.5


def test_recovery_after_a_failed_command():
    sid = "r2"
    ev = run(basic(sid) + commands(sid, [("pytest", 1), ("pytest", 0)]))
    assert ev[(sid, "recovery_workflow_resumption_rate")]["value"] == 1.0
    assert ev[(sid, "recovery_stability_duration")]["value"] == 360.0
    assert ev[(sid, "recovery_disruption_count")]["value"] == 1


def test_stall_time_is_summed_within_each_turn():
    sid = "st"
    rows = basic(sid) + [row(sid, 10, 4, hung_seconds=5, collector="cli_wrapper"),
                         row(sid, 11, 5, hung_seconds=7, collector="cli_wrapper"),
                         row(sid, 12, 6, hung_seconds=20, collector="cli_wrapper", turn_id=f"{sid}-t2")]
    f = one(rows, sid, "stalled_duration_per_turn")
    assert f["value"] == 20.0 and f["features"]["per_turn"][f"{sid}-t1"] == 12.0


def test_delegation_tool_share_and_models():
    ev = run(delegated("dl"))
    assert ev[("dl", "excessive_delegation_ratio")]["value"] == 0.2
    assert one(basic("u1"), "u1", "behavior_tool_usage_distribution")["value"] == 0.5
    rows = basic("mo") + [row("mo", 10, 4, model="m-a", total_tokens=10), row("mo", 11, 5, model="m-b", total_tokens=30)]
    ev = run(rows)
    assert ev[("mo", "behavior_model_variation")]["value"] == 2
    assert ev[("mo", "workload_per_model")]["features"]["tokens_by_model"] == {"m-a": 10.0, "m-b": 30.0}


def test_record_integrity_rates():
    sid = "ri"
    rows = basic(sid) + [
        row(sid, 10, 4, captured_at="2026-10-09T10:04:01Z", ingested_at="2026-10-09T10:04:02Z", confidence="inferred"),
        row(sid, 11, 5, captured_at="2026-10-09T10:04:00Z", ingested_at="2026-10-09T10:06:00Z", confidence="reported")]
    ev = run(rows)
    assert ev[(sid, "audit_timestamp_integrity")]["value"] == 0.5
    assert ev[(sid, "audit_unverifiable_record_rate")]["value"] == 0.5


def test_changes_since_the_previous_session():
    prev = [row("pv", 1, 0, observable_type="session_start", permission_mode="default",
                occurred_at="2026-10-01T10:00:00Z"),
            row("pv", 2, 1, observable_type="mcp_request", tool_name="jira_search", mcp_server="jira",
                tool_use_id="pv-m", occurred_at="2026-10-01T10:01:00Z")]
    now = [row("nw", 1, 0, observable_type="session_start", permission_mode="bypassPermissions",
               occurred_at="2026-10-02T10:00:00Z"),
           row("nw", 2, 1, observable_type="mcp_request", tool_name="gh_search", mcp_server="github",
               tool_use_id="nw-m", occurred_at="2026-10-02T10:01:00Z")]
    ev = run(prev + now)
    assert ev[("nw", "agent_permission_drift")]["value"] == 1
    assert ev[("nw", "agent_connectivity_change_rate")]["value"] == 2
    assert ev[("pv", "agent_permission_drift")]["features"]["availability"] == "INSUFFICIENT_HISTORY"


def with_rows(sid, *extra):
    return basic(sid) + [row(sid, 10 + i, 4 + i, **k) for i, k in enumerate(extra)]


def pair(prev, now):
    return [row("pv", 1, 0, **{"observable_type": "session_start", "occurred_at": "2026-10-01T10:00:00Z", **prev}),
            row("nw", 1, 0, **{"observable_type": "session_start", "occurred_at": "2026-10-02T10:00:00Z", **now})]


MCP = {"observable_type": "mcp_request", "tool_name": "jira_search", "mcp_server": "jira"}

CASES = [
    ("tool_timeout_rate", lambda: basic("c") + [call("c", 10, 4, tool="Bash", status="timeout")], "c", 0.333),
    ("compaction_count", lambda: with_rows("c", {"observable_type": "compaction"}, {"observable_type": "compaction"}), "c", 2),
    ("boundary_violation_count", lambda: with_rows("c", {"outside_workspace": True}, {"violation_count": 2}), "c", 2),
    ("restricted_resource_access_rate",
     lambda: basic("c") + [call("c", 10, 4, sensitivity_tier=3), call("c", 11, 5, sensitivity_tier=1)], "c", 0.5),
    ("event_freshness_live", lambda: with_rows("c", {"ingested_at": "2026-10-09T10:04:10Z"},
                                               {"ingested_at": "2026-10-09T10:05:30Z"}), "c", 20.0),
    ("audit_actor_attribution_rate", lambda: with_rows("c", {"operator_email": None}), "c", 0.833),
    ("audit_action_attribution_rate", lambda: with_rows("c", {"attribution": "agent"}, {"attribution": "unknown"}), "c", 0.143),
    ("agent_configuration_change_rate", lambda: with_rows("c", {"observable_type": "permission_mode_change"}), "c", 1),
    ("agent_ownership_attribution", lambda: basic("c"), "c", 1),
    ("agent_ownership_attribution", lambda: [{**r, "operator_email": None} for r in basic("c")], "c", 0),
    ("audit_trace_completeness", lambda: basic("c"), "c", 1.0),
    ("resource_redundant_execution_cost",
     lambda: basic("c") + [{**call("c", 10, 4), "tool_arguments": {"n": 3}}], "c", 200.0),
    ("subagent_resource_consumption",
     lambda: delegated("c") + [row("c", 20, 7, observable_type="model_request", agent_id="sub-a", total_tokens=300)], "c", 300.0),
    ("subagent_resource_overhead",
     lambda: delegated("c") + [row("c", 20, 7, observable_type="model_request", agent_id="sub-a", total_tokens=300)], "c", 0.75),
    ("interaction_route_novelty", lambda: history(5) + current() + [row("now", 9, 0, occurred_at="2026-10-20T10:02:00Z",
                                                                        tool_use_id="now-m", **MCP)], "now", 1.0),
    ("agent_runtime_environment_drift", lambda: pair({"tool_version": "1.0", "hostname": "h1"},
                                                     {"tool_version": "1.1", "hostname": "h1"}), "nw", 1),
    ("agent_tool_inventory_change", lambda: pair({"tool_version": "1.0"}, {"tool_version": "1.1"}), "nw", 1),
    ("agent_machine_change_frequency", lambda: pair({"machine_id": "m1"}, {"machine_id": "m2"}), "nw", 1),
    ("agent_organizational_mapping_consistency", lambda: pair({"org_id": "o", "team_id": "t1"},
                                                              {"org_id": "o", "team_id": "t2"}), "nw", 2),
    ("agent_capability_change_count", lambda: pair({**MCP, "mcp_tools_available": ["a", "b"]},
                                                   {**MCP, "mcp_tools_available": ["b", "c"]}), "nw", 2),
    ("agent_mcp_configuration_drift", lambda: pair({**MCP, "mcp_server_version": "1"},
                                                   {**MCP, "mcp_server_version": "2"}), "nw", 1),
]


@pytest.mark.parametrize("mid,rows,sid,expected", CASES, ids=[f"{c[0]}-{i}" for i, c in enumerate(CASES)])
def test_formula(mid, rows, sid, expected):
    f = one(rows(), sid, mid)
    assert f["value"] == expected, f["features"]


# ---------------------------------------------------------------- zero denominators and missing fields

def test_zero_denominator_is_not_a_rate():
    sid = "z1"
    rows = [row(sid, 1, 0, observable_type="session_start", total_tokens=50),
            row(sid, 2, 0, observable_type="session_end")]
    ev = run(rows)
    assert ev[(sid, "policy_violation_rate")]["state"] == "NOT_APPLICABLE"
    f = ev[(sid, "resource_consumption_rate")]
    assert f["value"] is None and f["state"] == "UNKNOWN"
    assert M.ratio(0, 0) is None


@pytest.mark.parametrize("mid,field", [
    ("cost_per_session", "cost_usd"),
    ("policy_violation_rate", "violation_count"),
    ("audit_source_provenance_completeness", "raw_ref"),
    ("behavior_model_variation", "model"),
    ("audit_timestamp_integrity", "captured_at"),
])
def test_absent_field_is_unknown_not_zero(mid, field):
    f = one(basic("n1"), "n1", mid)
    assert f["value"] is None and f["state"] == "UNKNOWN"
    assert f["features"]["availability"] == "MISSING_DATA" and field in f["features"]["missing"]


def test_null_values_count_as_missing():
    sid = "n2"
    rows = basic(sid) + [row(sid, 10, 4, observable_type="plan_created", plan_item=None),
                         row(sid, 11, 4, cost_usd=None, raw_ref=None, violation_count=None)]
    ev = run(rows)
    assert ev[(sid, "task_progress")]["value"] is None
    assert ev[(sid, "cost_per_session")]["value"] is None


def test_no_catalogue_metric_reports_a_value_without_its_evidence():
    ev = run(E._estate(S.normal_session("e1", 9)))
    for (_, mid), e in ev.items():
        if mid not in M.CATALOGUE_IDS:
            continue
        if e["state"] in ("UNKNOWN", "NOT_APPLICABLE"):
            assert e["value"] is None and e["features"]["availability"] != "CALCULATED", mid
        else:
            assert e["value"] is not None and e["features"]["availability"] == "CALCULATED", mid
        if not e["basis_present"]:
            assert e["value"] is None, mid


def test_unapproved_threshold_is_missing_reference_and_values_are_unjudged():
    f = one(basic("t1"), "t1", "execution_duration_threshold_breach")
    assert f["state"] == "UNKNOWN" and f["features"]["availability"] == "MISSING_REFERENCE"
    assert one(basic("t1"), "t1", "execution_duration_per_session")["state"] == "NO_THRESHOLD"
    cfg = M.load_config()
    cfg["thresholds"]["execution_duration_threshold_breach"] = {"direction": "above", "warning": 60, "exception": 300}
    f = one(basic("t1"), "t1", "execution_duration_threshold_breach", cfg)
    assert f["value"] == 540.0 and f["state"] == "EXCEPTION"


# ---------------------------------------------------------------- duplicates, ordering, attribution

def test_duplicate_rows_are_counted_once():
    sid = "d1"
    rows = basic(sid) + [row(sid, 10, 4, observable_type="permission_denied", tool_use_id="u9", cost_usd=0.5),
                         row(sid, 11, 4, observable_type="permission_denied", tool_use_id="u9")]
    doubled = rows + [dict(r) for r in rows]
    for rs in (rows, doubled):
        ev = run(rs)
        assert ev[(sid, "unauthorized_action_attempts")]["value"] == 1
        assert ev[(sid, "cost_per_session")]["value"] == 0.5
        assert ev[(sid, "token_consumption_per_session")]["value"] == 100


def test_row_order_does_not_change_any_value():
    rows = E._estate(S.normal_session("o1", 9))
    shuffled = rows[:]
    random.Random(7).shuffle(shuffled)
    a, b = run(copy.deepcopy(rows)), run(shuffled)
    for k in a:
        if k[1] in M.CATALOGUE_IDS:
            assert a[k]["value"] == b[k]["value"], k


def test_sequence_against_time_inversion_is_detected():
    sid = "q1"
    rows = basic(sid) + [row(sid, 10, 5, sequence_num=1), row(sid, 11, 6, sequence_num=2),
                         row(sid, 12, 4, sequence_num=3)]
    f = one(rows, sid, "audit_event_ordering_consistency")
    assert f["value"] == 0.5 and f["evidence_refs"] == [f"observable:{sid}-12"]


def delegated(sid, linked=True, depth=None):
    rows = basic(sid)
    if linked:
        rows.append(row(sid, 10, 3, observable_type="subagent_start", agent_id="sub-a"))
    rows += [call(sid, 11, 4, agent_id="sub-a", delegation_depth=depth),
             call(sid, 12, 5, tool="Grep", agent_id="sub-a"),
             row(sid, 13, 6, observable_type="mcp_request", tool_name="jira_search", mcp_server="jira",
                 tool_use_id=f"{sid}-m", agent_id="sub-a")]
    return rows


def test_interaction_graph_from_linked_lanes():
    ev = run(delegated("i1"))
    assert ev[("i1", "interaction_edge_count")]["value"] == 2
    assert ev[("i1", "interaction_propagation_depth")]["value"] == 1
    assert ev[("i1", "interaction_propagation_breadth")]["value"] == 1
    assert ev[("i1", "interaction_graph_completeness")]["value"] == 1.0
    assert ev[("i1", "interaction_orphaned_events")]["value"] == 0


def test_subagent_rows_without_a_parent_are_orphaned():
    ev = run(delegated("i2", linked=False))
    assert ev[("i2", "interaction_orphaned_events")]["value"] == 3
    assert ev[("i2", "interaction_graph_completeness")]["value"] == 0.0
    assert ev[("i2", "interaction_propagation_depth")]["state"] == "UNKNOWN"


def test_rows_from_another_session_are_not_counted():
    rows = basic("a1") + [call("a2", 1, 4, cost_usd=9.0), call("a2", 2, 5, tool="Bash")]
    ev = run(rows)
    assert ev[("a1", "cost_per_session")]["value"] is None
    assert ev[("a1", "interaction_unique_entities")]["features"]["tools"] == 2
    assert ev[("a2", "cost_per_session")]["value"] == 9.0


def test_subagent_tokens_need_a_subagent_on_the_token_row():
    f = run(delegated("i3"))[("i3", "subagent_resource_consumption")]
    assert f["value"] is None and "agent_id" in f["features"]["missing"]


# ---------------------------------------------------------------- conflicting evidence

def test_reported_depth_that_disagrees_with_structure_is_flagged():
    f = run(delegated("x1", depth=3))[("x1", "interaction_propagation_depth")]
    assert f["value"] == 3 and f["features"]["structural"] == 1 and "conflict" in f["features"]


def test_collector_disagreement_is_a_conflicting_record():
    sid = "x2"
    rows = basic(sid) + [row(sid, 10, 4, collectors_agree=False), row(sid, 11, 5, collectors_agree=True),
                         row(sid, 12, 6, collector="otel")]
    assert one(rows, sid, "audit_conflicting_record_rate")["value"] == 0.5


# ---------------------------------------------------------------- history windows and isolation

def history(n, operator=OP, files=("/ws/a.py",)):
    rows = []
    for d in range(n):
        sid = f"h{d}-{operator[:3]}"
        rows += [row(sid, 1, 0, observable_type="session_start", operator_email=operator,
                     occurred_at=f"2026-10-{d + 1:02d}T10:00:00Z")]
        rows += [call(sid, 2 + i, 1, tool="Read", file_path=f, operator_email=operator,
                      occurred_at=f"2026-10-{d + 1:02d}T10:01:0{i}Z") for i, f in enumerate(files)]
    return rows


def current(files=("/ws/a.py", "/ws/new.py"), operator=OP):
    sid = "now"
    return [row(sid, 1, 0, observable_type="session_start", occurred_at="2026-10-20T10:00:00Z", operator_email=operator)] + \
        [call(sid, 2 + i, 1, file_path=f, occurred_at=f"2026-10-20T10:01:0{i}Z", operator_email=operator)
         for i, f in enumerate(files)]


def test_incomplete_history_window_is_insufficient_not_novel():
    need = M.load_config()["min_sessions_for_baseline"]
    f = one(history(need - 1) + current(), "now", "resource_access_novelty_files")
    assert f["value"] is None and f["features"]["availability"] == "INSUFFICIENT_HISTORY"
    f = one(history(need) + current(), "now", "resource_access_novelty_files")
    assert f["value"] == 0.5 and f["features"]["new"] == ["/ws/new.py"]
    t = one(history(need - 1) + current(), "now", "behavior_failure_trend")
    assert t["features"]["availability"] == "INSUFFICIENT_HISTORY"


def test_history_is_only_this_operators_earlier_sessions():
    need = M.load_config()["min_sessions_for_baseline"]
    other = history(need, operator="other@tenant-b.example", files=("/ws/new.py",))
    base = run(history(need) + current())
    mixed = run(history(need) + other + current())
    for mid in M.CATALOGUE_IDS:
        assert base[("now", mid)]["value"] == mixed[("now", mid)]["value"], mid
    later = history(need) + current() + [call("later", 1, 0, file_path="/ws/new.py",
                                               occurred_at="2026-10-25T10:00:00Z")]
    assert run(later)[("now", "resource_access_novelty_files")]["value"] == 0.5


# ---------------------------------------------------------------- provenance

def test_every_catalogue_event_names_its_source_and_inputs():
    rows = delegated("v1") + commands("v1", [("pytest", 1), ("pytest", 0)])
    ev = run(rows)
    ids = {r["observable_id"] for r in rows}
    _, sessions = M.compute(rows, catalogue=True)
    steps = {f"step:{x['step_id']}" for s in sessions for x in s.steps}
    version = M.load_config()["metric_version"]
    for (sid, mid), e in ev.items():
        if mid not in M.CATALOGUE_IDS:
            continue
        assert e["source"] == f"signal_metrics.py:{mid}@{version}"
        assert e["required_fields"] and e["basis"] and e["session_id"] == sid
        for r in e["evidence_refs"]:
            assert r in steps or r.split(":", 1)[1] in ids, (mid, r)
    assert ev[("v1", "audit_metric_evidence_linkage")]["value"] == 1.0


# ---------------------------------------------------------------- registry

REG = __import__("json").load(open(os.path.join(os.path.dirname(M.__file__), "metric_registry.json")))
BY_ID = {e["id"]: e for e in REG["metrics"]}


def test_registry_matches_the_mapping_document_counts():
    assert len(REG["metrics"]) == len(BY_ID) == REG["source_document"]["distinct_metrics"] == 244
    assert sum(len(e["consumers"]) for e in REG["metrics"]) == REG["source_document"]["distinct_pairs"] == 487


def test_registry_and_engine_agree():
    built = {e["id"] for e in REG["metrics"] if e["status"] == "IMPLEMENTED"}
    existing = {e["id"] for e in REG["metrics"] if e["status"] == "IMPLEMENTED_EXISTING"}
    assert built == set(M.CATALOGUE_IDS)
    assert existing == set(M.METRIC_IDS) | {"telemetry.unknown_share"}
    for mid, name, fn, basis, fields, applies in M.CATALOGUE:
        e = BY_ID[mid]
        assert (e["required_fields"], e["basis"], e["applies_when"]) == (fields, basis, applies)
        assert e["formula"] and e["scope"] and e["window"] and e["missing_data"] and e["consumers"]


def test_code_only_pairs_are_kept_apart_and_typos_kept_as_written():
    for p in REG["code_only_pairs"]:
        assert p["cop"] not in BY_ID[p["metric"]]["consumers"]
    assert BY_ID["telemetry.unknown_shared"]["suspected_typo_of"] == "telemetry.unknown_share"
    assert "suspected_typo_of" in BY_ID["behavior_investigation_prioritya"]


def test_every_unbuilt_metric_says_what_it_needs():
    for e in REG["metrics"]:
        if e["status"] == "NOT_IMPLEMENTED":
            assert any(k in e for k in ("why_not_built", "input_contract", "note", "question")), e["id"]
        if e.get("category") == "R":
            assert e["input_contract"]["reference"] in REG["reference_data_contracts"]
