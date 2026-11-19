"""Interaction Discovery package: contract, metric-to-question mapping, harness against ground truth,
deterministic interpretation, memory, COP-to-COP influence and the decision record. Synthetic data,
no model call."""
import copy, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "collector"))

import pytest

from cops.packages.interaction_discovery import harness as H
from cops.runtime import cop_package as P, decision as D, evaluation as E, evidence_assessment as EA, graph as G
from cops.runtime import synthetic as S
from cops.runtime.llm import FixtureProvider
from cops.runtime.rules import DETERMINISTIC_TOOLS
from cops.specs import BY_KEY

COP = "interaction_discovery"
PKG = os.path.join(os.path.dirname(__file__), "..", "collector", "cops", "packages", COP)
WORKBOOK_METRICS = ("interaction.subagent_lanes", "interaction.mcp_servers", "telemetry.turn_linkage",
                    "telemetry.lane_linkage", "telemetry.collector_coverage", "telemetry.source_agreement",
                    "telemetry.unknown_share", "path.transition_novelty", "access.first_seen_mcp",
                    "interaction_graph_completeness", "interaction_edge_count", "interaction_unique_entities",
                    "interaction_route_novelty", "interaction_propagation_depth", "interaction_propagation_breadth",
                    "interaction_orphaned_events", "interaction_unattributed_edges")


@pytest.fixture(scope="module")
def bench():
    return H.run_all()


def rows_of(name):
    return H.BUILD[name]()


def questions(name, **k):
    rows = rows_of(name)
    return P.assess_interaction_discovery(rows, "s1", None, None, **k)


# ---------------------------------------------------------------- contract and mapping

def test_metrics_yaml_is_valid():
    assert P.validate(COP) == []


def test_every_workbook_metric_is_mapped_and_insights_outside_is_the_code_only_pair():
    pkg = EA.load_package(COP)
    assert set(pkg["mapping"]) == set(WORKBOOK_METRICS) | {"insights.outside"}
    assert pkg["mapping"]["insights.outside"].get("code_only") is True
    assert set(BY_KEY[COP].metric_keys) <= set(pkg["mapping"])


def test_every_required_metric_is_calculated_and_the_unbuilt_one_is_optional():
    built = set(EA.signal_metrics.METRIC_IDS) | set(EA.signal_metrics.CATALOGUE_IDS) | {"telemetry.unknown_share"}
    for qid, q in EA.load_package(COP)["questions"].items():
        for r in q["requires"]:
            assert r["metric"] in built or r["kind"] == EA.OPTIONAL, (qid, r)
    a = questions("normal")["ID-5"]["assessment"]
    item = next(x for x in a["presence"]["items"] if x["requirement"] == "interaction_unattributed_edges")
    assert item["state"] == EA.NOT_CALCULATED


def test_validator_rejects_a_code_only_mark_the_registry_does_not_have(monkeypatch):
    pkg = copy.deepcopy(EA.load_package(COP))
    pkg["mapping"]["telemetry.lane_linkage"]["code_only"] = True
    monkeypatch.setattr(EA, "load_package", lambda cop: pkg)
    assert any("marked code_only" in p for p in P.validate(COP))


def test_every_operation_is_deterministic_and_the_cop_calls_the_question_tool():
    assert {op["by"] for op in EA.load_package(COP)["operations"].values()} == {"deterministic"}
    assert DETERMINISTIC_TOOLS[COP][-1] == "assess_interaction_questions"


def test_every_package_file_exists():
    for f in ("SPEC.md", "METRICS.yaml", "INTERPRETATION.md", "MEMORY.md", "CONNECTIONS.md", "DECISIONS.md",
              "GROUND_TRUTH.yaml", "harness.py"):
        assert os.path.exists(os.path.join(PKG, f)), f


def test_each_question_reads_the_metrics_its_contract_names():
    q = questions("known_delegation")
    for qid, x in q.items():
        want = [r["metric"] for r in EA.load_package(COP)["questions"][qid]["requires"]]
        assert [i["requirement"] for i in x["assessment"]["presence"]["items"]] == want


# ---------------------------------------------------------------- harness

def test_harness_meets_every_ground_truth_expectation(bench):
    assert bench["failed"] == [], bench["failed"]
    assert bench["passed"] == len(bench["expectations"]) > 100


def test_every_scenario_publishes_a_valid_decision(bench):
    for name, r in bench["results"].items():
        res = r["cop"]["result"]
        assert res["decision_problems"] == [], name
        assert [x["id"] for x in res["decision"]["question_findings"]] == [f"ID-{i}" for i in range(1, 6)], name


# ---------------------------------------------------------------- calculation and interpretation

def test_depth_and_breadth_come_from_the_linked_lane_structure():
    b = questions("known_delegation")["ID-4"]["answer"]["basis"]
    assert (b["depth"], b["structural_depth"], b["breadth"], b["children"]) == (1, 1, 2, {"main": 2})


def test_outside_workspace_is_judged_by_its_approved_threshold():
    b = questions("propagating_outside_workspace")["ID-4"]["answer"]["basis"]
    th = EA.signal_metrics.load_config()["thresholds"]["insights.outside"]
    assert b["beyond"] == [{"metric": "insights.outside", "value": 1, "state": "EXCEPTION"}] and th["exception"] == 1


def test_unthresholded_measures_are_reported_not_judged():
    b = questions("known_delegation")["ID-4"]["answer"]["basis"]
    assert {m["metric"] for m in b["beyond"] + b["within"]} == {"interaction.subagent_lanes",
                                                                 "interaction.mcp_servers", "insights.outside"}
    assert len(b["not_judged"]) == 2


def test_one_fact_changes_the_answer_and_the_label():
    """Evidence changes the decision: only the github calls differ between the two worlds."""
    a, b = questions("normal"), questions("new_mcp_route")
    d = lambda q: P.decide_interaction_discovery(q)["label"]
    assert (a["ID-3"]["answer"]["outcome"], d(a)) == ("NO_NEW_ROUTES", "CONTAINED")
    assert (b["ID-3"]["answer"]["outcome"], d(b)) == ("NEW_ROUTES_APPEARED", "NEW_ROUTE")


def test_a_partial_graph_never_concludes_contained():
    for name in ("unlinked_subagents", "conflicting_action_sources", "contradictory_depth", "mcp_unobservable"):
        assert P.decide_interaction_discovery(questions(name))["label"] == "PATH_PARTIAL", name


def test_a_new_route_on_a_partial_graph_keeps_both_readings():
    d = P.decide_interaction_discovery(questions("new_route_on_partial_graph"))
    assert d["label"] == "NEW_ROUTE" and d["also_held"] == ["PATH_PARTIAL"] and d["drivers"] == ["ID-3"]


def test_missing_history_leaves_novelty_undecided_not_the_graph_partial():
    d = P.decide_interaction_discovery(questions("insufficient_history_with_routes"))
    assert d["label"] == "INCONCLUSIVE" and d["drivers"] == ["ID-3"] and d["action"] == "WATCH"


def test_no_edge_needs_no_history():
    q = questions("no_history_no_routes")["ID-3"]
    assert q["assessment"]["sufficiency"]["status"] == "INSUFFICIENT"
    assert q["answer"]["outcome"] == "NO_NEW_ROUTES" and "no interaction edge" in q["answer"]["basis"]["note"]


def test_delegation_with_no_identity_impairs_reconstruction():
    rows = rows_of("normal")
    rows.insert(-1, {**rows[-2], "observable_id": "s1-x", "observable_type": "subagent_start", "agent_id": None})
    b = P.assess_interaction_discovery(rows, "s1")["ID-5"]["answer"]
    assert b["outcome"] == "RECONSTRUCTION_IMPAIRED"
    assert b["basis"]["delegation_without_identity"] == ["observable:s1-x"]


def test_single_agent_sessions_without_agent_id_are_not_impaired():
    rows = rows_of("normal")
    assert not any(r.get("agent_id") for r in rows if r["session_id"] == "s1")
    assert questions("normal")["ID-5"]["answer"]["outcome"] == "RECONSTRUCTION_RELIABLE"


def test_the_label_never_rests_on_instruction_text_in_a_tool_result(bench):
    r = bench["results"]["adversarial_instruction_text"]
    assert r["cop"]["finding"]["label"] == "NEW_ROUTE" == r["without_text"]["label"]


# ---------------------------------------------------------------- memory

def test_only_validated_route_records_count_as_known(bench):
    v, u = bench["results"]["memory_validated_route"], bench["results"]["memory_unvalidated_route"]
    assert v["remembered"]["written"] and u["remembered"]["written"]
    assert v["cop"]["finding"]["label"] == "CONTAINED" and u["cop"]["finding"]["label"] == "NEW_ROUTE"
    assert v["cop"]["result"]["decision"]["memory_used"]["cited_as_precedent"] == [v["remembered"]["record_id"]]


def test_route_records_carry_the_routes_they_establish(bench):
    r = bench["results"]["new_mcp_route"]["cop"]
    routes = r["finding"]["cop_specific"]["memory_payload"]["routes"]
    assert {"main>mcp:github", "mcp:github"} <= set(routes)
    assert r["result"]["memory_write"]["written"] and r["result"]["memory_write"]["status"] == "VERIFIED"


def test_a_contained_session_writes_no_route_record(bench):
    assert bench["results"]["normal"]["cop"]["result"]["memory_write"]["written"] is False


# ---------------------------------------------------------------- COP-to-COP

def test_runtime_monitoring_rm3_changes_the_interaction_decision(bench):
    r = bench["results"]["upstream_rm_unlinked"]
    assert r["linked"]["finding"]["label"] == "CONTAINED" and r["cop"]["finding"]["label"] == "PATH_PARTIAL"
    q = r["cop"]["finding"]["cop_specific"]["questions"]["ID-5"]["basis"]["upstream"]["runtime_monitoring"]
    assert q["RM-3"] == "PARTIALLY_LINKED" and q["finding_id"] == "f-rm-partially_linked"


def test_policy_enforcement_consumes_the_new_route(bench):
    r = bench["results"]["downstream_policy"]
    pe = r["downstream"]["policy_enforcement"]
    assert any(c.get("upstream_cop") == COP for c in pe["claims"])
    assert [u for u in pe["upstream_used"] if u["cop"] == COP][0]["finding_id"] == r["cop"]["finding"]["finding_id"]


def test_resource_management_reads_id2_not_only_the_label(bench):
    r = bench["results"]["downstream_resource_attribution"]
    a = r["downstream"]["resource_management"]["cop_specific"]["attribution"]
    assert r["cop"]["finding"]["label"] == "NEW_ROUTE" and a["status"] == "ATTRIBUTED"
    assert a["source_finding"] == r["cop"]["finding"]["finding_id"]


def test_the_published_finding_carries_the_question_findings():
    rt = E.make_runtime("id-pub", "s1", rows_of("new_mcp_route"), {})
    out = G.run_case(rt, cops=["runtime_monitoring", COP])
    pub = out["findings"][COP]
    assert list(pub["cop_specific"]["questions"]) == [f"ID-{i}" for i in range(1, 6)]
    assert pub["cop_specific"]["questions"]["ID-3"]["drives_label"] is True
    claims = [c for c in pub["claims"] if (c.get("result_field") or "").startswith("questions.")]
    assert len(claims) == 5 and pub["verified"] is True


# ---------------------------------------------------------------- the decision record (all COPs)

def test_every_cop_publishes_a_valid_decision_record():
    from cops.runtime.live_eval import allow_all
    rt = E.make_runtime("all18", "s1", E._estate(S.normal_session("s1", 9)),
                        {"policy_store": allow_all(), "risk_register": S.risk_register()})
    out = G.run_case(rt, cops=list(BY_KEY))
    assert len(out["results"]) == 18
    for cop, r in out["results"].items():
        d = r["decision"]
        assert r["decision_problems"] == [], cop
        assert set(D.CORE) <= set(d)
        if cop in EA.PACKAGED:
            assert d["question_findings"] and "question_findings" not in d["absent"], cop
        else:
            assert d["question_findings"] == [] and "question_findings" in d["absent"], cop


def test_a_model_cop_records_its_calls_in_the_decision_provenance():
    from cops.runtime import fixtures as FX
    prov = FixtureProvider(lambda *a: FX.inconclusive("simulated: nothing to conclude", []))
    rt = E.make_runtime("id-llm", "s1", E._estate(S.normal_session("s1", 9)), {}, provider=prov)
    r = G.run_cop(rt, "anomaly_detection")["result"]
    calls = r["decision"]["provenance"]["model_calls"]
    assert calls and all(c["simulated"] for c in calls)
    assert r["decision_problems"] == []


def test_the_validator_rejects_an_answer_outside_the_contract(bench):
    d = copy.deepcopy(bench["results"]["normal"]["cop"]["result"]["decision"])
    d["question_findings"][2]["answer"] = "PROBABLY_FINE"
    d["question_findings"].pop()
    probs = D.validate(d)
    assert "ID-3: answer PROBABLY_FINE is not one of its outcomes" in probs
    assert "question_findings do not cover every question in the contract" in probs
