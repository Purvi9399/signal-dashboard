"""Context & Memory package (retrieval only): contract, harness against ground truth, MemoryGateway access and tenant
isolation, provenance and retrieval, cross-COP consumption through the retrieval contract, the previous contract kept
for comparison, and topology. Synthetic data; no model is called."""
import copy, json, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "collector"))

import pytest

from cops.packages.context_memory import harness as H
from cops.runtime import cop_package as P, evidence_assessment as EA, graph as G, triage as TR
from cops.runtime.memory import MemoryGateway, MemoryAccessDenied, POLICIES
from cops.runtime.persistence import SQLiteStore
from cops.runtime.profiles import LLM_COPS, LLM_TOOLS
from cops.runtime.requirements import REQUIREMENTS
from cops.runtime.rules import DETERMINISTIC_TOOLS, RULES
from cops.runtime.tools import TOOLS, _memory_lookup
from cops.specs import BY_KEY, CONSUMES, FEEDBACK, consumers

COP = "context_memory"


@pytest.fixture(scope="module")
def bench():
    return H.run_all()


def store_with(keys, name="t"):
    s = SQLiteStore()
    return s, H.seed(s, keys, name)


def gw(store):
    return MemoryGateway(store, clock=lambda: H.day(9))


def retrieve(store, name="t", **over):
    return P.retrieve_memory(gw(store), H.request(name, over))


# ---------------------------------------------------------------- contract

def test_metrics_yaml_is_valid():
    assert P.validate(COP) == []


def test_package_registered_and_decided_without_model():
    assert COP in EA.PACKAGED
    assert DETERMINISTIC_TOOLS[COP] == ("assess_context_memory_questions",)
    assert "assess_context_memory_questions" in TOOLS and COP in RULES
    assert COP in LLM_COPS and LLM_TOOLS[COP] == ("assess_context_memory_questions",)
    assert EA.load_package(COP)["model_role"] == "none"
    assert COP not in REQUIREMENTS


def test_labels_retrieval_only_and_retired_labels_gone():
    spec = BY_KEY[COP]
    assert spec.interpretations == ("NO_RELEVANT_MEMORY", "MEMORY_RETURNED", "MEMORY_PARTIAL", "MEMORY_UNAVAILABLE")
    assert not set(spec.interpretations) & set(P.RETIRED_LABELS)
    assert spec.behavioural_question.startswith("What previously recorded information is relevant")
    assert all(TR.LABEL_ACTIONS[COP].get(l) is None for l in spec.interpretations)


def test_all_fifteen_metrics_visible_none_invented():
    pkg = EA.load_package(COP)
    reg = json.load(open(os.path.join(os.path.dirname(__file__), "..", "collector", "metric_registry.json")))["metrics"]
    names = {x["id"] for x in reg if COP in (x.get("consumers") or [])}
    assert len(names) == 15 and names <= set(pkg["mapping"])
    assert all(v["used_by"] == [] for v in pkg["mapping"].values())
    assert all(q["requires"] == [] for q in pkg["questions"].values())


def test_previous_contract_kept():
    pkg = EA.load_package(COP)
    prev = pkg["previous_contract"]
    assert set(P.RETIRED_LABELS) <= set(prev["labels"])
    from cops.runtime import rules
    assert hasattr(rules, "_context_memory_previous_contract")


# ---------------------------------------------------------------- harness

def test_harness_meets_every_expectation(bench):
    assert bench["failed"] == [], [(r["scenario"], r["expectation"], str(r["got"])[:200]) for r in bench["failed"]]
    assert len(bench["expectations"]) >= 300


def test_every_ground_truth_check_is_defined():
    names = {c for t in H.truth().values() for c in t.get("checks", [])}
    assert names <= set(H.CHECKS), sorted(names - set(H.CHECKS))


# ---------------------------------------------------------------- MemoryGateway and isolation

def test_read_include_excluded_is_opt_in():
    s, ids = store_with(["ad_val_m1", "ad_rej_m5", "ad_exp_m6"])
    plain = gw(s).read("anomaly_detection", "c", H.TENANT)
    assert "excluded_records" not in plain and plain["excluded"] == 2
    full = gw(s).read("anomaly_detection", "c", H.TENANT, include_excluded=True)
    assert sorted(x["reason"] for x in full["excluded_records"]) == ["EXPIRED", "REJECTED"]
    assert [r["record_id"] for r in full["established"]] == [ids["ad_val_m1"]]


def test_log_writes_one_access_row():
    s = SQLiteStore()
    gw(s).log(COP, "c1", H.TENANT, "retrieve", "anomaly_observation", "b1", "RETRIEVED", "x")
    rows = s.select("cop_memory_access_log")
    assert [(r["cop"], r["operation"], r["record_id"], r["outcome"]) for r in rows] == [(COP, "retrieve", "b1", "RETRIEVED")]


def test_other_tenant_never_visible_not_even_counted():
    s, ids = store_with(["ad_tenant_b"])
    b = retrieve(s)
    assert b["retrieval_status"] == P.NO_MEMORY_IN_SCOPE and b["in_scope_count"] == 0
    assert ids["ad_tenant_b"] not in json.dumps(b, default=str)


def test_cross_tenant_request_denied_by_gateway_and_logged():
    s, _ = store_with(["ad_val_m1"])
    b = retrieve(s, requested_tenant=H.TENANT_B)
    assert b["retrieval_status"] == P.ACCESS_DENIED and b["label"] == "MEMORY_UNAVAILABLE" and b["records"] == []
    assert any(r["outcome"] == "DENIED" and "cross-tenant" in r["reason"] for r in s.select("cop_memory_access_log"))


def test_unentitled_type_never_asked_of_gateway():
    s, _ = store_with(["ba_val_b1"])
    b = retrieve(s, memory_types=["behaviour_baseline"])
    assert b["retrieval_status"] == P.ACCESS_DENIED
    assert not [r for r in s.select("cop_memory_access_log") if r["operation"] == "read"]


def test_agent_scope_boundary_applies():
    s, ids = store_with(["ad_val_m1"])
    b = retrieve(s, requesting_cop="anomaly_detection", agent_scope="other@tenant-a.example")
    assert b["records"] == [] and b["retrieval_status"] == P.NO_MEMORY_IN_SCOPE


def test_tool_lookup_denial_behaviour_preserved():
    s, _ = store_with(["ad_val_m1"])
    from cops.runtime import evaluation as E
    rt = E.make_runtime("cm-tool", "s1", [], {}, None, store=s)
    ctx = {"memory": rt.memory, "cop": "incident_response", "tenant_id": rt.case.tenant_id,
           "case_id": rt.case.case_id, "session_id": "s1", "as_of": rt.case.computed_at, "run_id": rt.run_id}
    with pytest.raises(MemoryAccessDenied):
        _memory_lookup(ctx, "behaviour_baseline")
    got = _memory_lookup(ctx, "anomaly_observation")
    assert [r["record_id"] for r in got["established"]] and got["retrieval"]["retrieval_status"] == P.RETRIEVED


# ---------------------------------------------------------------- provenance and retrieval

def test_retrieval_never_changes_store():
    s, _ = store_with(["ad_val_m1", "ad_ver_m2", "ad_no_review", "ad_injected", "ad_model_claim"])
    before = H.snapshot(s)
    retrieve(s)
    retrieve(s, memory_types=["anomaly_observation"], max_results=1)
    assert H.snapshot(s) == before


def test_forged_validated_is_insufficient_and_never_established():
    s, ids = store_with(["ad_no_review"])
    b = retrieve(s, memory_types=["anomaly_observation"])
    v = b["records"][0]
    assert v["validation_status"] == "VALIDATED" and v["standing"] == P.INSUFFICIENTLY_SUPPORTED
    assert b["buckets"]["established"] == [] and v["may_corroborate"] is False and v["feedback"] is None
    assert b["retrieval_status"] == P.PARTIAL


def test_injected_text_withheld():
    s, ids = store_with(["ad_injected"])
    b = retrieve(s, memory_types=["anomaly_observation"])
    assert b["records"] == [] and [x["reason"] for x in b["excluded"]] == ["INSTRUCTION_SHAPED_CONTENT"]
    assert b["answers"]["CM-3"] == "NONE_ATTRIBUTABLE"


def test_storage_failure_distinct_from_missing():
    s, _ = store_with(["ad_val_m1"])
    b = P.retrieve_memory(MemoryGateway(H.FailingStore(s), clock=lambda: H.day(9)), H.request("t", {}))
    empty = retrieve(SQLiteStore())
    assert b["retrieval_status"] == P.STORAGE_UNAVAILABLE and empty["retrieval_status"] == P.NO_MEMORY_IN_SCOPE
    assert P.retrieve_memory(None, H.request("t", {}))["retrieval_status"] == P.STORAGE_UNAVAILABLE


def test_missing_identifiers_not_filled():
    b = P.retrieve_memory(gw(SQLiteStore()), H.request("t", {"case_id": None}))
    assert b["retrieval_status"] == P.INVALID_REQUEST and b["request"]["case_id"] is None
    assert [x["item"] for x in b["missing"]] == ["case_id"]


def test_deterministic_same_request_same_bundle():
    s, _ = store_with(["ad_val_m1", "ad_ver_m2", "ad_unv_m3"])
    a = retrieve(s, memory_types=["anomaly_observation"])
    b = retrieve(s, memory_types=["anomaly_observation"])
    strip = lambda x: {k: v for k, v in x.items() if k != "stages"}
    assert strip(a) == strip(b)


# ---------------------------------------------------------------- consumption and topology

def test_runtime_memory_view_routed_through_contract():
    s, ids = store_with(["ad_val_m1", "ad_no_review"])
    from cops.runtime import evaluation as E
    rt = E.make_runtime("cm-view", "s1", E._estate(E.spike_session("s1", 9)), {}, None, store=s)
    out = G.run_case(rt, cops=["runtime_monitoring", "anomaly_detection"])
    rows = [r for r in s.select("cop_memory_access_log") if r["operation"] == "retrieve"
            and r["reason"].startswith("for anomaly_detection:")]
    assert rows and f"memory:{ids['ad_val_m1']}" in rows[0]["reason"]
    assert f"memory:{ids['ad_no_review']}" in rows[0]["reason"]
    assert out["results"]["anomaly_detection"]["finding"]["label"]


def test_ba_reads_new_labels_as_context(bench):
    r = bench["results"]["runtime_ba_precedent"]
    ba = r["out"]["results"]["behavior_analytics"]["finding"]
    assert ba["cop_specific"]["questions"]["BA-4"]["answer"] == "NO_ALTERATION_SHOWN"


def test_topology_unchanged():
    assert CONSUMES[COP] == ("runtime_monitoring",)
    assert list(consumers(COP)) == ["behavior_analytics"]
    assert COP not in FEEDBACK and not any(COP in v for v in FEEDBACK.values())
    assert POLICIES[COP]["writes"] == ("context_integrity_finding",)
