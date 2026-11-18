"""COP-to-COP communication: current-cycle edges, previous-cycle feedback, provenance."""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "collector"))

import pytest

from cops import specs
from cops.runtime import evaluation as E, synthetic as S
from cops.runtime.graph import build_case_graph, prior_feedback, run_case, run_cop
from cops.runtime.requirements import precheck

ROWS = E._estate(S.normal_session("c01", 9))


def cycle(case_id, history=None, cops=None):
    rt = E.make_runtime(case_id, "c01", ROWS, {}, None)
    return rt, run_case(rt, cops=cops, history=history)


@pytest.fixture(scope="module")
def first():
    return cycle("comm-1")


def edge(out, producer, consumer, current=True):
    rows = [x for x in out["consumption"] if x["producer"] == producer and x["consumer"] == consumer
            and (x.get("cycle") != "previous") == current]
    assert len(rows) == 1, rows
    return rows[0]


def view(result, cop):
    return next(u for u in result["upstream"] if u["cop"] == cop)


def test_legacy_runner_edges_unchanged():
    for key in specs.ACTIVE:
        assert specs.BY_KEY[key].consumes_decisions_from == specs.SPEC_CONSUMES[key]
    assert "resource_management" not in specs.BY_KEY["incident_response"].consumes_decisions_from
    assert "interaction_discovery" not in specs.BY_KEY["resource_management"].consumes_decisions_from


@pytest.mark.parametrize("producer,consumer", [
    ("classification_tagging", "incident_response"),
    ("resource_management", "incident_response"),
    ("interaction_discovery", "resource_management"),
])
def test_topology_edges_deliver_the_upstream_finding(first, producer, consumer):
    _, out = first
    pub = out["findings"][producer]
    row = edge(out, producer, consumer)
    assert row["finding_id"] == pub["finding_id"] and row["consumed_as"] != "absent"
    v = view(out["results"][consumer], producer)
    assert v["present"] and v["finding_id"] == pub["finding_id"] and v["label"] == pub["label"]
    assert out["order"].index(producer) < out["order"].index(consumer)


def test_missing_upstream_is_recorded_and_does_not_block():
    _, out = cycle("comm-missing", cops=["runtime_monitoring", "anomaly_detection", "incident_response"])
    for producer in ("classification_tagging", "resource_management", "policy_enforcement"):
        assert edge(out, producer, "incident_response")["consumed_as"] == "absent"
        assert view(out["results"]["incident_response"], producer)["present"] is False
    assert out["findings"]["incident_response"]["label"]
    rt = E.make_runtime("comm-missing-pc", "c01", ROWS, {}, None)
    pc = precheck("incident_response", rt.case, {"rows": []}, {}, [])
    miss = [r for r in pc["reasons"] if r["item"] == "finding:resource_management"]
    assert miss and miss[0]["code"] == "MISSING_DEPENDENCY" and miss[0]["gates"] == "none"


def test_inconclusive_upstream_is_context_only(first):
    _, out = first
    ct = out["findings"]["classification_tagging"]
    assert ct["status"] != "SUBSTANTIVE" and not ct["verified"]
    row = edge(out, "classification_tagging", "incident_response")
    assert row["consumed_as"] == "context_only_unverified"
    v = view(out["results"]["incident_response"], "classification_tagging")
    assert v["status"] == ct["status"] and v["verified"] is False
    used = out["findings"]["incident_response"].get("upstream_used") or []
    assert "classification_tagging" not in {u["cop"] for u in used}


def test_no_cop_runs_twice_in_a_cycle(first):
    _, out = first
    graph, order = build_case_graph()
    assert len(order) == len(set(order)) == 18
    assert set(out["results"]) == set(order)
    for consumer, producers in specs.FEEDBACK.items():
        for p in producers:
            assert consumer not in specs.CONSUMES[p] or order.index(consumer) < order.index(p)
            assert p not in specs.CONSUMES[consumer]

def test_feedback_edge_as_current_edge_would_be_a_cycle(monkeypatch):
    cyc = {**specs.CONSUMES,
           "behavior_analytics": specs.CONSUMES["behavior_analytics"] + ("anomaly_detection",)}
    monkeypatch.setattr(specs, "CONSUMES", cyc)
    with pytest.raises(ValueError):
        specs.topological_order()


def test_same_cycle_finding_is_not_feedback(first):
    rt, out = first
    fb, rows = prior_feedback("behavior_analytics", {"anomaly_detection": {**out["findings"]["anomaly_detection"],
                                                                           "verified": True}}, rt.run_id)
    assert fb == {} and rows[0]["reason"] == "finding is from the current cycle"
    ba_fb = edge(out, "anomaly_detection", "behavior_analytics", current=False)
    assert ba_fb["consumed_as"] == "not_delivered"


def test_feedback_reaches_only_its_consumer_and_stays_out_of_the_cycle(first):
    _, prev = first
    history = {k: {**f, "verified": True} for k, f in prev["findings"].items()}
    rt, out = cycle("comm-2", history=history)
    assert edge(out, "anomaly_detection", "behavior_analytics", current=False)["consumed_as"] == "evidence"
    assert edge(out, "workload_optimization", "resource_management", current=False)["consumed_as"] == "evidence"
    fed = [x for x in out["consumption"] if x.get("cycle") == "previous"]
    assert {(x["producer"], x["consumer"]) for x in fed} == {
        (p, c) for c, ps in specs.FEEDBACK.items() for p in ps}
    for k, f in out["findings"].items():
        assert f["run_id"] == rt.run_id
    for x in out["consumption"]:
        if x.get("cycle") != "previous" and x["finding_id"]:
            assert x["finding_id"] == out["findings"][x["producer"]]["finding_id"]
    ba = out["results"]["behavior_analytics"]
    assert {u["cop"] for u in ba["upstream"]} == set(specs.CONSUMES["behavior_analytics"])
    assert [r["cop"] for r in ba["feedback"] if r["delivered"]] == ["anomaly_detection"]
    assert out["results"]["incident_response"]["feedback"] == []


def test_feedback_without_cycle_provenance_is_refused(first):
    _, prev = first
    f = {k: v for k, v in prev["findings"]["workload_optimization"].items() if k != "run_id"}
    fb, rows = prior_feedback("resource_management", {"workload_optimization": f}, "eval-other")
    assert fb == {} and rows[0]["reason"] == "finding does not name the cycle it came from"


def test_exchanged_findings_carry_provenance(first):
    rt, out = first
    for cop, f in out["findings"].items():
        assert f["finding_id"] and f["case_id"] == rt.case.case_id and f["run_id"] == rt.run_id
        assert f["session_id"] == "c01" and f["decided_at"] == rt.case.computed_at
        assert f["status"] and "verified" in f
        assert all(c.get("evidence_refs") for c in f.get("claims") or [])
    history = {"workload_optimization": prev_wko(out)}
    rt2, out2 = cycle("comm-prov", history=history)
    row = next(r for r in out2["results"]["resource_management"]["feedback"] if r["cop"] == "workload_optimization")
    src = history["workload_optimization"]
    assert (row["finding_id"], row["run_id"], row["decided_at"], row["status"]) == (
        src["finding_id"], src["run_id"], src["decided_at"], src["status"])
    assert edge(out2, "workload_optimization", "resource_management", current=False)["source_run_id"] == rt.run_id


def prev_wko(out):
    return dict(out["findings"]["workload_optimization"])


def test_unverified_feedback(first):
    _, prev = first
    history = {k: {**f, "verified": False} for k, f in prev["findings"].items()}
    _, out = cycle("comm-unverified", history=history)
    ad = edge(out, "anomaly_detection", "behavior_analytics", current=False)
    assert ad["consumed_as"] == "not_delivered"
    assert ad["reason"] == "unverified; this edge delivers verified findings only"
    wko = edge(out, "workload_optimization", "resource_management", current=False)
    assert wko["consumed_as"] == "context_only_unverified"
    rt = E.make_runtime("comm-unverified-cop", "c01", ROWS, {}, None)
    r = run_cop(rt, "behavior_analytics", history=history)["result"]
    assert not any(x["delivered"] for x in r["feedback"])
