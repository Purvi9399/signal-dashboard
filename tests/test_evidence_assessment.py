"""Evidence assessment per behavioural question (Runtime Monitoring).
Rows are synthetic, shaped like the observables table. Limits are test values."""
import copy, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "collector"))

import signal_metrics as M
from cops.runtime import evidence_assessment as EA
from cops.packages.runtime_monitoring.harness import raw_store

COP = "runtime_monitoring"
AT = "2026-10-09T10:20:00Z"
LIMITS = {"max_ingest_lag_s": 60, "max_evidence_age_s": 3600, "max_missing_event_rate": 0}


def cfg(**limits):
    c = copy.deepcopy(M.load_config())
    if limits:
        c["evidence_limits"] = {k: v for k, v in limits.items() if v is not None}
    return c


def obs(sid, n, minute, kind, collector="local_hooks", lag_s=30, **k):
    at = f"2026-10-09T10:{minute:02d}:00Z"
    r = {"observable_id": f"{sid}-{n}", "session_id": sid, "tool": "claude-code", "collector": collector,
         "contributing": [collector], "operator_email": "dev1@tenant-a.example", "workspace": "/ws",
         "turn_id": f"{sid}-t1", "agent_id": "main", "observable_type": kind, "occurred_at": at,
         "captured_at": at, "ingested_at": f"2026-10-09T10:{minute:02d}:{lag_s:02d}Z",
         "raw_ref": f"native/{sid}.jsonl", "source_refs": [f"raw/{sid}/{n}"], "collectors_agree": None}
    r.update(k)
    return r


def session(sid="s1", lost_result=False, calls=True, **k):
    """Two collectors, every call answered, the calls cross-checked by both and agreeing."""
    rows = [obs(sid, 1, 0, "session_start", **k), obs(sid, 2, 1, "user_prompt", total_tokens=100, **k)]
    for i, minute in ((3, 2), (5, 4)) if calls else ():
        u = f"{sid}-u{i}"
        rows.append(obs(sid, i, minute, "tool_request", tool_name="Read", tool_use_id=u, tool_arguments={"n": i},
                        contributing=["local_hooks", "otel"], collectors_agree=True, **k))
        if not (lost_result and i == 5):
            rows.append(obs(sid, i + 1, minute + 1, "tool_result", tool_name="Read", tool_use_id=u, success=True,
                            tool_duration_ms=200, **k))
    rows.append(obs(sid, 9, 8, "model_request", collector="otel", contributing=["otel"], total_tokens=50, **k))
    rows.append(obs(sid, 10, 9, "session_end", **k))
    return rows


def one_source(rows):
    for r in rows:
        r.update(collector="local_hooks", contributing=["local_hooks"], collectors_agree=None)
    return rows


def assess(rows, c=None, at=AT):
    return EA.assess_session(COP, rows, "s1", c or cfg(**LIMITS), computed_at=at, raw_events=raw_store(rows))


def check(a, name):
    return [x for x in a["reliability"]["checks"] if x["check"] == name]


def outcome(a, name):
    return check(a, name)[0]["outcome"]


def item(a, requirement):
    return next(x for x in a["presence"]["items"] if x["requirement"] == requirement)


def codes(a):
    return [(r["code"], r.get("requirement") or r.get("check")) for r in a["sufficiency"]["reasons"]]


def status(a):
    return a["presence"]["status"], a["reliability"]["status"], a["sufficiency"]["status"]


# ---------------------------------------------------------------- contract

def test_five_questions_each_with_three_separate_answers_and_no_score():
    res = assess(session())
    assert list(res) == ["RM-1", "RM-2", "RM-3", "RM-4", "RM-5"]
    for a in res.values():
        assert set(a) == {"cop", "question_id", "question", "presence", "reliability", "findings", "sufficiency",
                          "evidence_used"}
        assert set(a["sufficiency"]) == {"status", "reasons", "not_assessed", "missing_requirements"}
        assert not [k for k in str(list(a) + list(a["sufficiency"])).split("'") if "score" in k or "confidence" in k]
        assert {c["outcome"] for c in a["reliability"]["checks"]} <= set(EA.OUTCOMES)


def test_requirements_name_built_metrics_or_are_optional():
    built = set(M.METRIC_IDS) | set(M.CATALOGUE_IDS) | {"telemetry.unknown_share"}
    for qid, q in EA.QUESTIONS[COP].items():
        for spec in q["requires"]:
            assert spec[0] in built or spec[1] == EA.OPTIONAL, (qid, spec)
        assert set(q["blocking"]) | set(q["verified"]) <= set(EA.CHECKS)
        assert not set(q["blocking"]) & set(q["verified"])


# ---------------------------------------------------------------- normal

def test_normal_session_is_sufficient_for_every_question():
    res = assess(session())
    for qid, a in res.items():
        assert status(a) == ("PRESENT", "RELIABLE", "SUFFICIENT"), (qid, a["sufficiency"])
        assert a["findings"] == []
    rm2 = res["RM-2"]
    c = check(rm2, EA.CONFLICTING_SOURCES)[0]
    assert c["outcome"] == EA.VERIFIED_OK and c["evidence"]["cross_checked"] == 2 and c["metric_value"] == 1.0
    assert {outcome(res["RM-4"], k) for k in (EA.INGEST_LATENCY, EA.STALE, EA.DISCONTINUITY)} == {EA.VERIFIED_OK}


def test_heartbeat_and_collector_errors_stay_not_calculated_and_are_listed_as_not_assessed():
    rm1 = assess(session())["RM-1"]
    for m in ("collector_heartbeat", "collector_errors"):
        assert item(rm1, m)["state"] == EA.NOT_CALCULATED
    not_assessed = [x["requirement"] for x in rm1["sufficiency"]["not_assessed"]]
    assert not_assessed == ["collector_heartbeat", "collector_errors"]
    assert "collector_heartbeat" not in rm1["evidence_used"]


# ---------------------------------------------------------------- RM-2 source agreement

def test_one_source_is_comparison_not_possible_never_verified_agreement():
    res = assess(one_source(session()))
    rm2 = res["RM-2"]
    c = check(rm2, EA.CONFLICTING_SOURCES)[0]
    assert c["outcome"] == EA.COMPARISON_NOT_POSSIBLE and c["detail"].startswith("only one source (local_hooks)")
    assert status(rm2) == ("ABSENT", "UNDETERMINED", "INSUFFICIENT")
    assert codes(rm2) == [(EA.COMPARISON_NOT_POSSIBLE, EA.CONFLICTING_SOURCES)]
    assert res["RM-5"]["sufficiency"]["status"] == "SUFFICIENT"
    assert res["RM-5"]["sufficiency"]["not_assessed"][-1]["check"] == EA.CONFLICTING_SOURCES


def test_two_sources_that_never_saw_the_same_event_cannot_be_compared():
    rows = session()
    for r in rows:
        r.update(contributing=[r["collector"]], collectors_agree=None)
    c = check(assess(rows)["RM-2"], EA.CONFLICTING_SOURCES)[0]
    assert c["outcome"] == EA.COMPARISON_NOT_POSSIBLE and "no event was seen by more than one" in c["detail"]


def test_correlation_field_not_read_is_evidence_missing():
    rows = session()
    for r in rows:
        r.pop("collectors_agree")
    rm2 = assess(rows)["RM-2"]
    assert outcome(rm2, EA.CONFLICTING_SOURCES) == EA.EVIDENCE_MISSING
    assert rm2["sufficiency"]["status"] == "INSUFFICIENT"


def test_contradictory_sources_answer_rm2_and_make_rm3_and_rm5_inconclusive():
    rows = session()
    rows[2]["collectors_agree"] = False
    res = assess(rows)
    rm2 = res["RM-2"]
    c = check(rm2, EA.CONFLICTING_SOURCES)[0]
    assert c["outcome"] == EA.VERIFIED_FAILED and c["evidence_refs"] == ["observable:s1-3"] and c["value"] == 0.5
    assert status(rm2) == ("PRESENT", "RELIABLE_WITH_FINDINGS", "SUFFICIENT")
    assert rm2["findings"][0] == {"check": EA.CONFLICTING_SOURCES, "role": "answer", "detail": c["detail"],
                                  "evidence_refs": ["observable:s1-3"]}
    assert [(f["check"], f["role"]) for f in rm2["findings"][1:]] == [(EA.CONFLICTING_ACTION_SOURCES, "context")]
    for qid, blocking in (("RM-3", EA.CONFLICTING_ACTION_SOURCES), ("RM-5", EA.CONFLICTING_SOURCES)):
        assert status(res[qid]) == ("PRESENT", "UNRELIABLE", "INCONCLUSIVE"), qid
        assert codes(res[qid]) == [(blocking, blocking)]
    assert res["RM-1"]["sufficiency"]["status"] == "SUFFICIENT"
    assert {f["role"] for f in res["RM-1"]["findings"]} == {"context"}


def test_disagreement_on_a_non_action_row_does_not_block_action_linkage():
    rows = session()
    rows[1].update(contributing=["local_hooks", "otel"], collectors_agree=False)
    res = assess(rows)
    assert outcome(res["RM-3"], EA.CONFLICTING_ACTION_SOURCES) == EA.VERIFIED_OK
    assert res["RM-3"]["sufficiency"]["status"] == "SUFFICIENT"
    assert res["RM-5"]["sufficiency"]["status"] == "INCONCLUSIVE"


# ---------------------------------------------------------------- RM-3 linkage

def test_actions_without_turn_ids_are_a_sufficient_negative_finding():
    rows = session()
    for r in rows:
        r.pop("turn_id")
    a = assess(rows)["RM-3"]
    assert item(a, "telemetry.turn_linkage")["value"] == 0
    assert item(a, "telemetry.turn_linkage")["features"]["without_turn"] > 0
    assert a["presence"]["fields"]["turn_id"]["status"] == "ABSENT"
    assert status(a) == ("PRESENT", "RELIABLE", "SUFFICIENT")


def test_zero_observed_actions_is_insufficient():
    rows = [r for r in session(calls=False) if r["observable_type"] in ("session_start", "session_end")]
    a = assess(rows)["RM-3"]
    tl = item(a, "telemetry.turn_linkage")
    assert tl["state"] == EA.MISSING_DATA and tl["reason"] == "no steps"
    assert outcome(a, EA.CONFLICTING_ACTION_SOURCES) == EA.EVIDENCE_MISSING
    assert status(a) == ("ABSENT", "UNDETERMINED", "INSUFFICIENT")
    assert a["sufficiency"]["missing_requirements"] == ["telemetry.turn_linkage"]


def test_unverifiable_action_evidence_makes_rm3_inconclusive():
    rows = session()
    rows[2].pop("source_refs")
    a = assess(rows)["RM-3"]
    assert outcome(a, EA.UNVERIFIABLE_PROVENANCE) == EA.VERIFIED_OK
    assert provenance(a)[0]["outcome"] == EA.VERIFIED_FAILED and provenance(a)[0]["resolution"][EA.UNRESOLVED] == 1
    assert status(a) == ("PRESENT", "UNRELIABLE", "INCONCLUSIVE")


# ---------------------------------------------------------------- RM-4 latency, age, continuity

def test_ingest_latency_and_evidence_age_are_separate():
    rows = session(lag_s=5)
    a = assess(rows, at="2026-10-09T12:00:00Z")["RM-4"]
    lat, age = check(a, EA.INGEST_LATENCY)[0], check(a, EA.STALE)[0]
    assert lat["outcome"] == EA.VERIFIED_OK and lat["value"] == 5.0
    assert age["outcome"] == EA.VERIFIED_FAILED and age["value"] == 6660.0
    assert age["newest_observation"] == "2026-10-09T10:09:00+00:00"


def test_stale_at_decision_time_is_answered_not_hidden():
    a = assess(session(), at="2026-10-09T12:00:00Z")["RM-4"]
    assert status(a) == ("PRESENT", "RELIABLE", "SUFFICIENT")
    assert [(f["check"], f["role"]) for f in a["findings"]] == [(EA.STALE, "answer")]
    assert check(a, EA.STALE)[0]["detail"] == \
        "age of the newest observation at evaluation time 6660.0 s exceeds the approved 3600 s"


def test_no_evaluation_time_means_age_not_executed():
    a = EA.assess_session(COP, session(), "s1", cfg(**LIMITS), computed_at=None,
                          raw_events=raw_store(session()))["RM-4"]
    assert check(a, EA.STALE)[0]["not_executed_because"] == EA.NO_EVALUATION_TIME
    assert a["sufficiency"]["status"] == "INCONCLUSIVE"


def test_lost_results_are_a_verified_discontinuity():
    a = assess(session(lost_result=True))["RM-4"]
    d = check(a, EA.DISCONTINUITY)[0]
    assert d["outcome"] == EA.VERIFIED_FAILED and d["value"] == 0.5 and d["evidence_refs"] == ["observable:s1-5"]
    assert a["sufficiency"]["status"] == "SUFFICIENT" and a["findings"][0]["check"] == EA.DISCONTINUITY


def test_missing_denominator_is_insufficient_not_zero_loss():
    rows = session()
    for r in rows:
        r.pop("tool_use_id", None)
    a = assess(rows)["RM-4"]
    mer = item(a, "missing_event_rate")
    assert mer["state"] == EA.MISSING_DATA and mer["value"] is None
    assert outcome(a, EA.DISCONTINUITY) == EA.EVIDENCE_MISSING
    assert a["sufficiency"]["status"] == "INSUFFICIENT"
    assert (EA.MISSING_DATA, "missing_event_rate") in codes(a) and (EA.EVIDENCE_MISSING, EA.DISCONTINUITY) in codes(a)


def test_no_calls_means_no_result_is_expected():
    rows = session(calls=False)
    a = assess(rows)["RM-4"]
    assert item(a, "missing_event_rate")["state"] == EA.NOT_APPLICABLE
    assert outcome(a, EA.DISCONTINUITY) == EA.VERIFIED_OK


def test_missing_ingest_time_is_insufficient():
    rows = session()
    for r in rows:
        r.pop("ingested_at")
    a = assess(rows)["RM-4"]
    assert item(a, "event_freshness_live")["state"] == EA.MISSING_DATA
    assert outcome(a, EA.INGEST_LATENCY) == EA.EVIDENCE_MISSING
    assert outcome(a, EA.STALE) == EA.VERIFIED_OK
    assert status(a)[2] == "INSUFFICIENT"
    assert assess(rows)["RM-1"]["sufficiency"]["status"] == "SUFFICIENT"


def test_each_missing_limit_is_reported_unassessed_never_passed():
    for key in LIMITS:
        c = cfg(**dict(LIMITS, **{key: None}))
        a = assess(session(lost_result=key == "max_missing_event_rate"), c)["RM-4"]
        unexec = [x for x in a["reliability"]["checks"] if x["outcome"] == EA.NOT_EXECUTED]
        assert [x["limit_key"] for x in unexec] == [key]
        assert unexec[0]["not_executed_because"] == EA.LIMIT_NOT_APPROVED
        assert a["reliability"]["status"] == "UNDETERMINED" and a["sufficiency"]["status"] == "INCONCLUSIVE"
        assert codes(a) == [(EA.LIMIT_NOT_APPROVED, unexec[0]["check"])]


def test_shipped_config_has_no_evidence_limits():
    assert "evidence_limits" not in M.load_config()
    a = assess(session(), cfg())["RM-4"]
    assert {x["check"] for x in a["reliability"]["checks"] if x["outcome"] == EA.NOT_EXECUTED} == \
        {EA.INGEST_LATENCY, EA.STALE, EA.DISCONTINUITY}


# ---------------------------------------------------------------- timestamps and provenance

def test_incomparable_timestamps_make_rm4_inconclusive():
    rows = session()
    rows[4]["captured_at"] = "2026-10-09T10:04:00"
    a = assess(rows)["RM-4"]
    c = check(a, EA.INCOMPARABLE_TIMESTAMPS)[0]
    assert c["outcome"] == EA.VERIFIED_FAILED and c["detail"].startswith("1 of 8 rows")
    assert outcome(a, EA.TIMESTAMP_ORDER) == EA.VERIFIED_OK
    assert status(a) == ("PRESENT", "UNRELIABLE", "INCONCLUSIVE")


def test_out_of_order_timestamps_fail_the_order_check():
    rows = session()
    rows[4]["captured_at"] = "2026-10-09T09:00:00Z"
    a = assess(rows)["RM-4"]
    assert check(a, EA.TIMESTAMP_ORDER)[0]["evidence_refs"] == ["observable:s1-5"]
    assert a["sufficiency"]["status"] == "INCONCLUSIVE"


def test_inferred_rows_without_sources_are_unverifiable_provenance():
    rows = session()
    rows[1].update(confidence="inferred")
    rows[2].update(confidence="observed")
    a = assess(rows)["RM-5"]
    assert [c["source"] for c in check(a, EA.UNVERIFIABLE_PROVENANCE) if c["outcome"] == EA.VERIFIED_FAILED] == \
        ["audit_unverifiable_record_rate"]
    assert a["sufficiency"]["status"] == "INCONCLUSIVE"


def test_provenance_never_read_is_evidence_missing_and_inconclusive():
    rows = session()
    for r in rows:
        r.pop("raw_ref")
    a = assess(rows)["RM-5"]
    assert outcome(a, EA.UNVERIFIABLE_PROVENANCE) == EA.EVIDENCE_MISSING
    assert status(a) == ("PRESENT", "UNDETERMINED", "INCONCLUSIVE")
    assert codes(a) == [(EA.EVIDENCE_MISSING, EA.UNVERIFIABLE_PROVENANCE)]


# ---------------------------------------------------------------- item states

def ev(state="NORMAL", value=1.0, **features):
    return {"state": state, "value": value, "features": features, "evidence_refs": [], "error": None}


def test_item_states_are_distinguished():
    assert EA.item_state(None)[0] == EA.NOT_CALCULATED
    assert EA.item_state(ev())[0] == EA.PRESENT
    assert EA.item_state(ev("NOT_APPLICABLE", None))[0] == EA.NOT_APPLICABLE
    for avail in (EA.MISSING_REFERENCE, EA.NOT_OBSERVED, EA.INSUFFICIENT_HISTORY):
        assert EA.item_state(ev("UNKNOWN", None, availability=avail, reason="r")) == (avail, "r")
    assert EA.item_state(ev("UNKNOWN", None, availability="MISSING_DATA", reason="r")) == (EA.MISSING_DATA, "r")
    assert EA.item_state(dict(ev("UNKNOWN", None), error="boom")) == (EA.MISSING_DATA, "boom")


def test_insufficient_history_is_reported_as_such(monkeypatch):
    q = {"question": "q?", "fields": [], "verified": [], "blocking": [], "requires": [("trend", EA.REQUIRED)]}
    monkeypatch.setitem(EA.QUESTIONS, "test_cop", {"T-1": q})
    a = EA.assess("test_cop", "T-1", {"trend": ev("UNKNOWN", None, availability=EA.INSUFFICIENT_HISTORY,
                                                  reason="2 earlier sessions, 5 needed")}, [], cfg(**LIMITS), AT)
    assert a["sufficiency"]["status"] == "INSUFFICIENT"
    assert a["sufficiency"]["reasons"] == [{"code": EA.INSUFFICIENT_HISTORY, "requirement": "trend",
                                            "detail": "2 earlier sessions, 5 needed", "inputs_not_supplied": []}]


def test_not_applicable_satisfies_only_a_conditional_requirement(monkeypatch):
    q = {"question": "q?", "fields": [], "verified": [], "blocking": [],
         "requires": [("a", EA.REQUIRED), ("b", EA.WHEN_APPLICABLE), ("c", EA.WHEN_APPLICABLE)]}
    monkeypatch.setitem(EA.QUESTIONS, "test_cop", {"T-1": q})
    events = {"a": ev("NOT_APPLICABLE", None), "b": ev("NOT_APPLICABLE", None),
              "c": ev("UNKNOWN", None, availability=EA.NOT_OBSERVED)}
    a = EA.assess("test_cop", "T-1", events, [], cfg(**LIMITS), AT)
    assert a["sufficiency"]["missing_requirements"] == ["a"]
    assert a["presence"]["status"] == "ABSENT"


def test_only_the_packaged_cops_and_shared_runtime_use_the_assessment():
    import glob
    here = os.path.join(os.path.dirname(__file__), "..", "collector")
    users = sorted(os.path.relpath(p, here) for p in glob.glob(os.path.join(here, "**", "*.py"), recursive=True)
                   if any(m in open(p).read() for m in ("evidence_assessment", "cop_package"))
                   and not p.endswith(("evidence_assessment.py", "cop_package.py")))
    assert users == [os.path.join("cops", "packages", "anomaly_detection", "harness.py"),
                     os.path.join("cops", "packages", "audit_traceability", "harness.py"),
                     os.path.join("cops", "packages", "interaction_discovery", "harness.py"),
                     os.path.join("cops", "packages", "runtime_monitoring", "harness.py"),
                     os.path.join("cops", "runtime", "decision.py"),
                     os.path.join("cops", "runtime", "evolution.py"), os.path.join("cops", "runtime", "graph.py"),
                     os.path.join("cops", "runtime", "replay.py"),
                     os.path.join("cops", "runtime", "rules.py"), os.path.join("cops", "runtime", "support.py"),
                     os.path.join("cops", "runtime", "tools.py"), os.path.join("cops", "runtime", "verifiers.py")]


# ---------------------------------------------------------------- raw_ref resolution

def provenance(a):
    return [x for x in check(a, EA.UNVERIFIABLE_PROVENANCE) if x["source"] == "source_events"]


def test_without_the_raw_store_provenance_is_not_executed_never_passed():
    rows = session()
    a = EA.assess_session(COP, rows, "s1", cfg(**LIMITS), computed_at=AT)["RM-3"]
    assert [(x["outcome"], x["not_executed_because"]) for x in provenance(a)] == \
        [(EA.NOT_EXECUTED, EA.RAW_STORE_NOT_SUPPLIED)]
    assert a["sufficiency"]["status"] == "INCONCLUSIVE"


def test_a_source_ref_that_names_no_stored_event_fails_provenance():
    rows = session()
    store = raw_store(rows)
    rows[3]["source_refs"] = ["raw/s1/999"]
    a = assess_with(rows, store)["RM-3"]
    p = provenance(a)
    assert [x["outcome"] for x in p] == [EA.VERIFIED_FAILED] and p[0]["evidence_refs"] == ["observable:s1-4"]
    assert p[0]["resolution"] == {EA.VERIFIED: len(rows) - 1, EA.UNRESOLVED: 1}
    assert a["sufficiency"]["status"] == "INCONCLUSIVE" and a["reliability"]["status"] == "UNRELIABLE"


def test_a_source_event_of_another_session_is_mismatched():
    rows = session()
    store = raw_store(rows) + [{"source_id": "x", "session_id": "s2", "source_ref": "raw/s2/1"}]
    rows[1]["source_refs"] = ["raw/s2/1"]
    p = provenance(assess_with(rows, store)["RM-3"])
    assert [x["outcome"] for x in p] == [EA.VERIFIED_FAILED] and p[0]["resolution"][EA.MISMATCHED] == 1


def test_a_populated_raw_ref_alone_is_never_verified():
    rows = session()
    for r in rows:
        r.pop("source_refs")
        r["raw_ref"] = "/home/dev/.claude/projects/p/s1.jsonl"
    store = [{"source_id": "e1", "session_id": "s1", "source_ref": "/home/dev/.signal/sessions/c-RAW.jsonl:12"}]
    a = assess_with(rows, store)["RM-3"]
    assert [(x["outcome"], x["not_executed_because"]) for x in provenance(a)] == \
        [(EA.NOT_EXECUTED, EA.SOURCE_JOIN_UNAVAILABLE)]
    assert a["reliability"]["status"] == "UNDETERMINED" and a["sufficiency"]["status"] == "INCONCLUSIVE"
    assert provenance(a)[0]["native"] == {"resolution": {EA.UNAVAILABLE: len(rows)}, "native_store_supplied": False,
                                          "limitation": EA.NATIVE_STORE_NOT_SUPPLIED}


def test_a_reconstructed_dedup_key_links_a_row_to_its_source_event():
    rows = session()
    for i, r in enumerate(rows):
        r.pop("source_refs")
        r["dedup_key"] = f"k{i}"
    store = [{"source_id": f"e{i}", "session_id": "s1", "tool": "claude-code", "dedup_key": f"k{i}"}
             for i in range(len(rows))]
    assert [x["outcome"] for x in provenance(assess_with(rows, store)["RM-3"])] == [EA.VERIFIED_OK]
    store[2]["tool"] = "cursor"
    p = provenance(assess_with(rows, store)["RM-3"])
    assert p[0]["outcome"] == EA.VERIFIED_FAILED and p[0]["resolution"][EA.MISMATCHED] == 1


def test_an_unreconstructable_source_leaves_the_link_unavailable():
    rows = session()
    for i, r in enumerate(rows):
        r.pop("source_refs")
        r["dedup_key"] = f"k{i}"
    store = [{"source_id": "e0", "session_id": "s1", "dedup_key": "k0"},
             {"source_id": "e1", "session_id": "s1", "dedup_key_unavailable": "time-bucket key"}]
    p = provenance(assess_with(rows, store)["RM-3"])
    assert p[0]["outcome"] == EA.NOT_EXECUTED and p[0]["resolution"] == {EA.VERIFIED: 1, EA.UNAVAILABLE: len(rows) - 1}


def test_native_transcripts_resolve_only_against_an_index():
    rows = session()
    store = raw_store(rows)
    ok = {"host": None, "paths": {"native/s1.jsonl": {"exists": True, "session_ids": ["s1"]}}}
    a = EA.assess_session(COP, rows, "s1", cfg(**LIMITS), computed_at=AT, raw_events=store, native=ok)["RM-3"]
    assert provenance(a)[0]["native"]["resolution"] == {EA.VERIFIED: len(rows)}
    assert provenance(a)[0]["native"]["limitation"] is None
    gone = {"host": None, "paths": {}}
    a = EA.assess_session(COP, rows, "s1", cfg(**LIMITS), computed_at=AT, raw_events=store, native=gone)["RM-3"]
    assert provenance(a)[0]["native"]["limitation"] == EA.NATIVE_TRANSCRIPT_MISSING
    assert provenance(a)[0]["outcome"] == EA.VERIFIED_OK
    other = {"host": None, "paths": {"native/s1.jsonl": {"exists": True, "session_ids": ["s9"]}}}
    a = EA.assess_session(COP, rows, "s1", cfg(**LIMITS), computed_at=AT, raw_events=store, native=other)["RM-3"]
    assert EA.VERIFIED_FAILED in [x["outcome"] for x in check(a, EA.UNVERIFIABLE_PROVENANCE)]


def test_a_source_event_naming_another_transcript_is_mismatched():
    rows = session()
    store = raw_store(rows)
    store[0]["transcript_path"] = "native/other.jsonl"
    a = assess_with(rows, store)["RM-3"]
    assert [x["outcome"] for x in check(a, EA.UNVERIFIABLE_PROVENANCE) if x["source"] == "native_transcripts"] == \
        [EA.VERIFIED_FAILED]
    assert a["reliability"]["status"] == "UNRELIABLE"


def test_derived_from_ids_must_be_observables_of_the_session():
    rows = session()
    store = raw_store(rows)
    rows[2]["derived_from"] = ["s1-1", "missing-id"]
    p = provenance(assess_with(rows, store)["RM-3"])
    assert p[0]["outcome"] == EA.VERIFIED_FAILED
    assert p[0]["unresolved"][0]["derived"]["refs"] == ["observable:missing-id"]


def test_the_chain_runs_from_finding_to_native_transcript():
    rows = session()
    events, srows = EA.session_inputs(rows, "s1", cfg(**LIMITS), AT)
    c = EA.resolve_chain(["metric:telemetry.collector_coverage", "metric:no_such_metric", "tool:abc"],
                         events, srows, raw_store(rows))
    assert [n["status"] for n in c["nodes"]] == [EA.VERIFIED, EA.UNRESOLVED, EA.UNAVAILABLE]
    assert c["nodes"][0]["method"] == "session_scope"
    assert c["source"] == {EA.VERIFIED: len(srows)} and c["native"] == {EA.UNAVAILABLE: len(srows)}


def assess_with(rows, store):
    return EA.assess_session(COP, rows, "s1", cfg(**LIMITS), computed_at=AT, raw_events=store)


def test_inputs_never_supplied_are_marked_so():
    rows = session()
    for r in rows:
        r.pop("ingested_at")
    a = assess(rows)["RM-4"]
    fresh = next(x for x in a["sufficiency"]["reasons"] if x.get("requirement") == "event_freshness_live")
    assert a["sufficiency"]["status"] == "INSUFFICIENT" and "ingested_at" in fresh["inputs_not_supplied"]
    rows = session()
    for r in rows:
        r["ingested_at"] = None
    fresh = next(x for x in assess(rows)["RM-4"]["sufficiency"]["reasons"]
                 if x.get("requirement") == "event_freshness_live")
    assert fresh["inputs_not_supplied"] == []
