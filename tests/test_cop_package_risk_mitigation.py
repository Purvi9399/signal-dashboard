"""Risk Mitigation package: contract, metric-to-question mapping, harness against ground truth, the observation and
interpretation layers, the register formula, signal independence, controls and authority, memory, downstream
feedback and the decision record. Synthetic data; no model is called for Risk Mitigation, and Incident Response's
model, where it runs, is a scripted fixture (simulated). No paid model call."""
import copy, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "collector"))

import pytest

from cops.packages.risk_mitigation import harness as H
from cops.runtime import cop_package as P, evaluation as E, evidence_assessment as EA, graph as G
from cops.runtime import synthetic as S
from cops.runtime.cop_package import DECIDED_WITHOUT_MODEL
from cops.runtime.rules import DETERMINISTIC_TOOLS
from cops.runtime.tools import TOOLS, UNSEQUENCED
from cops.specs import BY_KEY, CONSUMES, FEEDBACK, SPEC_CONSUMES

COP = "risk_mitigation"
PKG = os.path.join(os.path.dirname(__file__), "..", "collector", "cops", "packages", COP)
# COPS- implementation 1.xlsx, COMPLETE_DELEGATION_DATA row 140
WORKBOOK_METRICS = (
    "insights.outside", "insights.sensitive_unasked", "insights.secrets", "insights.blocked", "insights.no_oversight",
    "insights.failed", "insights.repetition", "insights.retry", "insights.stalled", "insights.reverted",
    "insights.unattributed", "insights.token_outlier", "insights.duration_outlier", "oversight.fast_approvals",
    "oversight.declined_inferred", "access.first_seen_mcp", "path.transition_novelty", "activity.action_rate_ratio",
    "interaction.subagent_lanes", "interaction.mcp_servers", "telemetry.collector_coverage",
    "telemetry.source_agreement", "telemetry.turn_linkage", "telemetry.lane_linkage", "telemetry.unknown_share",
    "risk_likelihood_score", "risk_severity_score", "risk_exposure_score", "risk_impact_score",
    "risk_asset_criticality", "risk_affected_entity_count", "risk_propagation_potential", "risk_evidence_sufficiency",
    "risk_mitigation_effectiveness", "risk_intervention_cost", "risk_response_proportionality",
    "risk_mitigation_completion", "risk_residual_score", "risk_recurrence_rate", "risk_escalation_required")


@pytest.fixture(scope="module")
def bench():
    return H.run_all()


def answers(f):
    return {q: x["answer"] for q, x in f["cop_specific"]["questions"].items()}


# ---------------------------------------------------------------- contract and mapping

def test_metrics_yaml_is_valid():
    assert P.validate(COP) == []


def test_every_workbook_metric_is_mapped_and_unbuilt_ones_are_not_calculated():
    pkg = EA.load_package(COP)
    assert set(pkg["mapping"]) == set(WORKBOOK_METRICS) and len(WORKBOOK_METRICS) == 40
    built = set(EA.signal_metrics.METRIC_IDS) | set(EA.signal_metrics.CATALOGUE_IDS) | {"telemetry.unknown_share"}
    unbuilt = {m for m in WORKBOOK_METRICS if m.startswith("risk_")}
    assert len(unbuilt) == 15 and not unbuilt & built and set(WORKBOOK_METRICS) - unbuilt <= built
    assert all(pkg["mapping"][m]["note"].startswith("NOT_CALCULATED") for m in unbuilt)
    # every question requires its unbuilt metrics as OPTIONAL, never REQUIRED
    for q in pkg["questions"].values():
        assert all(r["kind"] == "OPTIONAL" for r in q["requires"] if r["metric"] in unbuilt)
    # only the spec's metric_keys are REQUIRED: they are what observed risk is counted from
    req = {r["metric"] for q in pkg["questions"].values() for r in q["requires"] if r["kind"] == "REQUIRED"}
    assert req == set(BY_KEY[COP].metric_keys)


def test_spec_missing_metrics_are_not_calculated_with_their_missing_input():
    pkg = EA.load_package(COP)
    assert set(pkg["not_calculated"]) == set(BY_KEY[COP].missing_metrics)
    assert all("Missing input" in v for v in pkg["not_calculated"].values())


def test_no_operation_uses_the_model_and_the_question_tool_is_unsequenced():
    ops = EA.load_package(COP)["operations"]
    assert [k for k, v in ops.items() if v["by"] == "llm"] == []
    assert DETERMINISTIC_TOOLS[COP] == ("read_risk_register", "calculate_risk_components", "assess_risk_questions")
    assert "assess_risk_questions" in TOOLS and "assess_risk_questions" in UNSEQUENCED and COP in EA.PACKAGED


def test_every_package_file_exists():
    for f in ("METRICS.yaml", "SPEC.md", "INTERPRETATION.md", "MEMORY.md", "CONNECTIONS.md", "DECISIONS.md",
              "GROUND_TRUTH.yaml", "harness.py", "__init__.py"):
        assert os.path.exists(os.path.join(PKG, f)), f
    assert os.path.exists(os.path.join(PKG, "..", "..", "..", "..", ".claude", "skills", "cops-risk-mitigation",
                                       "SKILL.md"))


def test_labels_actions_and_edges_are_the_existing_ones():
    pkg = EA.load_package(COP)
    assert set(pkg["labels"].values()) - {"INCONCLUSIVE"} == set(BY_KEY[COP].interpretations)
    assert {d["action"] for d in pkg["decision"].values()} <= set(BY_KEY[COP].permitted_actions)
    assert set(pkg["upstream"]) == set(CONSUMES[COP]) == set(SPEC_CONSUMES[COP])
    assert COP in FEEDBACK["incident_response"]


# ---------------------------------------------------------------- harness

def test_harness_meets_every_ground_truth_expectation(bench):
    assert bench["failed"] == [], bench["failed"][:3]
    assert bench["passed"] == len(bench["expectations"]) == 158


def test_every_decided_scenario_publishes_a_valid_decision_with_four_findings(bench):
    for name, res in bench["results"].items():
        r = res["cop"]["result"]
        assert r["decision_problems"] == [], name
        assert [x["id"] for x in r["decision"]["question_findings"]] == ["RK-1", "RK-2", "RK-3", "RK-4"], name


def test_risk_mitigation_never_calls_a_model(bench):
    for name, res in bench["results"].items():
        assert res["llm_invocations"] == [], name
        assert res["cop"]["finding"]["cop_specific"].get("stop_reason") == DECIDED_WITHOUT_MODEL, name


def test_the_label_is_rk2s_band(bench):
    band = {"MATERIAL_LEVEL": "MATERIAL", "EMERGING_LEVEL": "EMERGING", "BELOW_THRESHOLDS": "LOW"}
    for name, res in bench["results"].items():
        f = res["cop"]["finding"]
        a = answers(f)
        want = band.get(a["RK-2"], "INCONCLUSIVE")
        if want == "LOW" and a["RK-1"] == "INCONCLUSIVE":
            want = "INCONCLUSIVE"
        assert f["label"] == want, (name, a, f["label"])


def test_actions_never_exceed_authority_and_nothing_is_executed(bench):
    for name, res in bench["results"].items():
        assert H._within_authority(res)[0] or res["cop"]["finding"]["label"] == "INCONCLUSIVE", name
        assert H._not_executed(res)[0], name


# ---------------------------------------------------------------- the formula and its inputs

def test_quiet_and_correlated_findings_are_not_signals():
    up = {"anomaly_detection": {"verified": True, "status": "SUBSTANTIVE", "label": "NORMAL", "lineage": ["tool_call:a"]},
          "policy_enforcement": {"verified": True, "status": "SUBSTANTIVE", "label": "VIOLATION",
                                 "lineage": ["tool_call:b"]},
          "incident_response": {"verified": True, "status": "SUBSTANTIVE", "label": "INCIDENT",
                                "lineage": ["insights.secrets"]},
          "resource_management": {"verified": False, "status": "SUBSTANTIVE", "label": "EXCESSIVE",
                                  "lineage": ["tool_call:c"]}}
    split = P.rk_signal_split(up)
    assert [x["cop"] for x in split["independent"]] == ["policy_enforcement"]
    assert [x["cop"] for x in split["correlated"]] == ["incident_response"]
    assert split["quiet"] == ["anomaly_detection"]


def test_the_rule_vs01_and_package_read_the_same_signals(bench):
    for name in ("normal_low", "genuine_risk_emerging", "conflicting_disputed_irreversible"):
        f = bench["results"][name]["cop"]["finding"]
        assert f["cop_specific"]["legacy_reading"]["label"] == f["label"], name
        assert f["verified"] is True, name


def test_previous_reading_is_recorded_where_it_differs(bench):
    b = bench["results"]["normal_low"]["cop"]["finding"]["cop_specific"]["questions"]["RK-2"]["basis"]
    assert b["previous_reading"]["band"] == "EMERGING" and b["band"] == "LOW"


# ---------------------------------------------------------------- memory, downstream, evidence

def test_memory_is_comparison_only_and_quiet_readings_write_nothing(bench):
    m = bench["results"]["memory_precedent"]
    assert m["cop"]["finding"]["label"] == m["without_memory"]["label"]
    assert (bench["results"]["normal_low"]["cop"]["result"]["memory_write"] or {}).get("written") is not True
    w = bench["results"]["genuine_risk_emerging"]["cop"]["result"]["memory_write"]
    assert w["written"] and w["record_type"] == "mitigation_outcome"


def test_incident_response_receives_the_finding_on_the_feedback_edge_only(bench):
    r = bench["results"]["downstream_incident_response_feedback"]
    assert r["cycle2_ir_rule"]["earlier_risk_assessment"]["label"] == "EMERGING"
    assert not [k for k, ups in CONSUMES.items() if COP in ups]


def test_evidence_changes_move_the_decision(bench):
    r = bench["results"]["evidence_change_changes_decision"]
    assert (r["cop"]["finding"]["label"], r["permissive"]["label"], r["with_rmrf"]["label"], r["tier0"]["label"]) == \
        ("EMERGING", "LOW", "MATERIAL", "LOW")


def test_the_rule_function_still_returns_a_finding_without_the_question_tool():
    from cops.runtime.rules import risk_mitigation as rule
    ctx = {"tools": {"read_risk_register": {"result": S.risk_register()},
                     "calculate_risk_components": {"result": {"independent_verified_signals": ["policy_enforcement"],
                                                              "max_sensitivity_tier": 3,
                                                              "irreversible_operations": []}}},
           "upstream": {}, "events": []}
    f = rule(ctx)
    assert f["label"] == "EMERGING" and f["cop_specific"]["question_assessment"].startswith("unavailable")


def test_topology_doc_carries_the_runtime_edges_of_risk_mitigation():
    ok, diff = H._topology_matches()
    assert ok, diff
    order, after = H._order()
    assert after and len(order) == 18
