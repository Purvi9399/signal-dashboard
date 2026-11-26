"""Classification & Tagging package: contract, metric-to-question mapping, harness against ground truth, the
observation/interpretation layers, the taxonomy, deterministic versus model classification, upstream context,
entity linkage, memory, downstream influence and the decision record. Synthetic data; every model reply is a
scripted fixture (simulated), no paid model call."""
import copy, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "collector"))

import pytest

from cops.packages.classification_tagging import harness as H
from cops.runtime import cop_package as P, evaluation as E, evidence_assessment as EA, graph as G
from cops.runtime import synthetic as S
from cops.runtime.cop_package import DECIDED_WITHOUT_MODEL
from cops.runtime.rules import DETERMINISTIC_TOOLS, classification_tagging as legacy_rule
from cops.runtime.tools import TOOLS
from cops.specs import BY_KEY, CONSUMES, SPEC_CONSUMES
from cops.interpret import RuleInterpreter

COP = "classification_tagging"
PKG = os.path.join(os.path.dirname(__file__), "..", "collector", "cops", "packages", COP)
# COPS- implementation 1.xlsx, COMPLETE_DELEGATION_DATA row 117
WORKBOOK_METRICS = (
    "insights.outside", "insights.sensitive_unasked", "insights.secrets", "insights.blocked", "insights.no_oversight",
    "insights.failed", "insights.repetition", "insights.retry", "insights.stalled", "insights.reverted",
    "insights.lockfile", "insights.unattributed", "insights.token_outlier", "insights.duration_outlier",
    "oversight.fast_approvals", "oversight.declined_inferred", "access.first_seen_mcp", "path.transition_novelty",
    "activity.action_rate_ratio", "interaction.subagent_lanes", "interaction.mcp_servers",
    "telemetry.collector_coverage", "telemetry.source_agreement", "telemetry.turn_linkage", "telemetry.lane_linkage",
    "telemetry.unknown_share")


@pytest.fixture(scope="module")
def bench():
    return H.run_all()


def run(rows, art=None, cops=(COP,), case="t", script=None, upstream=None):
    rt = E.make_runtime(f"t-{case}", "s1", copy.deepcopy(rows), H.TAX if art is None else art,
                        H.FX.Script({COP: script}) if script else None)
    if upstream is not None:
        return G.run_cop(rt, COP, upstream=upstream)["result"]["finding"]
    return G.run_case(rt, cops=list(cops))["results"][COP]["finding"]


def answers(f):
    return {q: x["answer"] for q, x in f["cop_specific"]["questions"].items()}


# ---------------------------------------------------------------- contract and mapping

def test_metrics_yaml_is_valid():
    assert P.validate(COP) == []


def test_every_workbook_metric_is_mapped_and_built():
    pkg = EA.load_package(COP)
    assert set(pkg["mapping"]) == set(WORKBOOK_METRICS) and len(WORKBOOK_METRICS) == 26
    built = set(EA.signal_metrics.METRIC_IDS) | set(EA.signal_metrics.CATALOGUE_IDS) | {"telemetry.unknown_share"}
    assert set(WORKBOOK_METRICS) <= built
    # the spec's metric_keys are what the legacy rule judges, and the only metrics that indicate a family
    assert {m for d in pkg["families"].values() for m in d["metrics"]} == set(BY_KEY[COP].metric_keys)


def test_spec_missing_metrics_are_not_calculated_with_their_missing_input():
    pkg = EA.load_package(COP)
    assert set(pkg["not_calculated"]) == set(BY_KEY[COP].missing_metrics)
    assert all("Missing input" in v for v in pkg["not_calculated"].values())


def test_one_operation_uses_the_model_and_the_cop_calls_the_question_tool():
    ops = EA.load_package(COP)["operations"]
    assert [k for k, v in ops.items() if v["by"] == "llm"] == ["interpret_ambiguous_event"]
    assert DETERMINISTIC_TOOLS[COP] == ("query_taxonomy", "inspect_event_metadata", "assess_classification_questions")
    assert "assess_classification_questions" in TOOLS and COP in EA.PACKAGED


def test_every_package_file_exists():
    for f in ("METRICS.yaml", "SPEC.md", "INTERPRETATION.md", "MEMORY.md", "CONNECTIONS.md", "DECISIONS.md",
              "GROUND_TRUTH.yaml", "harness.py", "__init__.py"):
        assert os.path.exists(os.path.join(PKG, f)), f
    assert os.path.exists(os.path.join(PKG, "..", "..", "..", "..", ".claude", "skills",
                                       "cops-classification-tagging", "SKILL.md"))


def test_labels_are_the_existing_vocabulary():
    labels = set()
    for v in EA.load_package(COP)["labels"].values():
        labels |= set(v) if isinstance(v, list) else {v}
    assert labels <= set(BY_KEY[COP].interpretations) | {"INCONCLUSIVE"}
    assert not labels & {"LOOPS", "OUTCOME", "COST"}


def test_upstream_edges_are_the_canonical_ones_plus_the_three_named():
    assert SPEC_CONSUMES[COP] == ("anomaly_detection", "agent_lifecycle_management")
    assert CONSUMES[COP] == P.CT_UPSTREAM


# ---------------------------------------------------------------- harness

def test_harness_meets_every_ground_truth_expectation(bench):
    assert bench["failed"] == [], [(r["scenario"], r["expectation"]) for r in bench["failed"]]
    assert bench["passed"] == len(bench["expectations"]) == 165


def test_every_scenario_publishes_a_valid_decision_with_five_findings(bench):
    for name, r in bench["results"].items():
        res = r["cop"]["result"]
        assert res["decision_problems"] == [], name
        if name not in ("no_taxonomy",):
            assert [x["id"] for x in res["decision"]["question_findings"]] == ["CT-1", "CT-2", "CT-3", "CT-4", "CT-5"]


def test_no_paid_model_is_called(bench):
    for name, r in bench["results"].items():
        assert all(x.get("simulated") for x in r["llm_invocations"]), name
        assert all(x.get("provider") in ("fixture", "none") for x in r["llm_invocations"]), name


# ---------------------------------------------------------------- the answers decide the label

def test_the_label_is_ct5s_answer(bench):
    for name, r in bench["results"].items():
        f = r["cop"]["finding"]
        a = answers(f).get("CT-5")
        if a == "CLASSIFIED":
            assert f["label"] in ("BOUNDARY", "INTEGRITY", "OVERSIGHT"), name
        elif a == "UNCLASSIFIED":
            assert f["label"] == "UNCLASSIFIED", name
        elif a in ("UNDETERMINED", "INCONCLUSIVE"):
            assert f["label"] == "INCONCLUSIVE", name


def test_one_candidate_is_decided_without_a_model_and_two_need_one(bench):
    one = bench["results"]["boundary_outside"]["cop"]["finding"]
    two = bench["results"]["overlap_without_model"]["cop"]["finding"]
    assert one["cop_specific"]["stop_reason"] == DECIDED_WITHOUT_MODEL
    assert two["cop_specific"].get("stop_reason") != DECIDED_WITHOUT_MODEL
    assert two["baseline"]["label"] == "BOUNDARY"   # the legacy precedence reading stays the baseline


def test_the_model_label_must_be_a_candidate():
    q = {"CT-5": {"answer": "AMBIGUOUS", "basis": {"candidates": ["BOUNDARY", "INTEGRITY"]},
                  "sufficiency_reasons": [{"code": P.INTERPRETATION_REQUIRED}]}}
    base = {"cop_specific": {"questions": q, "memory_payload": {}}}
    from cops.runtime.contracts import ORIGIN_LLM
    ok = P.interpreted_classification_tagging({"origin": ORIGIN_LLM, "status": "SUBSTANTIVE", "label": "INTEGRITY",
                                              "verified": True, "cop_specific": {}}, base)
    bad = P.interpreted_classification_tagging({"origin": ORIGIN_LLM, "status": "SUBSTANTIVE", "label": "OVERSIGHT",
                                               "verified": True, "cop_specific": {}}, base)
    assert ok["label"] == "INTEGRITY" and ok["cop_specific"]["questions"]["CT-5"]["answer"] == "CLASSIFIED"
    assert bad["label"] == "INCONCLUSIVE" and bad["status"] == "INCONCLUSIVE"
    assert bad["rejected_candidate"]["label"] == "OVERSIGHT"
    assert bad["cop_specific"]["questions"]["CT-5"]["sufficiency_reasons"][-1]["code"] == P.OUTSIDE_CANDIDATES


def test_evaluation_case_c08_still_reads_integrity_through_the_model():
    out = E.run_all(["C08"])
    assert len(out) == 1 and out[0]["passed"], out


def test_missing_metadata_is_not_unclassified_but_positive_evidence_stands(bench):
    quiet = bench["results"]["metadata_missing_quiet"]["cop"]["finding"]
    secret = bench["results"]["metadata_missing_with_secret"]["cop"]["finding"]
    assert quiet["label"] == "INCONCLUSIVE" and answers(quiet)["CT-5"] == "UNDETERMINED"
    assert secret["label"] == "INTEGRITY"


def test_legacy_rule_unchanged_where_every_counted_metric_is_judged(bench):
    """Against cops/interpret.py's rule on the same events: the label is the same wherever all four counted
    metrics are judged and at most one family is indicated (INTERPRETATION.md, labels that changed)."""
    ri = RuleInterpreter()
    for name in ("normal_unclassified", "boundary_outside", "boundary_two_indicators", "integrity_secret",
                 "oversight_blocked", "novel_mcp_access", "source_disagreement"):
        rows, art = H.WORLDS[name][0](), H.WORLDS[name][1]()
        rt = E.make_runtime(f"t-legacy-{name}", "s1", rows, art)
        out = ri.classification_tagging(rt.case.case_events(BY_KEY[COP].metric_keys), {}, {}, 0.9)
        f = bench["results"][name]["cop"]["finding"]
        assert (out["interpretation"], round(out["confidence"], 2)) == (f["label"], round(f["confidence"], 2)), name


# ---------------------------------------------------------------- upstream, entities, memory, downstream

def test_an_unverified_upstream_finding_is_listed_and_not_used():
    rows = H.history() + H.session(H.OUTSIDE)
    fake = {"label": "VIOLATION", "verified": False, "status": "SUBSTANTIVE", "finding_id": "f-pe", "lineage": [],
            "cop_specific": {}}
    f = run(rows, upstream={"policy_enforcement": fake})
    u = f["cop_specific"]["questions"]["CT-5"]["basis"]["upstream"]["policy_enforcement"]
    assert u["present"] and u["used"] is False and u["counted_as"].startswith("not used")
    assert f["label"] == "BOUNDARY" and f["confidence"] == 0.75


def test_a_verified_policy_finding_is_correlated_and_never_counted():
    rows = H.history() + H.session(H.OUTSIDE)
    pe = {"label": "VIOLATION", "verified": True, "status": "SUBSTANTIVE", "finding_id": "f-pe",
          "lineage": ["tool_call:x"], "cop_specific": {}}
    f = run(rows, upstream={"policy_enforcement": pe})
    u = f["cop_specific"]["questions"]["CT-5"]["basis"]["upstream"]["policy_enforcement"]
    assert u["used"] and "correlated" in u["counted_as"] and "insights.outside" in u["counted_as"]
    assert f["label"] == "BOUNDARY" and f["confidence"] == 0.75


def test_a_lane_is_linked_only_through_a_recorded_link(bench):
    rec = bench["results"]["subagent_linkage_recorded"]["cop"]["finding"]
    inf = bench["results"]["subagent_linkage_inferred"]["cop"]["finding"]
    e = lambda f: f["cop_specific"]["questions"]["CT-5"]["basis"]["entities"][0]["linkage"]["status"]
    assert (e(rec), e(inf)) == ("RECORDED", "NOT_LINKED")
    assert rec["confidence"] == inf["confidence"] == 0.75    # linkage is a limitation, not a discount


def test_memory_is_comparison_only(bench):
    r = bench["results"]["memory_precedent"]
    f = r["cop"]["finding"]
    assert f["cop_specific"]["questions"]["CT-5"]["basis"]["precedents"][0]["use"] == "comparison only, never proof"
    assert (f["label"], f["confidence"]) == (r["without_memory"]["label"], r["without_memory"]["confidence"])


def test_a_classified_event_writes_its_answers_and_the_quiet_one_writes_nothing(bench):
    w = bench["results"]["integrity_secret"]["cop"]["result"]["memory_write"]
    assert w["written"] and w["record_type"] == "classification_example"
    pay = bench["results"]["integrity_secret"]["cop"]["finding"]["cop_specific"]["memory_payload"]
    assert pay["answers"]["CT-5"] == "CLASSIFIED" and pay["decided_by"] == "package"
    assert pay["taxonomy_version"] == "tax-2026-10" and pay["entities"]
    assert not bench["results"]["normal_unclassified"]["cop"]["result"]["memory_write"].get("written")


def test_evidence_changes_move_the_classification(bench):
    r = bench["results"]["evidence_change_changes_classification"]
    assert (r["cop"]["finding"]["label"], r["without_secret"]["label"], r["without_family"]["label"]) == \
        ("INTEGRITY", "UNCLASSIFIED", "INCONCLUSIVE")


def test_downstream_reads_the_finding_and_incident_response_reading_is_unchanged(bench):
    r = bench["results"]["downstream_consumers"]
    fid = r["cop"]["finding"]["finding_id"]
    assert {c["consumer"] for c in r["consumption"] if c["finding_id"] == fid} >= {"incident_response",
                                                                                     "governance_intelligence"}
    assert r["rule_baselines"]["incident_response"] == r["rule_baselines_without"]["incident_response"]


def test_the_legacy_rule_function_still_returns_a_finding_without_the_question_tool():
    """rules.classification_tagging keeps the legacy reading when the question tool is absent."""
    rows = H.history() + H.session(H.OUTSIDE)
    rt = E.make_runtime("t-noq", "s1", rows, H.TAX)
    ctx = {"events": rt.case.case_events(BY_KEY[COP].metric_keys), "upstream": {}, "tools": {}, "case": rt.case,
           "spec": BY_KEY[COP]}
    f = legacy_rule(ctx)
    assert f["label"] == "BOUNDARY" and "unavailable" in f["cop_specific"]["question_assessment"]
