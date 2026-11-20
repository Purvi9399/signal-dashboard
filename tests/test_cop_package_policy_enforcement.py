"""Policy Enforcement package: contract, metric-to-question mapping, harness against ground truth, the
observation/interpretation layers, policy sources, memory, COP-to-COP influence and the decision record.
Synthetic data, no model call."""
import copy, json, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "collector"))

import pytest

from cops.packages.policy_enforcement import harness as H
from cops.runtime import cop_package as P, evaluation as E, evidence_assessment as EA, graph as G
from cops.runtime import synthetic as S
from cops.runtime.rules import DETERMINISTIC_TOOLS
from cops.runtime.tools import TOOLS
from cops.specs import BY_KEY

COP = "policy_enforcement"
PKG = os.path.join(os.path.dirname(__file__), "..", "collector", "cops", "packages", COP)
WORKBOOK_METRICS = (
    "insights.outside", "insights.sensitive_unasked", "insights.secrets", "insights.blocked", "insights.no_oversight",
    "insights.unattributed", "oversight.fast_approvals", "oversight.declined_inferred", "access.first_seen_mcp",
    "path.transition_novelty", "interaction.subagent_lanes", "interaction.mcp_servers", "telemetry.collector_coverage",
    "telemetry.source_agreement", "telemetry.turn_linkage", "telemetry.lane_linkage", "telemetry.unknown_shared",
    "policy_applicability_coverage", "entitlement_mismatch_rate", "unauthorized_action_attempts",
    "boundary_violation_count", "restricted_resource_access_rate", "approval_requirement_compliance",
    "approval_action_linkage", "policy_violation_rate", "unapproved_tool_usage", "unapproved_mcp_access",
    "unauthorized_interaction_route_count", "policy_evidence_sufficiency", "policy_decision_confidence",
    "policy_exception_rate")
NOT_CALCULATED = ("policy_applicability_coverage", "entitlement_mismatch_rate", "policy_exception_rate",
                  "approval_requirement_compliance", "unapproved_tool_usage", "unapproved_mcp_access",
                  "unauthorized_interaction_route_count", "policy_evidence_sufficiency")


@pytest.fixture(scope="module")
def bench():
    return H.run_all()


def run(name, art=None, cops=("runtime_monitoring", "interaction_discovery", COP), upstream=None):
    rows = H.rows_for(name)
    rt = E.make_runtime(f"t-{name}", "s1", copy.deepcopy(rows), H.WORLDS[name][1]() if art is None else art)
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
    assert set(pkg["mapping"]) == set(WORKBOOK_METRICS)
    assert set(BY_KEY[COP].metric_keys) <= set(pkg["mapping"])


def test_unbuilt_metrics_are_optional_not_calculated_and_name_their_missing_input():
    built = set(EA.signal_metrics.METRIC_IDS) | set(EA.signal_metrics.CATALOGUE_IDS) | {"telemetry.unknown_share"}
    pkg = EA.load_package(COP)
    for qid, q in pkg["questions"].items():
        for r in q["requires"]:
            assert r["metric"] in built or (r["kind"] == EA.OPTIONAL and r["metric"] in NOT_CALCULATED), (qid, r)
    for m in NOT_CALCULATED:
        assert m not in built and "Missing input" in pkg["mapping"][m]["note"]
    assert "typo" in pkg["mapping"]["telemetry.unknown_shared"]["note"]
    assert not pkg["mapping"]["telemetry.unknown_shared"].get("used_by")


def test_every_operation_is_deterministic_and_the_cop_calls_the_question_tool():
    assert {op["by"] for op in EA.load_package(COP)["operations"].values()} == {"deterministic"}
    assert DETERMINISTIC_TOOLS[COP][-1] == "assess_policy_questions" and "assess_policy_questions" in TOOLS


def test_every_package_file_exists():
    for f in ("METRICS.yaml", "SPEC.md", "INTERPRETATION.md", "MEMORY.md", "CONNECTIONS.md", "DECISIONS.md",
              "GROUND_TRUTH.yaml", "harness.py", "__init__.py"):
        assert os.path.exists(os.path.join(PKG, f)), f
    assert os.path.exists(os.path.join(PKG, "..", "..", "..", "..", ".claude", "skills", "cops-policy-enforcement",
                                       "SKILL.md"))


def test_labels_are_the_existing_vocabulary():
    assert set(EA.load_package(COP)["labels"].values()) == set(BY_KEY[COP].interpretations) | {"INCONCLUSIVE"}


# ---------------------------------------------------------------- harness

def test_harness_meets_every_ground_truth_expectation(bench):
    assert bench["failed"] == [], bench["failed"]
    assert bench["passed"] == len(bench["expectations"]) > 200


def test_every_scenario_publishes_a_valid_decision_with_five_findings(bench):
    for name, r in bench["results"].items():
        res = r["cop"]["result"]
        assert res["decision_problems"] == [], name
        assert [x["id"] for x in res["decision"]["question_findings"]] == [f"PE-{i}" for i in range(1, 6)], name


# ---------------------------------------------------------------- policy sources

def test_no_policy_source_claims_no_violation_and_stays_inconclusive(bench):
    for name in ("missing_policy_store", "missing_store_boundary_crossing", "unapproved_policy_only"):
        f = bench["results"][name]["cop"]["finding"]
        assert f["label"] == "INCONCLUSIVE" and f["status"] == "MISSING_DEPENDENCY", name
        assert not f.get("rejected_candidate"), name
        assert all(x["answer"] == "INCONCLUSIVE" for q, x in f["cop_specific"]["questions"].items()
                   if q in ("PE-1", "PE-2", "PE-3", "PE-5")), name


def test_a_boundary_crossing_is_observed_relevant_and_judged_only_by_the_policy(bench):
    """The same edit of /etc/hosts: undecided without a policy, permitted by one, a violation under another."""
    got = {n: bench["results"][n]["cop"]["finding"]["label"] for n in
           ("missing_store_boundary_crossing", "outside_workspace_permitted", "outside_workspace_denied")}
    assert got == {"missing_store_boundary_crossing": "INCONCLUSIVE", "outside_workspace_permitted": "ALLOWED",
                   "outside_workspace_denied": "VIOLATION"}
    for n in got:
        b = bench["results"][n]["cop"]["finding"]["cop_specific"]["questions"]["PE-2"]["basis"]
        assert "insights.outside" in b["policy_relevant"], n


def test_only_the_newest_approved_version_is_in_force(bench):
    ch = bench["results"]["policy_changed"]["cop"]["finding"]["cop_specific"]["questions"]["PE-1"]["basis"]["policy"]
    un = bench["results"]["policy_change_unapproved"]["cop"]["finding"]["cop_specific"]["questions"]["PE-1"]["basis"][
        "policy"]
    assert ch["in_force"]["version"] == un["in_force"]["version"] == "pol-2026-10-01"
    assert [x["why"] for x in un["not_in_force"]] == ["not approved"]


def test_conflicting_rules_resolve_by_the_existing_precedence_and_are_reported(bench):
    f = bench["results"]["conflicting_policy_rules"]["cop"]["finding"]
    c = f["cop_specific"]["questions"]["PE-1"]["basis"]["conflicts"]
    assert c and all(x["resolved_as"] == "DENY" for x in c)
    assert "knowledge_policy_management" in f["cop_specific"]["recipients"]


def test_memory_never_overrides_the_policy_in_force(bench):
    r = bench["results"]["policy_changed"]
    f = r["cop"]["finding"]
    assert r["remembered_label"] == "APPROVAL_REQUIRED" and f["label"] == "ALLOWED"
    p = f["cop_specific"]["questions"]["PE-5"]["basis"]["precedents"]
    assert p[0]["status"] == "SUPERSEDED"
    assert any("superseded" in c for c in f["cop_specific"]["contrary"])


# ---------------------------------------------------------------- upstream and downstream

def test_a_new_route_triggers_evaluation_never_an_automatic_violation(bench):
    labels = {n: bench["results"][n]["cop"]["finding"]["label"] for n in
              ("legitimate_new_route", "new_route_no_rule", "mcp_denied", "new_delegation_route")}
    assert labels == {"legitimate_new_route": "ALLOWED", "new_route_no_rule": "UNCERTAIN", "mcp_denied": "VIOLATION",
                      "new_delegation_route": "UNCERTAIN"}
    for n in labels:
        b = bench["results"][n]["cop"]["finding"]["cop_specific"]["questions"]["PE-4"]["basis"]
        assert b["routes_from"]["source"] == "interaction_discovery" and b["routes"], n


def test_interaction_discovery_routes_change_the_answer():
    """The same github calls: Interaction Discovery names the route either way; only the policy changes PE-4."""
    assert answers(run("legitimate_new_route"))["PE-4"] == "NEW_ROUTES_PERMITTED"
    assert answers(run("legitimate_new_route", {"policy_store": H.store(dict(H.GITHUB, effect="deny")),
                                                "entitlements": H.entitled("github")}))["PE-4"] == \
        "NEW_ROUTES_NOT_PERMITTED"


def test_a_new_route_finding_naming_no_route_is_not_read_as_none():
    route = {"finding_id": "f-id", "cop": "interaction_discovery", "label": "NEW_ROUTE", "status": "SUBSTANTIVE",
             "verified": True, "lineage": [], "claims": []}
    f = run("normal", upstream={"interaction_discovery": route})
    assert answers(f)["PE-4"] == "INCONCLUSIVE" and f["label"] == "INCONCLUSIVE"


def test_runtime_monitoring_insufficient_stops_allowed():
    rm = {"finding_id": "f-rm", "cop": "runtime_monitoring", "label": "INSUFFICIENT", "status": "SUBSTANTIVE",
          "verified": True, "lineage": ["telemetry.collector_coverage"], "claims": []}
    f = run("normal", upstream={"runtime_monitoring": rm})
    assert answers(f)["PE-5"] == "INCONCLUSIVE" and f["label"] == "INCONCLUSIVE"
    assert any(u["cop"] == "runtime_monitoring" for u in f["upstream_used"])


def test_audit_traceability_traces_the_policy_decision(bench):
    at = bench["results"]["downstream_audit_traceability"]["downstream"]["audit_traceability"]
    assert at["cop_specific"]["questions"]["AT-4"]["answer"] == "TRACEABLE"
    assert any(d["cop"] == COP for d in at["cop_specific"]["questions"]["AT-4"]["basis"]["decisions"])


def test_existing_consumer_fields_are_kept(bench):
    cs = bench["results"]["proposed_operation_denied"]["cop"]["finding"]["cop_specific"]
    assert {"policy_version", "denied", "proposed", "approval_needed", "blocked_by_hook"} <= set(cs)
    assert [x["decision"] for x in cs["proposed"]] == ["DENY"]


# ---------------------------------------------------------------- answers decide the label

def test_answers_decide_the_label(bench):
    lab = P.EA.load_package(COP)["labels"]
    for name, r in bench["results"].items():
        f = r["cop"]["finding"]
        five = f["cop_specific"]["questions"]["PE-5"]["answer"]
        cand = (f.get("rejected_candidate") or {}).get("label") or f["label"]
        if five != "INCONCLUSIVE":
            assert cand == lab[five], name


def test_the_label_follows_from_the_four_answers_in_every_scenario(bench):
    """Accepted 2026-11-20: the answers determine the label. PE-5 and the label are recomputed here from
    PE-1 to PE-4 with the documented order (SPEC.md PE-5), independently of cop_package."""
    lab = P.EA.load_package(COP)["labels"]
    for name, r in bench["results"].items():
        f = r["cop"]["finding"]
        a = answers(f)
        if a["PE-2"] == "OUTSIDE_AUTHORIZED_BOUNDARIES" or a["PE-4"] == "NEW_ROUTES_NOT_PERMITTED":
            want = "DENY"
        elif a["PE-3"] == "APPROVAL_MISSING":
            want = "REQUIRE_APPROVAL"
        elif (a["PE-1"] == "POLICY_PARTIALLY_APPLIES" or a["PE-2"] == "BOUNDARY_NOT_DEFINED"
              or a["PE-4"] == "NEW_ROUTES_UNDECIDED"):
            want = "UNDECIDED"
        elif "INCONCLUSIVE" in (a["PE-1"], a["PE-2"], a["PE-3"], a["PE-4"]):
            want = "INCONCLUSIVE"
        else:
            want = "ALLOW"
        assert a["PE-5"] == want, name
        cand = (f.get("rejected_candidate") or {}).get("label") or f["label"]
        assert cand == lab[want], name


def test_several_findings_keep_every_answer_and_the_label_order(bench):
    f = bench["results"]["several_findings"]["cop"]["finding"]
    assert f["label"] == "VIOLATION" and f["cop_specific"]["also_held"] == ["APPROVAL_REQUIRED", "UNCERTAIN"]
    assert answers(f) == {"PE-1": "POLICY_PARTIALLY_APPLIES", "PE-2": "OUTSIDE_AUTHORIZED_BOUNDARIES",
                          "PE-3": "APPROVAL_MISSING", "PE-4": "NEW_ROUTES_NOT_PERMITTED", "PE-5": "DENY"}


def test_instruction_text_in_a_tool_result_changes_nothing(bench):
    r = bench["results"]["adversarial_instruction_text"]
    assert r["cop"]["finding"]["label"] == r["without_text"]["label"] == "VIOLATION"


def test_the_entitlement_only_violation_is_proposed_and_held_back_by_vs01(bench):
    f = bench["results"]["mcp_not_entitled"]["cop"]["finding"]
    assert answers(f)["PE-5"] == "DENY" and f["rejected_candidate"]["label"] == "VIOLATION"
    assert f["label"] == "INCONCLUSIVE"


# ---------------------------------------------------------------- memory and the record

def test_allowed_writes_no_record_and_a_violation_writes_its_decisions(bench):
    assert bench["results"]["normal"]["cop"]["result"]["memory_write"]["written"] is False
    w = bench["results"]["outside_workspace_denied"]["cop"]["result"]["memory_write"]
    assert w["written"] and w["record_type"] == "enforcement_record"
    pay = bench["results"]["outside_workspace_denied"]["cop"]["finding"]["cop_specific"]["memory_payload"]
    assert pay["policy_version"] == "pol-2026-10-01" and pay["decisions"][0]["rules"] == ["P-write", "P-write-etc"]


def test_limitations_are_structured_and_serialize(bench):
    d = bench["results"]["entitlements_missing"]["cop"]["result"]["decision"]
    lims = d["uncertainty"]["limitations"]
    assert any(x["subject"] == "entitlements" and x["kind"] == "MISSING_EVIDENCE" for x in lims)
    assert any(x["kind"] == "NOT_CALCULATED" for x in lims)
    assert json.loads(json.dumps(d, default=str))["uncertainty"]["limitations"]
