"""Evidence assessment per behavioural question (Runtime Monitoring).
Rows are synthetic, shaped like the observables table."""
import copy, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "collector"))

import signal_metrics as M
from cops.runtime import evidence_assessment as EA

COP = "runtime_monitoring"
AT = "2026-10-10T00:00:00Z"


def cfg(limit=None):
    c = copy.deepcopy(M.load_config())
    if limit is not None:
        c["evidence_limits"] = {"max_ingest_lag_s": limit}
    return c


def obs(sid, n, minute, kind, collector="local_hooks", lag_s=30, **k):
    at = f"2026-10-09T10:{minute:02d}:00Z"
    r = {"observable_id": f"{sid}-{n}", "session_id": sid, "tool": "claude-code", "collector": collector,
         "contributing": [collector], "operator_email": "dev1@tenant-a.example", "workspace": "/ws",
         "turn_id": f"{sid}-t1", "agent_id": "main", "observable_type": kind, "occurred_at": at,
         "captured_at": at, "ingested_at": f"2026-10-09T10:{minute:02d}:{lag_s:02d}Z", "raw_ref": f"raw/{sid}/{n}"}
    r.update(k)
    return r


def session(sid="s1", lost_result=False, **k):
    """A complete session: two collectors, every call answered, cross-checked events that agree."""
    rows = [obs(sid, 1, 0, "session_start", **k), obs(sid, 2, 1, "user_prompt", total_tokens=100, **k)]
    for i, minute in ((3, 2), (5, 4)):
        u = f"{sid}-u{i}"
        rows.append(obs(sid, i, minute, "tool_request", tool_name="Read", tool_use_id=u, tool_arguments={"n": i},
                        collectors_agree=True, **k))
        if not (lost_result and i == 5):
            rows.append(obs(sid, i + 1, minute + 1, "tool_result", tool_name="Read", tool_use_id=u, success=True,
                            tool_duration_ms=200, **k))
    rows.append(obs(sid, 9, 8, "model_request", collector="otel", total_tokens=50, **k))
    rows.append(obs(sid, 10, 9, "session_end", **k))
    return rows


def assess(rows, c=None, sid="s1"):
    return EA.assess_session(COP, rows, sid, c or cfg(limit=60), computed_at=AT)


def check(a, name):
    return [x for x in a["reliability"]["checks"] if x["check"] == name]


def item(a, requirement):
    return next(x for x in a["presence"]["items"] if x["requirement"] == requirement)


# ---------------------------------------------------------------- contract shape

def test_every_question_answers_three_separate_questions_and_no_score():
    res = assess(session())
    assert set(res) == {"RM-1", "RM-3", "RM-4", "RM-5"}
    for a in res.values():
        assert set(a) == {"cop", "question_id", "question", "presence", "reliability", "sufficiency", "evidence_used"}
        assert set(a["sufficiency"]) == {"status", "reasons", "missing_requirements"}
        assert not any("score" in k or "confidence" in k for k in a) and "confidence" not in a["sufficiency"]


def test_questions_carry_workbook_text_and_require_only_existing_metrics_or_mark_them_not_calculated():
    ids = set(M.METRIC_IDS) | set(M.CATALOGUE_IDS) | {"telemetry.unknown_share"}
    for qid, q in EA.QUESTIONS[COP].items():
        assert q["question"].endswith("?")
        for metric, kind in q["requires"]:
            assert metric in ids or kind == EA.OPTIONAL, (qid, metric)
        assert set(q["blocking"]) <= set(EA.CHECKS)


# ---------------------------------------------------------------- normal

def test_normal_session_is_sufficient_for_every_question():
    res = assess(session())
    for qid, a in res.items():
        assert a["presence"]["status"] == "PRESENT", qid
        assert a["reliability"]["status"] == "RELIABLE", (qid, a["reliability"])
        assert a["sufficiency"] == {"status": "SUFFICIENT", "reasons": [], "missing_requirements": []}, qid
    rm5 = res["RM-5"]
    assert item(rm5, "telemetry.source_agreement")["value"] == 1.0
    assert item(rm5, "missing_event_rate")["value"] == 0
    assert {"telemetry.unknown_share", "telemetry.source_agreement", "missing_event_rate",
            "audit_source_provenance_completeness"} <= set(rm5["evidence_used"])
    assert check(res["RM-4"], EA.STALE)[0]["outcome"] == EA.PASSED


def test_optional_requirement_that_is_not_built_is_reported_and_does_not_block():
    a = assess(session())["RM-1"]
    hb = item(a, "collector_heartbeat")
    assert hb["kind"] == EA.OPTIONAL and hb["state"] == EA.NOT_CALCULATED
    assert a["sufficiency"]["status"] == "SUFFICIENT"


# ---------------------------------------------------------------- abnormal: bad values are still evidence

def test_abnormal_values_are_present_and_sufficient_not_inconclusive():
    rows = [r for r in session(lost_result=True) if r["collector"] != "otel"]
    res = assess(rows)
    rm1, rm4 = res["RM-1"], res["RM-4"]
    assert item(rm1, "telemetry.collector_coverage")["value"] == 0.5
    assert item(rm4, "missing_event_rate")["value"] == 0.5
    assert rm1["sufficiency"]["status"] == rm4["sufficiency"]["status"] == "SUFFICIENT"


def test_actions_without_turn_ids_answer_rm3_as_unlinked_rather_than_missing():
    rows = session()
    for r in rows:
        r.pop("turn_id")
    a = assess(rows)["RM-3"]
    assert item(a, "telemetry.turn_linkage")["value"] == 0
    assert a["presence"]["fields"]["turn_id"]["status"] == "ABSENT"
    assert a["sufficiency"]["status"] == "SUFFICIENT"


# ---------------------------------------------------------------- missing

def test_missing_ingest_time_makes_freshness_insufficient_and_staleness_unchecked():
    rows = session()
    for r in rows:
        r.pop("ingested_at")
    a = assess(rows)["RM-4"]
    assert item(a, "event_freshness_live")["state"] == EA.MISSING_DATA
    assert a["presence"]["status"] == "PARTIAL"
    assert a["sufficiency"]["status"] == "INSUFFICIENT"
    assert a["sufficiency"]["missing_requirements"] == ["event_freshness_live"]
    assert {"code": EA.MISSING_DATA, "check": EA.STALE} in [
        {k: r[k] for k in ("code", "check") if k in r} for r in a["sufficiency"]["reasons"]]
    assert check(a, EA.STALE)[0]["outcome"] == EA.NOT_CHECKED


def test_missing_results_is_missing_data_not_a_zero_loss_rate():
    rows = [r for r in session() if r["observable_type"] != "tool_result"]
    a = assess(rows)["RM-5"]
    mer = item(a, "missing_event_rate")
    assert mer["state"] == EA.MISSING_DATA and mer["value"] is None
    assert a["sufficiency"]["status"] == "INSUFFICIENT"
    assert a["sufficiency"]["reasons"][0] == {"code": EA.MISSING_DATA, "requirement": "missing_event_rate",
                                              "detail": mer["reason"]}


# ---------------------------------------------------------------- contradictory

def test_disagreeing_collectors_make_rm5_inconclusive_and_leave_rm1_sufficient():
    rows = session()
    rows[2]["collectors_agree"] = False
    res = assess(rows)
    rm5, rm1 = res["RM-5"], res["RM-1"]
    c = check(rm5, EA.CONFLICTING_SOURCES)[0]
    assert c["outcome"] == EA.FAILED and c["evidence_refs"] == ["observable:s1-3"]
    assert rm5["presence"]["status"] == "PRESENT"
    assert rm5["reliability"]["status"] == "UNRELIABLE"
    assert rm5["sufficiency"]["status"] == "INCONCLUSIVE"
    assert rm5["sufficiency"]["reasons"] == [{"code": EA.CONFLICTING_SOURCES, "detail": c["detail"]}]
    assert rm1["reliability"]["status"] == "RELIABLE_WITH_FINDINGS"
    assert rm1["sufficiency"]["status"] == "SUFFICIENT"


def test_single_collector_cannot_conflict_and_is_not_applicable():
    rows = [r for r in session() if r["collector"] != "otel"]
    a = assess(rows)["RM-5"]
    assert check(a, EA.CONFLICTING_SOURCES)[0]["outcome"] == EA.NOT_APPLICABLE
    assert item(a, "telemetry.source_agreement")["state"] == EA.NOT_APPLICABLE
    assert a["sufficiency"]["status"] == "SUFFICIENT"


# ---------------------------------------------------------------- provenance

def test_rows_without_raw_ref_are_unverifiable_provenance():
    rows = session()
    rows[3].pop("raw_ref")
    a = assess(rows)["RM-5"]
    c = check(a, EA.UNVERIFIABLE_PROVENANCE)[0]
    assert c["outcome"] == EA.FAILED and c["detail"].startswith("1 of 8 rows have no raw_ref")
    assert a["sufficiency"]["status"] == "INCONCLUSIVE"


def test_inferred_rows_without_sources_are_unverifiable_provenance():
    rows = session()
    rows[1].update(confidence="inferred")
    rows[2].update(confidence="observed")
    a = assess(rows)["RM-5"]
    assert [c["source"] for c in check(a, EA.UNVERIFIABLE_PROVENANCE) if c["outcome"] == EA.FAILED] == \
        ["audit_unverifiable_record_rate"]
    assert a["sufficiency"]["status"] == "INCONCLUSIVE"


def test_provenance_that_was_never_read_is_not_checked_not_passed():
    rows = session()
    for r in rows:
        r.pop("raw_ref")
    a = assess(rows)["RM-5"]
    assert check(a, EA.UNVERIFIABLE_PROVENANCE)[0]["outcome"] == EA.NOT_CHECKED
    assert a["reliability"]["status"] == "UNDETERMINED"
    assert a["sufficiency"]["status"] == "INCONCLUSIVE"
    assert a["sufficiency"]["reasons"][0]["check"] == EA.UNVERIFIABLE_PROVENANCE


# ---------------------------------------------------------------- timestamps and staleness

def test_incomparable_timestamps_are_a_failed_check_not_valid():
    rows = session()
    rows[4]["captured_at"] = "2026-10-09T10:04:00"
    a = assess(rows)["RM-4"]
    c = check(a, EA.INCOMPARABLE_TIMESTAMPS)[0]
    assert c["outcome"] == EA.FAILED and c["detail"].startswith("1 of 8 rows")
    assert check(a, EA.TIMESTAMP_ORDER)[0]["outcome"] == EA.PASSED
    assert a["sufficiency"]["status"] == "INCONCLUSIVE"


def test_out_of_order_timestamps_fail_the_order_check():
    rows = session()
    rows[4]["captured_at"] = "2026-10-09T09:00:00Z"
    a = assess(rows)["RM-4"]
    c = check(a, EA.TIMESTAMP_ORDER)[0]
    assert c["outcome"] == EA.FAILED and c["evidence_refs"] == ["observable:s1-5"]
    assert a["sufficiency"]["status"] == "INCONCLUSIVE"


def test_staleness_against_an_approved_limit():
    rows = session(lag_s=45)
    assert check(assess(rows, cfg(limit=60))["RM-4"], EA.STALE)[0]["outcome"] == EA.PASSED
    a = assess(rows, cfg(limit=30))["RM-4"]
    assert check(a, EA.STALE)[0]["outcome"] == EA.FAILED
    assert a["sufficiency"] == {"status": "INCONCLUSIVE", "missing_requirements": [],
                                "reasons": [{"code": EA.STALE, "detail": "median ingest lag 45.0 s exceeds the "
                                                                         "approved 30 s"}]}
    assert assess(rows, cfg(limit=30))["RM-1"]["sufficiency"]["status"] == "SUFFICIENT"


def test_without_an_approved_limit_staleness_is_not_checked_and_rm4_inconclusive():
    c = cfg()
    assert "evidence_limits" not in c
    a = assess(session(), c)["RM-4"]
    st = check(a, EA.STALE)[0]
    assert st["outcome"] == EA.NOT_CHECKED and st["not_checked_because"] == EA.LIMIT_NOT_APPROVED
    assert a["reliability"]["status"] == "UNDETERMINED"
    assert a["sufficiency"]["status"] == "INCONCLUSIVE"
    assert a["sufficiency"]["reasons"][0]["code"] == EA.LIMIT_NOT_APPROVED


# ---------------------------------------------------------------- item states kept apart

def ev(state="NORMAL", value=1.0, **features):
    return {"state": state, "value": value, "features": features, "evidence_refs": [], "error": None,
            "source": "signal_metrics.py:x@v"}


def test_item_states_are_distinguished():
    assert EA.item_state(None)[0] == EA.NOT_CALCULATED
    assert EA.item_state(ev())[0] == EA.PRESENT
    assert EA.item_state(ev("NOT_APPLICABLE", None))[0] == EA.NOT_APPLICABLE
    for avail in (EA.MISSING_REFERENCE, EA.NOT_OBSERVED, EA.INSUFFICIENT_HISTORY):
        assert EA.item_state(ev("UNKNOWN", None, availability=avail, reason="r")) == (avail, "r")
    assert EA.item_state(ev("UNKNOWN", None, availability="MISSING_DATA", reason="r")) == (EA.MISSING_DATA, "r")
    assert EA.item_state(dict(ev("UNKNOWN", None), error="boom")) == (EA.MISSING_DATA, "boom")


def test_insufficient_history_is_reported_as_such(monkeypatch):
    q = {"question": "q?", "fields": [], "blocking": [], "requires": [("trend", EA.REQUIRED)]}
    monkeypatch.setitem(EA.QUESTIONS, "test_cop", {"T-1": q})
    a = EA.assess("test_cop", "T-1", {"trend": ev("UNKNOWN", None, availability=EA.INSUFFICIENT_HISTORY,
                                                  reason="2 earlier sessions, 5 needed")}, [], cfg(limit=60))
    assert a["sufficiency"]["status"] == "INSUFFICIENT"
    assert a["sufficiency"]["reasons"] == [{"code": EA.INSUFFICIENT_HISTORY, "requirement": "trend",
                                            "detail": "2 earlier sessions, 5 needed"}]


def test_not_applicable_or_not_observed_only_satisfies_a_conditional_requirement(monkeypatch):
    q = {"question": "q?", "fields": [], "blocking": [],
         "requires": [("a", EA.REQUIRED), ("b", EA.WHEN_APPLICABLE), ("c", EA.WHEN_APPLICABLE)]}
    monkeypatch.setitem(EA.QUESTIONS, "test_cop", {"T-1": q})
    events = {"a": ev("NOT_APPLICABLE", None), "b": ev("NOT_APPLICABLE", None),
              "c": ev("UNKNOWN", None, availability=EA.NOT_OBSERVED)}
    a = EA.assess("test_cop", "T-1", events, [], cfg(limit=60))
    assert a["sufficiency"]["missing_requirements"] == ["a"]
    assert a["presence"]["status"] == "ABSENT"
