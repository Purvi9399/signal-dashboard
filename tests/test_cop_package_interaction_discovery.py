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


def test_outside_workspace_is_observed_for_policy_enforcement_not_judged_as_propagation():
    """D17: a workspace boundary crossing is not propagation across agents, tools or services."""
    q = questions("propagating_outside_workspace")
    b = q["ID-4"]["answer"]["basis"]
    assert q["ID-4"]["answer"]["outcome"] == "WITHIN_APPROVED_BOUNDS" and b["beyond"] == []
    o = b["boundary_observations"]
    assert [(x["metric"], x["value"], x["state"], x["owner"]) for x in o] == [
        ("insights.outside", 1, "EXCEPTION", "policy_enforcement")] and o[0]["evidence_refs"]
    assert P.decide_interaction_discovery(q)["label"] == "CONTAINED"


def test_only_approved_propagation_measures_are_judged():
    b = questions("known_delegation")["ID-4"]["answer"]["basis"]
    assert {m["metric"] for m in b["beyond"] + b["within"]} == set(EA.load_package(COP)["propagation_measures"]) \
        == {"interaction.subagent_lanes", "interaction.mcp_servers"}
    assert len(b["not_judged"]) == 2


def test_within_bounds_covers_only_the_two_evaluated_count_limits():
    b = questions("known_delegation")["ID-4"]["answer"]
    assert b["outcome"] == "WITHIN_APPROVED_BOUNDS"
    assert b["basis"]["evaluated_limits"] == ["interaction.subagent_lanes", "interaction.mcp_servers"]
    assert {x["metric"]: x["status"] for x in b["basis"]["not_evaluated"]} == {
        "interaction_propagation_depth": "NOT_EVALUATED", "interaction_propagation_breadth": "NOT_EVALUATED"}
    pkg = EA.load_package(COP)
    assert set(b["basis"]["evaluated_limits"]) == set(pkg["propagation_measures"]) <= set(pkg["mapping"])
    assert set(pkg["propagation_measures"]) <= set(BY_KEY[COP].metric_keys)
    assert set(pkg["propagation_measures"]) <= set(EA.signal_metrics.METRIC_IDS)


def test_an_approved_limit_that_cannot_be_evaluated_leaves_id4_inconclusive():
    a = questions("mcp_unobservable")["ID-4"]["answer"]
    assert a["outcome"] == "INCONCLUSIVE" and a["sufficiency"] == "SUFFICIENT"
    assert [x["requirement"] for x in a["because"]] == ["interaction.mcp_servers"]
    assert a["basis"]["evaluated_limits"] == ["interaction.subagent_lanes"]


def test_more_subagent_lanes_than_approved_is_propagation():
    q = questions("several_concerns")
    assert [(x["metric"], x["value"], x["state"]) for x in q["ID-4"]["answer"]["basis"]["beyond"]] == [
        ("interaction.subagent_lanes", 16, "EXCEPTION")]


def test_several_concerns_keep_every_answer_and_follow_the_label_order(bench):
    r = bench["results"]["several_concerns"]
    f, d = r["cop"]["finding"], r["cop"]["result"]["decision"]
    assert (f["label"], f["cop_specific"]["also_held"]) == ("NEW_ROUTE", ["PROPAGATING", "PATH_PARTIAL"])
    by = {x["id"]: x for x in d["question_findings"]}
    assert [by[q]["answer"] for q in ("ID-3", "ID-4", "ID-2")] == [
        "NEW_ROUTES_APPEARED", "BEYOND_APPROVED_BOUNDS", "PARTIALLY_RECONSTRUCTED"]
    assert by["ID-3"]["drives_conclusion"] and by["ID-4"]["basis"]["beyond"] and by["ID-2"]["basis"]["gaps"]


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
        assert set(D.CORE) <= set(d) and isinstance(d["uncertainty"]["limitations"], list), cop
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


# ---------------------------------------------------------------- link provenance, time order, continuity

def _links(q):
    return {x["link"]: x["basis"] for x in q["ID-2"]["answer"]["basis"]["links"] if x["kind"] == "delegation"}


def test_the_unbuilt_metric_states_its_missing_input():
    u = questions("normal")["ID-5"]["answer"]["basis"]["unassessed"]
    m = next(x for x in u if x["metric"] == "interaction_unattributed_edges")
    assert m["state"] == EA.NOT_CALCULATED and "Missing input: an approved definition" in m["missing_inputs"]


def test_a_link_is_recorded_only_when_a_row_names_the_parent():
    assert _links(questions("recorded_delegation")) == {"main>sub-a": "RECORDED", "main>sub-b": "RECORDED"}
    assert _links(questions("known_delegation")) == {"main>sub-a": "INFERRED", "main>sub-b": "INFERRED"}
    assert _links(questions("unlinked_subagents")) == {"?>sub-a": "UNLINKED", "?>sub-b": "UNLINKED"}


def test_a_link_inferred_from_timing_is_not_verified_and_leaves_id2_partial():
    """U5: only parent_agent_id differs between the two worlds."""
    inferred, recorded = questions("known_delegation"), questions("recorded_delegation")
    assert (inferred["ID-2"]["answer"]["outcome"], recorded["ID-2"]["answer"]["outcome"]) == (
        "PARTIALLY_RECONSTRUCTED", "FULLY_RECONSTRUCTED")
    assert (P.decide_interaction_discovery(inferred)["label"], P.decide_interaction_discovery(recorded)["label"]) == (
        "PATH_PARTIAL", "CONTAINED")
    kinds = [[x["subject"] for x in P.decide_interaction_discovery(q)["limitations"]
              if x["kind"] == D.INFERRED_RELATIONSHIP and x["verified"] is False] for q in (inferred, recorded)]
    assert kinds == [["main>sub-a", "main>sub-b"], []]


def test_a_handoff_recorded_after_the_lane_began_is_a_gap():
    """Only the order of sub-a's first call and its handoff differs from known_delegation."""
    q = questions("handoff_after_lane_start")
    assert _links(q) == {"main>sub-a": "INFERRED_AGAINST_TIME_ORDER", "main>sub-b": "INFERRED"}
    lim = {x["subject"]: x for x in P.decide_interaction_discovery(q)["limitations"]
           if x["kind"] == D.INFERRED_RELATIONSHIP}
    assert lim["main>sub-a"]["reason_code"] == "FIRST_HANDOFF_FALLBACK" and lim["main>sub-a"]["verified"] is False
    assert lim["main>sub-a"]["evidence_refs"] and lim["main>sub-b"]["reason_code"] == "LATEST_PRIOR_HANDOFF"
    assert q["ID-2"]["answer"]["outcome"] == "PARTIALLY_RECONSTRUCTED"
    assert P.decide_interaction_discovery(q)["label"] == "PATH_PARTIAL"


def test_timestamps_out_of_order_are_a_gap_and_missing_ones_a_limitation():
    bad, plain = questions("timestamps_out_of_order"), questions("normal")
    assert bad["ID-2"]["answer"]["basis"]["temporal_checks"]["TIMESTAMP_ORDER"]["outcome"] == EA.VERIFIED_FAILED
    assert (bad["ID-2"]["answer"]["outcome"], plain["ID-2"]["answer"]["outcome"]) == (
        "PARTIALLY_RECONSTRUCTED", "FULLY_RECONSTRUCTED")
    assert any(x["kind"] == D.MISSING_EVIDENCE and x["subject"] == "TIMESTAMP_ORDER"
               for x in P.decide_interaction_discovery(plain)["limitations"])


def test_tools_are_associated_with_the_lane_that_called_them():
    b = questions("known_delegation")["ID-1"]["answer"]["basis"]
    assert b["tools_by_lane"]["sub-a"] == ["Read"] and "Bash" in b["tools_by_lane"]["main"]


def test_a_session_with_no_operator_has_no_continuity_and_no_route_answer():
    q = questions("no_operator_with_routes")
    assert q["ID-3"]["answer"]["outcome"] == "INCONCLUSIVE"
    assert P.decide_interaction_discovery(q)["label"] == "INCONCLUSIVE"
    c = questions("known_delegation")["ID-3"]["answer"]["basis"]["continuity"]
    assert c["operator_recorded"] is True and c["earlier_sessions"] == 7


def test_limitations_reach_the_decision_record(bench):
    d = bench["results"]["known_delegation"]["cop"]["result"]["decision"]
    assert any("not verified" in x for x in d["uncertainty"]["cop_limitations"])
    assert "limitations" not in d["extensions"][COP]


def test_the_four_kinds_of_limitation_are_kept_apart(bench):
    """D14: missing evidence, an uncalculated metric, an unapproved limit and an inferred relationship."""
    lim = bench["results"]["known_delegation"]["cop"]["result"]["decision"]["uncertainty"]["limitations"]
    by = {}
    for x in lim:
        by.setdefault(x["kind"], set()).add(x["subject"])
    assert set(by) == set(D.LIMITATION_KINDS)
    assert by[D.NOT_CALCULATED] == {"interaction_unattributed_edges"}
    assert by[D.LIMIT_NOT_APPROVED] == {"interaction_propagation_depth", "interaction_propagation_breadth"}
    assert by[D.INFERRED_RELATIONSHIP] == {"main>sub-a", "main>sub-b"}
    assert {"UNVERIFIABLE_PROVENANCE", "TIMESTAMP_ORDER"} <= by[D.MISSING_EVIDENCE]


def test_unapproved_depth_and_breadth_never_make_propagating(bench):
    """U3: the measurements are kept and reported; with no approved threshold they are not judged."""
    f = bench["results"]["known_delegation"]["cop"]["finding"]
    b = f["cop_specific"]["questions"]["ID-4"]["basis"]
    assert (b["depth"], b["breadth"], f["label"]) == (1, 2, "PATH_PARTIAL")
    assert f["cop_specific"]["questions"]["ID-4"]["answer"] == "WITHIN_APPROVED_BOUNDS"


def test_the_record_with_limitations_serializes_and_validates(bench):
    import json
    for name, r in bench["results"].items():
        d = (r.get("cop") or {}).get("result", {}).get("decision")
        if d:
            again = json.loads(json.dumps(d, default=str))
            assert again["uncertainty"]["limitations"] == d["uncertainty"]["limitations"], name
            assert D.validate(again) == [], name


def test_a_record_without_limitations_still_validates(bench):
    d = copy.deepcopy(bench["results"]["normal"]["cop"]["result"]["decision"])
    del d["uncertainty"]["limitations"], d["uncertainty"]["cop_limitations"]
    assert D.validate(d) == []


def test_the_validator_rejects_a_malformed_limitation(bench):
    d = copy.deepcopy(bench["results"]["known_delegation"]["cop"]["result"]["decision"])
    x = next(x for x in d["uncertainty"]["limitations"] if x["kind"] == D.INFERRED_RELATIONSHIP)
    x["verified"] = True
    d["uncertainty"]["limitations"].append(dict(x, kind="PROBABLY_FINE"))
    probs = D.validate(d)
    assert "inferred relationship main>sub-a is not marked unverified" in probs
    assert any(p.startswith("limitation kind PROBABLY_FINE") for p in probs)


def test_downstream_receives_the_limitations_and_they_do_not_change_its_decision(bench):
    r = bench["results"]["downstream_audit_traceability_inferred"]
    idf = r["cop"]["finding"]
    assert any(x["kind"] == D.INFERRED_RELATIONSHIP for x in idf["cop_specific"]["limitations"])
    rows = H.BUILD["downstream_audit_traceability_inferred"]()
    up = G.run_case(E.make_runtime("lim-up", "s1", copy.deepcopy(rows), {}), cops=["runtime_monitoring", COP])
    seen = up["findings"]
    stripped = copy.deepcopy(seen)
    stripped[COP]["cop_specific"]["limitations"] = []
    at = [G.run_cop(E.make_runtime(f"lim-{i}", "s1", copy.deepcopy(rows), {}), "audit_traceability", upstream=u)
          ["finding"] for i, u in enumerate((seen, stripped))]
    assert [a["label"] for a in at] == ["PARTIAL", "PARTIAL"]
    assert {q: v["answer"] for q, v in at[0]["cop_specific"]["questions"].items()} \
        == {q: v["answer"] for q, v in at[1]["cop_specific"]["questions"].items()}


# ---------------------------------------------------------------- downstream use changes with the evidence

def test_audit_traceability_reads_id2_and_its_label_follows(bench):
    late, clean, timed = (bench["results"][n] for n in ("downstream_audit_traceability",
                                                         "downstream_audit_traceability_clean",
                                                         "downstream_audit_traceability_inferred"))
    at = [r["downstream"]["audit_traceability"] for r in (late, clean, timed)]
    assert [a["label"] for a in at] == ["PARTIAL", "COMPLETE", "PARTIAL"]
    assert at[0]["cop_specific"]["questions"]["AT-2"]["basis"]["gaps"] == [
        "Interaction Discovery ID-2: PARTIALLY_RECONSTRUCTED"]
    assert at[0]["cop_specific"]["questions"]["AT-2"]["basis"]["upstream"]["interaction_discovery"]["finding_id"] \
        == late["cop"]["finding"]["finding_id"]


def test_agent_lifecycle_uses_a_new_route_and_nothing_otherwise(bench):
    used = [[u["cop"] for u in bench["results"][n]["downstream"]["agent_lifecycle_management"]["upstream_used"]]
            for n in ("downstream_lifecycle", "downstream_lifecycle_no_route")]
    assert used == [[COP], []]
