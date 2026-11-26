"""Resource Management package: contract, metric-to-question mapping, harness against ground truth, the
observation/interpretation layers, the entitlement source, baselines, attribution, memory, COP-to-COP influence
and the decision record. Synthetic data, no model call."""
import copy, json, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "collector"))

import pytest

from cops.packages.resource_management import harness as H
from cops.runtime import cop_package as P, evaluation as E, evidence_assessment as EA, graph as G
from cops.runtime import synthetic as S
from cops.runtime.rules import DETERMINISTIC_TOOLS
from cops.runtime.tools import TOOLS
from cops.specs import BY_KEY
from cops.authority import LEGACY_TO_TYPED

COP = "resource_management"
PKG = os.path.join(os.path.dirname(__file__), "..", "collector", "cops", "packages", COP)
# COPS- implementation 1.xlsx, COMPLETE_DELEGATION_DATA row 156
WORKBOOK_METRICS = (
    "insights.token_outlier", "insights.duration_outlier", "insights.stalled", "insights.repetition", "insights.retry",
    "insights.failed", "activity.action_rate_ratio", "interaction.subagent_lanes", "interaction.mcp_servers",
    "telemetry.collector_coverage", "telemetry.unknown_share", "token_consumption_per_session",
    "token_consumption_per_turn", "token_budget_utilization", "token_quota_exceeded", "cost_per_session",
    "cost_budget_utilization", "execution_duration_per_session", "execution_duration_threshold_breach",
    "tool_timeout_rate", "resource_consumption_rate", "resource_capacity_utilization", "resource_concurrency_level",
    "resource_stall_rate", "resource_redundant_execution_cost", "model_selection_compliance",
    "subagent_resource_consumption", "resource_quota_compliance", "resource_limit_proximity",
    "resource_threshold_breach_count", "resource_intervention_required")
NOT_CALCULATED = ("token_budget_utilization", "token_quota_exceeded", "cost_budget_utilization",
                  "resource_capacity_utilization", "model_selection_compliance", "resource_quota_compliance",
                  "resource_limit_proximity", "resource_threshold_breach_count")


@pytest.fixture(scope="module")
def bench():
    return H.run_all()


def run(rows, art=None, cops=("runtime_monitoring", COP), case="t", upstream=None, store=None):
    rt = E.make_runtime(f"t-{case}", "s1", copy.deepcopy(rows), H.AMPLE if art is None else art, store=store)
    if upstream is not None:
        return G.run_cop(rt, COP, upstream=upstream)["finding"]
    return G.run_case(rt, cops=list(cops))["results"][COP]["finding"]


def answers(f):
    return {q: x["answer"] for q, x in f["cop_specific"]["questions"].items()}


# ---------------------------------------------------------------- contract and mapping

def test_metrics_yaml_is_valid():
    assert P.validate(COP) == []


def test_every_workbook_metric_is_mapped():
    pkg = EA.load_package(COP)
    assert set(pkg["mapping"]) == set(WORKBOOK_METRICS) and len(WORKBOOK_METRICS) == 31
    assert set(BY_KEY[COP].metric_keys) <= set(pkg["mapping"])
    # the spec's metric_keys are what the legacy rule judges, and what this package counts toward the label
    assert set(pkg["burn"]) | set(pkg["waste"]) == set(BY_KEY[COP].metric_keys)


def test_unbuilt_metrics_are_optional_not_calculated_and_name_their_missing_input():
    built = set(EA.signal_metrics.METRIC_IDS) | set(EA.signal_metrics.CATALOGUE_IDS) | {"telemetry.unknown_share"}
    pkg = EA.load_package(COP)
    for qid, q in pkg["questions"].items():
        for r in q["requires"]:
            assert r["metric"] in built or (r["kind"] == EA.OPTIONAL and r["metric"] in NOT_CALCULATED), (qid, r)
    for m in NOT_CALCULATED:
        assert m not in built and "Missing input" in pkg["mapping"][m]["note"], m
    assert not pkg["mapping"]["resource_intervention_required"].get("used_by")
    assert "category O" in pkg["mapping"]["resource_intervention_required"]["note"]


def test_every_operation_is_deterministic_and_the_cop_calls_the_question_tool():
    assert {op["by"] for op in EA.load_package(COP)["operations"].values()} == {"deterministic"}
    assert DETERMINISTIC_TOOLS[COP] == ("compute_consumption", "get_quota", "assess_resource_questions")
    assert "assess_resource_questions" in TOOLS and COP in EA.PACKAGED


def test_every_package_file_exists():
    for f in ("METRICS.yaml", "SPEC.md", "INTERPRETATION.md", "MEMORY.md", "CONNECTIONS.md", "DECISIONS.md",
              "GROUND_TRUTH.yaml", "harness.py", "__init__.py"):
        assert os.path.exists(os.path.join(PKG, f)), f
    assert os.path.exists(os.path.join(PKG, "..", "..", "..", "..", ".claude", "skills", "cops-resource-management",
                                       "SKILL.md"))


def test_labels_are_the_existing_vocabulary():
    labels = set()
    for v in EA.load_package(COP)["labels"].values():
        labels |= set(v) if isinstance(v, list) else {v}
    assert labels == set(BY_KEY[COP].interpretations) | {"INCONCLUSIVE"}


# ---------------------------------------------------------------- harness

def test_harness_meets_every_ground_truth_expectation(bench):
    assert bench["failed"] == [], bench["failed"]
    assert bench["passed"] == len(bench["expectations"]) > 150


def test_every_scenario_publishes_a_valid_decision_with_five_findings(bench):
    for name, r in bench["results"].items():
        res = r["cop"]["result"]
        assert res["decision_problems"] == [], name
        assert [x["id"] for x in res["decision"]["question_findings"]] == [f"RS-{i}" for i in range(1, 6)], name
        assert not res["llm_invocations"], name


# ---------------------------------------------------------------- answers decide the label

def test_the_label_follows_from_the_answers_in_every_scenario(bench):
    """RS-5 and the label recomputed from RS-1 to RS-4 and Runtime Monitoring with the documented order
    (INTERPRETATION.md), independently of cop_package."""
    for name, r in bench["results"].items():
        f = r["cop"]["finding"]
        a = answers(f)
        rm = r["upstream"].get("runtime_monitoring") or {}
        if a["RS-1"] == "ABOVE_PERMITTED":
            want = "INTERVENTION_REQUIRED"
        elif (a["RS-1"] in ("ABOVE_EXPECTED", "ELEVATED") or a["RS-2"] in ("OUTSIDE_THRESHOLDS", "ELEVATED")
              or a["RS-3"] in ("WASTE_OBSERVED", "ELEVATED")):
            want = "ATTENTION"
        elif rm.get("label") == "INSUFFICIENT" and rm.get("verified"):
            want = "UNDETERMINED"
        elif any(a[q] in ("INCONCLUSIVE", "BASELINE_INSUFFICIENT") for q in ("RS-1", "RS-2", "RS-3", "RS-4")):
            want = "UNDETERMINED"
        else:
            want = "NO_INTERVENTION"
        assert a["RS-5"] == want, name
        burn = [x for x in f["cop_specific"]["questions"]["RS-5"]["basis"]["hot"]
                if x["metric"] in ("insights.token_outlier", "activity.action_rate_ratio")]
        label = {"INTERVENTION_REQUIRED": "UNAUTHORIZED", "ATTENTION": "EXCESSIVE" if burn else "INEFFICIENT",
                 "UNDETERMINED": "INDETERMINATE", "NO_INTERVENTION": "NORMAL"}[want]
        assert (f.get("gated_label") or f["label"]) == label, name


def test_high_consumption_is_never_unauthorized_without_the_quota(bench):
    for name, r in bench["results"].items():
        f = r["cop"]["finding"]
        p = f["cop_specific"]["questions"]["RS-1"]["basis"].get("permitted") or {}
        if f["label"] == "UNAUTHORIZED":
            assert p["status"] == "ABOVE_QUOTA", name
        if p.get("status") != "ABOVE_QUOTA":
            assert f["label"] != "UNAUTHORIZED", name


def test_the_rule_matches_the_legacy_reading_where_its_semantics_apply():
    """Where both read the same evidence and the legacy rule's semantics are kept, the label, severity and
    action are cops/interpret.py's (confidence is rounded to three places, not two)."""
    from cops.interpret import RuleInterpreter
    from cops.runtime.rules import evidence_conf
    for name in ("normal_within_quota", "token_spike_within_quota", "rate_spike", "slow_step", "stalled_step",
                 "burn_and_waste", "elevated_tokens_warning", "spike_on_incomplete_telemetry"):
        rows, art = H.WORLDS[name][0](), H.WORLDS[name][1]()
        rt = E.make_runtime(f"leg-{name}", "s1", rows, art)
        out = G.run_case(rt, cops=["runtime_monitoring", COP])
        f = out["results"][COP]["finding"]
        evs = [e for e in rt.case.case_events() if e.metric_id in BY_KEY[COP].metric_keys]
        leg = RuleInterpreter().resource_management(evs, {}, {}, evidence_conf(
            {"runtime_monitoring": out["findings"]["runtime_monitoring"]}))
        assert (f["label"], f["severity"]) == (leg["interpretation"], leg["severity"]), name
        assert out["results"][COP]["triage"]["action_selection"]["original_proposal"] == \
            LEGACY_TO_TYPED.get(leg["action"], leg["action"]), name
        assert abs(f["confidence"] - leg["confidence"]) < 0.01, name


# ---------------------------------------------------------------- entitlement and evidence changes

def test_a_tighter_quota_changes_the_decision_and_nothing_else():
    rows = H.history() + H.session(tokens=40000)
    ample, tight = run(rows, H.AMPLE, case="ample"), run(rows, H.quota(20_000), case="tight")
    assert (ample["label"], tight["label"]) == ("EXCESSIVE", "UNAUTHORIZED")
    a, b = answers(ample), answers(tight)
    assert {k for k in a if a[k] != b[k]} == {"RS-1", "RS-5"}
    assert ample["confidence"] == tight["confidence"]


def test_package_tokens_and_the_quota_tool_read_the_same_quantity():
    rows = H.history() + H.session(tokens=40000)
    rt = E.make_runtime("t-same", "s1", rows, H.quota(20_000))
    f = G.run_case(rt, cops=["runtime_monitoring", COP])["results"][COP]["finding"]
    assert f["cop_specific"]["consumption"]["tokens"] == \
        f["cop_specific"]["questions"]["RS-1"]["basis"]["permitted"]["tokens"] == 40000


def test_runtime_monitoring_insufficient_stops_normal_but_not_a_positive_reading(bench):
    assert bench["results"]["incomplete_telemetry"]["cop"]["finding"]["label"] == "INDETERMINATE"
    assert bench["results"]["spike_on_incomplete_telemetry"]["cop"]["finding"]["label"] == "EXCESSIVE"


def test_a_missing_meter_is_not_thrift(bench):
    f = bench["results"]["missing_token_measurements"]["cop"]["finding"]
    assert f["label"] == "INDETERMINATE"
    assert answers(f)["RS-1"] == "INCONCLUSIVE"


# ---------------------------------------------------------------- baseline methodology (U-RS1)

def test_the_baseline_stays_self_inclusive_and_the_leave_one_out_reading_is_recorded(bench):
    for name, r in bench["results"].items():
        b = r["cop"]["finding"]["cop_specific"]["questions"]["RS-1"]["basis"]
        if "measures" not in b:
            continue
        m = b["measures"][0]
        if m.get("baseline"):
            assert m["baseline"]["method"] == "self_inclusive", name
        lo = b.get("leave_one_out")
        if lo:
            assert lo["used"] == "self_inclusive" and lo["leave_one_out"]["method"] == P.AD_BASELINE_METHOD, name


def test_the_comparison_uses_anomaly_detections_shared_function():
    rows = H.history(4) + H.history(3, tokens=40000, prefix="new", day0=5) + H.session(tokens=40000)
    cfg = EA.signal_metrics.load_config()
    events, _ = EA.session_inputs(rows, "s1", cfg, None)
    sessions = EA._computed(rows, cfg, None)[1]
    s = next(x for x in sessions if x.id == "s1")
    c = P.rs_baseline_comparison(sessions, s, cfg, events)
    loo = {e["metric_id"]: e for e in P.ad_leave_one_out(sessions, s, cfg, list(events.values()))}
    assert c["insights.token_outlier"]["leave_one_out"]["value"] == loo["insights.token_outlier"]["value"] == 40.0
    assert c["insights.token_outlier"]["self_inclusive"]["state"] == events["insights.token_outlier"]["state"] == "NORMAL"


# ---------------------------------------------------------------- attribution through Interaction Discovery

def test_attribution_needs_every_delegation_link_recorded(bench):
    st = {n: bench["results"][n]["cop"]["finding"]["cop_specific"]["attribution"]["status"]
          for n in ("attribution_verified", "attribution_timing_inferred", "attribution_mixed_links",
                    "attribution_without_interaction_discovery")}
    assert st == {"attribution_verified": "ATTRIBUTED", "attribution_timing_inferred": "UNATTRIBUTED",
                  "attribution_mixed_links": "UNATTRIBUTED", "attribution_without_interaction_discovery": "UNATTRIBUTED"}


def test_an_inferred_link_in_a_complete_graph_still_does_not_attribute(bench):
    """Defence beyond ID-2: a verified Interaction Discovery finding reading FULLY_RECONSTRUCTED whose links
    include one inferred from timing (a constructed finding; ID-2 itself reads such a graph as partial)."""
    r = bench["results"]["attribution_verified"]
    up = copy.deepcopy(r["upstream"])
    links = up["interaction_discovery"]["cop_specific"]["questions"]["ID-2"]["basis"]["links"]
    lk = next(x for x in links if x["link"] == "main>sub-b")
    lk["basis"] = "INFERRED"
    f = run(H.history() + H.delegated(), upstream=up, case="inferred-in-complete")
    a = f["cop_specific"]["attribution"]
    assert a["status"] == "UNATTRIBUTED" and "main>sub-b" in a["reason"]
    assert not any(u["cop"] == "interaction_discovery" for u in f["upstream_used"])
    lims = {(x["question"], x["subject"], x["kind"]) for x in f["cop_specific"]["limitations"]}
    assert ("RS-4", "main>sub-b", "INFERRED_RELATIONSHIP") in lims


# ---------------------------------------------------------------- memory

def test_memory_is_listed_and_never_decides(bench):
    r = bench["results"]["memory_precedent"]
    f = r["cop"]["finding"]
    assert f["cop_specific"]["questions"]["RS-5"]["basis"]["precedents"]
    assert (f["label"], f["confidence"], answers(f)) == \
        (r["without_memory"]["label"], r["without_memory"]["confidence"], answers(r["without_memory"]))


def test_a_quiet_reading_writes_no_record_and_a_reading_above_bounds_writes_its_answers(bench):
    assert bench["results"]["normal_within_quota"]["cop"]["result"]["memory_write"]["written"] is False
    w = bench["results"]["quota_exceeded"]["cop"]["result"]["memory_write"]
    assert w["written"] and w["record_type"] == "usage_baseline"
    pay = bench["results"]["quota_exceeded"]["cop"]["finding"]["cop_specific"]["memory_payload"]
    assert pay["permitted"] == "ABOVE_QUOTA" and pay["answers"]["RS-5"] == "INTERVENTION_REQUIRED"
    assert all(b.endswith("/self_inclusive") for b in pay["baselines"])


# ---------------------------------------------------------------- downstream

def test_downstream_cops_receive_the_verified_finding(bench):
    r = bench["results"]["downstream_consumers"]
    got = {c["consumer"] for c in r["consumption"] if c["verified"] and c["label"] == "EXCESSIVE"}
    assert got == {"risk_mitigation", "incident_response"}
    # Risk Mitigation counts it as correlated: its lineage shares insights.token_outlier with Risk Mitigation's
    assert COP in r["risk_components"]["correlated_not_counted"]


def test_existing_consumer_fields_are_kept(bench):
    cs = bench["results"]["attribution_verified"]["cop"]["finding"]["cop_specific"]
    assert {"consumption", "attribution", "questions", "memory_payload", "recipients"} <= set(cs)
    assert set(cs["attribution"]) == {"status", "source_finding", "interaction", "subagent_lanes", "parallel_lanes",
                                      "calls_by_actor", "mcp_calls", "mcp_call_share", "subagent_call_share"}


def test_instruction_text_in_a_tool_result_changes_nothing(bench):
    r = bench["results"]["adversarial_instruction_text"]
    assert r["cop"]["finding"]["label"] == r["without_text"]["label"] == "EXCESSIVE"


def test_limitations_are_structured_and_serialize(bench):
    d = bench["results"]["normal_without_quota"]["cop"]["result"]["decision"]
    lims = d["uncertainty"]["limitations"]
    assert any(x["subject"] == "budget_or_quota" and x["kind"] == "MISSING_EVIDENCE" for x in lims)
    assert any(x["kind"] == "NOT_CALCULATED" for x in lims)
    assert json.loads(json.dumps(d, default=str))["uncertainty"]["limitations"]
