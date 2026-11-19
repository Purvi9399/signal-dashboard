"""Real-data replay adapter, evidence profile and controlled evolution.
Rows are synthetic, shaped like the observables table; no Supabase call is made."""
import copy, json, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "collector"))

import pytest

from cops.packages.runtime_monitoring import harness as H
from cops.runtime import evaluation as E, evolution as V, graph as G, replay as R
from cops.runtime.activation import HUMAN, NotApproved
from cops.runtime.memory import VERIFIED
from cops.runtime.persistence import SQLiteStore
from cops.specs import BY_KEY

AT = H.AT
CFG = os.path.join(os.path.dirname(__file__), "..", "collector", "metrics_config.json")


def snap(*sessions, raw=True, capability=None):
    rows = H.estate(*sessions)
    return R.snapshot(rows, {"kind": "synthetic", "note": "harness rows shaped as observables"},
                      H.raw_store(rows) if raw else None, capability)


def run(s, cfg=None, store=None, at=AT):
    return R.replay(s, at, cfg=cfg or H.cfg(), store=store)


# ---------------------------------------------------------------- same pipeline as the harness

def test_replay_gives_the_harness_finding_for_the_same_rows():
    for name in ("normal", "unreliable_provenance", "partial_collector_failure", "adversarial_fabricated_raw_ref"):
        rows = H.BUILD[name]()
        rep = R.replay(R.snapshot(rows, {"kind": "synthetic"}, H.raw_store(rows)), AT, cfg=H.cfg())
        rt = E.make_runtime(f"direct-{name}", "s1", rows, artifacts={"source_events": H.raw_store(rows)},
                            cfg=H.cfg(), computed_at=AT)
        direct = G.run_case(rt, cops=list(R.SUPPORTED))["findings"]
        for cop in R.SUPPORTED:
            got = rep["results"]["s1"][cop]
            assert (got["label"], got["verified"]) == (direct[cop]["label"], direct[cop]["verified"]), (name, cop)
        ea = direct["runtime_monitoring"]["cop_specific"]["evidence_assessment"]
        assert rep["results"]["s1"]["runtime_monitoring"]["question_classes"] == ea["classes"], name


def test_only_the_two_supported_cops_replay():
    with pytest.raises(ValueError):
        R.replay(snap(H.session()), AT, cops=("policy_enforcement",))
    with pytest.raises(ValueError):
        R.replay(snap(H.session()), None)


def test_replay_is_a_dry_run_with_no_model_call():
    rep = run(snap(H._contradictory()))
    for r in rep["results"].values():
        for v in r.values():
            assert v["execution_status"] in (None, "NOT_EXECUTED", "RECORDED", "DRY_RUN", "NO_ACTION") or \
                v["action"] in BY_KEY["runtime_monitoring"].permitted_actions + BY_KEY["interaction_discovery"].\
                permitted_actions
    assert not rep["store"].select("cop_llm_invocations")


# ---------------------------------------------------------------- metric-to-question compatibility

def test_the_replay_query_selects_every_field_the_two_cops_metrics_need():
    assert R.select_gaps(R.relevant_metrics()) == {}


def test_the_existing_dry_run_without_catalogue_would_not_supply_raw_ref():
    import signal_metrics as M
    assert "raw_ref" not in M.COLS.split(",") and "raw_ref" in R.SELECT.split(",")


def test_every_question_requirement_is_built_or_reported_not_calculated():
    prof = R.evidence_profile(run(snap(H.session())))
    built = {k.split("|")[0] for k in prof["metrics"]}
    for qid, q in R.EA.QUESTIONS["runtime_monitoring"].items():
        for m, _ in q["requires"]:
            assert m in built or m in prof["not_calculated"], (qid, m)
    assert prof["not_calculated"] == ["collector_errors", "collector_heartbeat"]


def test_interaction_discovery_has_no_question_contract_and_says_so():
    prof = R.evidence_profile(run(snap(H.session())))
    assert prof["tools"]["claude-code"]["question_usability"]["interaction_discovery:*"] == {
        "NO_QUESTION_CONTRACT": prof["tools"]["claude-code"]["sessions"]}
    assert set(BY_KEY["interaction_discovery"].metric_keys) <= set(R.relevant_metrics())


# ---------------------------------------------------------------- evidence profile

def test_missing_collector_is_profiled_per_tool_and_proposed_not_applied():
    rep = run(snap(H.session(otel=False, lost_result=True)))
    prof = R.evidence_profile(rep)
    assert prof["tools"]["claude-code"]["sessions_missing_collector"] == {"otel": 1}
    props = V.proposals_from(prof, rep)
    cc = [p for p in props if p.kind == V.COLLECTOR_CONFIG]
    assert cc and cc[0].trigger["collector"] == "otel" and cc[0].status == V.PROPOSED


def test_incomplete_token_and_cost_attribution_is_counted_not_assumed():
    rows = H.session()
    rows[1].update(cost_usd=None)
    rows[-2].update(cost_usd="n/a", turn_id=None)
    rows[4].update(total_tokens=30, cost_usd=0.001, cost_is_estimated=True)
    rep = run(snap(rows))
    t = R.evidence_profile(rep)["tools"]["claude-code"]
    s1 = [r for r in rep["rows"] if r["session_id"] == "s1" and r.get("total_tokens")]
    assert len(s1) == 3
    assert t["token_attribution"]["with_valid_cost"] < t["token_attribution"]["rows_with_tokens"]
    assert t["token_attribution"]["with_turn"] < t["token_attribution"]["rows_with_tokens"]
    assert t["token_attribution"]["cost_estimated"] == 1
    assert t["fields"]["cost_usd"]["valid"] < t["fields"]["cost_usd"]["rows"]


def test_missing_data_reasons_are_kept_one_by_one():
    rows = H.session()
    for r in rows:
        r.pop("ingested_at")
    prof = R.evidence_profile(run(snap(rows)))["metrics"]
    fresh, missing = prof["event_freshness_live|claude-code"], prof["missing_event_rate|claude-code"]
    assert fresh["reasons"] == {"UNKNOWN|MISSING_DATA|basis absent: ingest": fresh["sessions"]}
    assert missing["reasons"] == {"UNKNOWN|MISSING_DATA|basis absent: results": missing["sessions"] - missing["calculated"]}
    assert fresh["calculated"] == 0


def test_calculated_metrics_are_not_counted_as_verified_findings():
    prof = R.evidence_profile(run(snap(H._contradictory())))
    t = prof["tools"]["claude-code"]
    assert "findings" in t and all("|" in k for v in t["findings"].values() for k in v)
    assert prof["metrics"]["telemetry.source_agreement|claude-code"]["calculated"] >= 1


def test_conflicting_sources_are_profiled_and_degrade_the_finding():
    rep = run(snap(H._contradictory()))
    t = R.evidence_profile(rep)["tools"]["claude-code"]
    assert t["source_reliability"]["disagreeing_rows"] == 1
    assert rep["results"]["s1"]["runtime_monitoring"]["label"] == "DEGRADED"
    assert rep["results"]["s1"]["runtime_monitoring"]["question_classes"]["RM-2"] == "CONTRADICTORY_OR_UNVERIFIABLE"


def test_unresolvable_raw_refs_are_profiled_and_proposed():
    rows = H.estate(H._fabricated())
    rep = run(R.snapshot(rows, {"kind": "synthetic"}, H.raw_store(rows)))
    prof = R.evidence_profile(rep)
    assert prof["tools"]["claude-code"]["provenance"]["unresolved"] == 1
    assert rep["results"]["s1"]["runtime_monitoring"]["label"] == "DEGRADED"
    assert any(p.kind == V.OBSERVABLE_DEFINITION for p in V.proposals_from(prof, rep))


def test_without_the_raw_store_provenance_is_not_executed():
    prof = R.evidence_profile(run(snap(H.session(), raw=False)))
    assert set(prof["tools"]["claude-code"]["provenance"]) == {"NOT_EXECUTED (raw store not supplied)"}


def test_insufficient_history_is_profiled_and_a_baseline_needs_validated_support():
    rep = run(R.snapshot(H.session(), {"kind": "synthetic"}, H.raw_store(H.session())))
    prof = R.evidence_profile(rep)
    assert prof["tools"]["claude-code"]["history"]["insufficient"] == 1
    base = [p for p in V.proposals_from(prof, rep) if p.kind == V.HISTORICAL_BASELINE]
    assert base and "VALIDATED" in base[0].verification


# ---------------------------------------------------------------- reproducibility, idempotency, isolation

def test_the_manifest_pins_the_inputs_and_versions():
    m = run(snap(H.session()))["manifest"]
    assert m["evaluated_at"] == AT and m["snapshot_digest"] and m["results_digest"]
    v = m["versions"]
    assert v["metric_version"] and v["metrics_config_sha256"] and v["signal_metrics_sha256"]
    assert v["packages"]["runtime_monitoring"]["version"] == R.EA.load_package("runtime_monitoring")["version"]
    assert v["packages"]["interaction_discovery"] == {"version": None, "note": "no COP package"}


def test_the_same_inputs_give_the_same_results():
    a, b = run(snap(H.session(), H._contradictory("s2"))), run(snap(H.session(), H._contradictory("s2")))
    assert a["manifest"] == b["manifest"] and a["results"] == b["results"]


def test_reprocessing_into_the_same_store_adds_no_duplicate():
    store = SQLiteStore()
    a = run(snap(H.thin_session("s1")), store=store)
    n = (len(store.select("cop_memory_records")), len(store.select("cop_decisions")))
    b = run(snap(H.thin_session("s1")), store=store)
    assert a["manifest"]["results_digest"] == b["manifest"]["results_digest"]
    assert (len(store.select("cop_memory_records")), len(store.select("cop_decisions"))) == n
    assert any(r["outcome"] == "KEPT" for r in store.select("cop_memory_access_log"))


def test_rows_after_the_evaluation_time_do_not_leak_in():
    base = snap(H.session())
    late = H.session("s9")
    for r in late:
        r["occurred_at"] = r["captured_at"] = "2026-10-09T11:00:00Z"
        r["ingested_at"] = "2026-10-09T11:00:30Z"
    s1 = H.session()
    s1[-1]["ingested_at"] = "2026-10-09T12:00:00Z"
    leaky = R.snapshot(H.estate(s1, late), {"kind": "synthetic"}, base["raw_events"])
    a, b = run(base), run(leaky)
    assert "s9" not in b["manifest"]["sessions"]
    assert b["manifest"]["excluded"]["after_evaluation"] == len(late) + 1
    assert b["results"]["s1"]["runtime_monitoring"]["label"] == a["results"]["s1"]["runtime_monitoring"]["label"]


def test_replayed_findings_stay_in_their_own_tenant_and_store():
    live = SQLiteStore()
    rt = E.make_runtime("live", "s1", H.BUILD["downstream_consumption"](), store=live)
    G.run_case(rt, cops=["runtime_monitoring"])
    before = len(live.select("cop_memory_records"))
    rep = run(snap(H.thin_session("s1")))
    assert len(live.select("cop_memory_records")) == before
    recs = rep["store"].select("cop_memory_records")
    assert recs and {r["tenant_id"] for r in recs} == {"replay"}
    assert all(r["provenance"]["run_id"] == f"replay-{rep['manifest']['replay_id']}" for r in recs)
    assert all(r["provenance"]["synthetic"] is True for r in recs)


def test_different_metric_versions_are_kept_apart():
    store = SQLiteStore()
    c2 = H.cfg()
    c2["metric_version"] = "2026-10-05.1-test"
    a = run(snap(H.thin_session("s1")), store=store)
    b = run(snap(H.thin_session("s1")), cfg=c2, store=store)
    assert a["manifest"]["replay_id"] != b["manifest"]["replay_id"]
    assert a["results"]["s1"]["runtime_monitoring"]["finding_id"] != b["results"]["s1"]["runtime_monitoring"][
        "finding_id"]
    assert a["results"]["s1"]["runtime_monitoring"]["label"] == b["results"]["s1"]["runtime_monitoring"]["label"]
    assert len(store.select("cop_memory_records")) == 2


def test_a_tampered_snapshot_file_is_refused(tmp_path):
    p = tmp_path / "snap.json"
    R.save_snapshot(snap(H.session()), p)
    assert R.load_snapshot(p)["row_count"]
    d = json.loads(p.read_text())
    d["rows"][0]["collector"] = "otel"
    p.write_text(json.dumps(d))
    with pytest.raises(ValueError):
        R.load_snapshot(p)


# ---------------------------------------------------------------- Supabase access

def test_supabase_read_is_refused_without_explicit_approval(monkeypatch):
    monkeypatch.delenv(R.READ_APPROVAL, raising=False)
    with pytest.raises(PermissionError):
        R.fetch_supabase("2026-10-01T00:00:00Z", "2026-10-02T00:00:00Z", approved=True, getter=lambda *a: [])
    monkeypatch.setenv(R.READ_APPROVAL, "1")
    with pytest.raises(PermissionError):
        R.fetch_supabase("2026-10-01T00:00:00Z", "2026-10-02T00:00:00Z", approved=False, getter=lambda *a: [])


def test_supabase_read_is_get_only_and_never_reads_raw_payload(monkeypatch):
    monkeypatch.setenv(R.READ_APPROVAL, "1")
    calls = []
    rows = H.session()

    def getter(table, params):
        calls.append((table, params))
        return {"observables": rows, "source_events": [], "collector_capability": []}[table]
    s = R.fetch_supabase("2026-10-09T00:00:00+00:00", "2026-10-10T00:00:00+00:00", approved=True, getter=getter)
    assert [c[0] for c in calls] == ["observables", "source_events", "collector_capability"]
    assert "payload" not in calls[1][1] and "raw_ref" in calls[0][1]
    assert "%2B00%3A00" in calls[0][1]
    assert s["source"]["kind"] == "supabase" and s["source"]["queries"]["observables"] == calls[0][1]
    assert not hasattr(R, "post")


# ---------------------------------------------------------------- controlled evolution

def _proposal():
    rep = run(snap(H.session(otel=False, lost_result=True)))
    return next(p for p in V.proposals_from(R.evidence_profile(rep), rep) if p.kind == V.COLLECTOR_CONFIG)


def _sha(path):
    import hashlib
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


def test_every_proposal_states_the_contract():
    rep = run(snap(H.session(otel=False, lost_result=True)), cfg=H.cfg(limits=False))
    props = V.proposals_from(R.evidence_profile(rep), rep)
    assert {p.kind for p in props} >= {V.COLLECTOR_CONFIG, V.METRIC_CALCULATION, V.INTERPRETATION_RULE}
    for p in props:
        assert V.validate(p) == [], (p.kind, V.validate(p))
        assert p.trigger["replay_id"] == rep["manifest"]["replay_id"]
        assert p.versioning["rollback"]
    assert V.proposals_from(R.evidence_profile(rep), rep)[0].proposal_id == props[0].proposal_id


def test_a_limit_proposal_never_names_a_value():
    rep = run(R.snapshot(H.estate(H.session()), {"kind": "synthetic"}), cfg=H.cfg(False))
    lim = [p for p in V.proposals_from(R.evidence_profile(rep), rep) if p.kind == V.INTERPRETATION_RULE]
    assert lim and all("no value is proposed" in p.change for p in lim)


def test_proposals_stay_inactive_until_a_named_person_in_the_role_approves():
    cfg_before, spec_before = _sha(CFG), BY_KEY["runtime_monitoring"].permitted_actions
    reg = V.EvolutionRegistry(clock=lambda: "2026-11-19T00:00:00+00:00")
    p = reg.propose(_proposal())
    assert reg.active_changes() == []
    with pytest.raises(NotApproved):
        reg.approve(p.proposal_id, "runtime", "system", "collector_owner")
    with pytest.raises(NotApproved):
        reg.approve(p.proposal_id, "replay", HUMAN, "collector_owner")
    with pytest.raises(NotApproved):
        reg.approve(p.proposal_id, "purvi", HUMAN, "cop_owner")
    reg.approve(p.proposal_id, "purvi", HUMAN, "collector_owner")
    assert p.status == V.APPROVED and reg.active_changes() == []
    with pytest.raises(ValueError):
        reg.record_implemented(p.proposal_id, "purvi", HUMAN, "", "")
    reg.record_implemented(p.proposal_id, "purvi", HUMAN, "collector-config/2", "commit abc123")
    assert reg.active_changes() == [p]
    reg.roll_back(p.proposal_id, "purvi", HUMAN, "regressed")
    assert p.status == V.ROLLED_BACK and reg.active_changes() == []
    assert _sha(CFG) == cfg_before and BY_KEY["runtime_monitoring"].permitted_actions == spec_before
    assert [h["event"] for h in p.history] == ["PROPOSED", "APPROVED", "IMPLEMENTED", "ROLLED_BACK"]


def test_registry_has_no_way_to_apply_a_change():
    names = {n for n in dir(V.EvolutionRegistry) if not n.startswith("_")}
    assert names == {"propose", "approve", "reject", "record_implemented", "roll_back", "active_changes",
                     "to_dict", "save", "load"}


def test_an_invalid_proposal_is_refused():
    p = copy.deepcopy(_proposal())
    p.proposal_id, p.approval = "", "anyone"
    with pytest.raises(ValueError):
        V.EvolutionRegistry().propose(V.Proposal(**{**p.to_dict(), "proposal_id": ""}))


def test_the_registry_round_trips(tmp_path):
    reg = V.EvolutionRegistry(clock=lambda: "t")
    p = reg.propose(_proposal())
    reg.save(tmp_path / "ledger.json")
    again = V.EvolutionRegistry.load(tmp_path / "ledger.json")
    assert again.to_dict() == reg.to_dict() and again.items[p.proposal_id].status == V.PROPOSED


def test_scenario_proposals_cover_codes_the_harness_does_not_reach():
    rep = run(R.snapshot(H.estate(H.session()), {"kind": "synthetic"}), cfg=H.cfg(False))
    props = V.scenario_proposals(rep, covered_codes={"LIMIT_NOT_APPROVED"})
    assert {p.trigger["code"] for p in props} == {"RAW_STORE_NOT_SUPPLIED"}
    assert all(p.kind == V.TEST_SCENARIO and p.status == V.PROPOSED for p in props)


# ---------------------------------------------------------------- memory and evolution kept apart

def test_unverified_replay_findings_never_become_a_baseline():
    rep = run(snap(H.thin_session("s1")))
    store = rep["store"]
    recs = store.select("cop_memory_records")
    assert recs and {r["validation_status"] for r in recs} <= {"UNVALIDATED", VERIFIED}
    from cops.runtime.memory import MemoryGateway
    out = MemoryGateway(store).propose_baseline_update("runtime_monitoring", "replay",
                                                       [r["record_id"] for r in recs], {"note": "baseline"})
    assert out["admissible"] is False and out["applied"] is False and len(out["refused"]) == len(recs)


def test_memory_layers_are_counted_apart():
    rep = run(snap(H.thin_session("s1")))
    reg = V.EvolutionRegistry(clock=lambda: "t")
    for p in V.proposals_from(R.evidence_profile(rep), rep):
        reg.propose(p)
    lay = R.memory_layers(rep["store"], reg)
    assert lay["historical_observations"][VERIFIED] >= 1 and lay["verified_outcomes"]["VALIDATED"] == 0
    assert lay["proposed_improvements"] == len(reg.items) and lay["approved_changes"] == 0
    assert not any("proposal" in r["record_type"] for r in rep["store"].select("cop_memory_records"))
