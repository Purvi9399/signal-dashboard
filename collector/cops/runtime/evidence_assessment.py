"""
Evidence assessment per behavioural question.

For one question of one COP, three answers kept apart:

  presence     is the telemetry the question needs there?
  reliability  can what is there be trusted (sources agree, provenance is
               verifiable, timestamps are comparable, evidence is fresh)?
  sufficiency  is it enough to answer this question?

Nothing here decides a COP label or a confidence. Limits (for example how
old evidence may be) are read from metrics_config.json "evidence_limits";
when a limit has not been approved the check is NOT_CHECKED, never passed.
"""

import os, sys

HERE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import signal_metrics                                    # noqa: E402

PRESENT = "PRESENT"
MISSING_DATA = "MISSING_DATA"
MISSING_REFERENCE = "MISSING_REFERENCE"
NOT_OBSERVED = "NOT_OBSERVED"
NOT_APPLICABLE = "NOT_APPLICABLE"
INSUFFICIENT_HISTORY = "INSUFFICIENT_HISTORY"
NOT_CALCULATED = "NOT_CALCULATED"
ITEM_STATES = (PRESENT, MISSING_DATA, MISSING_REFERENCE, NOT_OBSERVED, NOT_APPLICABLE, INSUFFICIENT_HISTORY,
               NOT_CALCULATED)

STALE = "STALE"
CONFLICTING_SOURCES = "CONFLICTING_SOURCES"
UNVERIFIABLE_PROVENANCE = "UNVERIFIABLE_PROVENANCE"
INCOMPARABLE_TIMESTAMPS = "INCOMPARABLE_TIMESTAMPS"
TIMESTAMP_ORDER = "TIMESTAMP_ORDER"
CHECKS = (STALE, CONFLICTING_SOURCES, UNVERIFIABLE_PROVENANCE, INCOMPARABLE_TIMESTAMPS, TIMESTAMP_ORDER)

PASSED, FAILED, NOT_CHECKED = "PASSED", "FAILED", "NOT_CHECKED"
LIMIT_NOT_APPROVED = "LIMIT_NOT_APPROVED"

REQUIRED, WHEN_APPLICABLE, OPTIONAL = "REQUIRED", "REQUIRED_WHEN_APPLICABLE", "OPTIONAL"

# Questions and fields as listed for Runtime Monitoring in COPS- implementation 1.xlsx,
# sheet COMPLETE_DELEGATION_DATA (numbered 1, 3, 4, 5 there; no question 2 is listed).
# Which metrics each question requires, and which reliability checks block it, is
# proposed in Block 2.2 for agreement.
QUESTIONS = {
    "runtime_monitoring": {
        "RM-1": {
            "question": "Are the expected telemetry collectors operating and providing the required data?",
            "fields": ["collector", "observable_id", "observable_type", "session_id", "agent_id", "raw_ref"],
            "requires": [("telemetry.collector_coverage", REQUIRED), ("collector_heartbeat", OPTIONAL)],
            "blocking": [],
        },
        "RM-3": {
            "question": ("Can observed actions be reliably linked to their originating turns, agents and "
                         "execution lanes?"),
            "fields": ["observable_id", "observable_type", "session_id", "turn_id", "agent_id", "parent_agent_id",
                       "agent_type", "is_multi_agent", "multi_agent_source", "delegation_depth", "tool_use_id",
                       "tool_name", "raw_ref"],
            "requires": [("telemetry.turn_linkage", REQUIRED), ("telemetry.lane_linkage", WHEN_APPLICABLE)],
            "blocking": [],
        },
        "RM-4": {
            "question": "Is the telemetry sufficiently fresh and continuous to support a current governance decision?",
            "fields": ["observable_id", "observable_type", "session_id", "collector", "status", "turn_id",
                       "turn_count", "tool_use_id", "tool_duration_ms", "hung_seconds", "background_tasks",
                       "scheduled_jobs", "raw_ref"],
            "requires": [("event_freshness_live", REQUIRED), ("missing_event_rate", WHEN_APPLICABLE),
                         ("collector_heartbeat", OPTIONAL)],
            "blocking": [STALE, INCOMPARABLE_TIMESTAMPS, TIMESTAMP_ORDER],
        },
        "RM-5": {
            "question": ("Is the available evidence sufficiently complete and trustworthy, or must the finding remain "
                         "uncertain because of missing or unknown data?"),
            "fields": ["observable_id", "observable_type", "collector", "session_id", "turn_id", "agent_id",
                       "parent_agent_id", "tool_use_id", "tool_name", "tool_arguments", "tool_result", "success",
                       "status", "error_type", "raw_ref"],
            "requires": [("telemetry.unknown_share", REQUIRED), ("telemetry.source_agreement", WHEN_APPLICABLE),
                         ("missing_event_rate", WHEN_APPLICABLE), ("collector_errors", OPTIONAL)],
            "blocking": [CONFLICTING_SOURCES, UNVERIFIABLE_PROVENANCE],
        },
    },
}


def item_state(ev):
    """The presence state of one requirement from its metric event."""
    if ev is None:
        return NOT_CALCULATED, "not calculated by signal_metrics.py"
    f = ev.get("features") or {}
    avail = f.get("availability")
    reason = f.get("reason") or ev.get("error") or ""
    if ev.get("error"):
        return MISSING_DATA, ev["error"]
    if ev["state"] == "NOT_APPLICABLE":
        return NOT_APPLICABLE, reason
    if avail in (MISSING_REFERENCE, NOT_OBSERVED, INSUFFICIENT_HISTORY):
        return avail, reason
    if ev["state"] == "UNKNOWN" or ev.get("value") is None:
        return MISSING_DATA, reason
    return PRESENT, ""


def field_presence(rows, fields):
    n = len(rows)
    out = {}
    for f in fields:
        k = sum(1 for r in rows if signal_metrics.has(r.get(f)))
        out[f] = {"rows_with_value": k, "rows": n,
                  "status": "ABSENT" if not k else "PRESENT" if k == n else "PARTIAL"}
    return out


def reliability_checks(events, cfg):
    """Each reliability check for the session, read from the metric events alone."""
    out = []

    def add(check, outcome, detail, source, refs=(), not_checked_because=None):
        out.append({"check": check, "outcome": outcome, "detail": detail, "source": source,
                    "evidence_refs": list(refs), "not_checked_because": not_checked_because})

    ev = lambda m: events.get(m) or {}                                       # noqa: E731
    feats = lambda m: ev(m).get("features") or {}                            # noqa: E731

    sa = ev("telemetry.source_agreement")
    if sa.get("value") is None:
        add(CONFLICTING_SOURCES, NOT_APPLICABLE, feats("telemetry.source_agreement").get("reason")
            or "no event was seen by more than one collector", "telemetry.source_agreement")
    elif sa["features"]["disagreed"]:
        f = sa["features"]
        add(CONFLICTING_SOURCES, FAILED, f"{f['disagreed']} of {f['cross_checked']} cross-checked events disagree "
            "between collectors", "telemetry.source_agreement", sa.get("evidence_refs", []))
    else:
        add(CONFLICTING_SOURCES, PASSED, f"{sa['features']['cross_checked']} cross-checked events agree",
            "telemetry.source_agreement")

    pv = ev("audit_source_provenance_completeness")
    if pv.get("value") is None:
        add(UNVERIFIABLE_PROVENANCE, NOT_CHECKED, feats("audit_source_provenance_completeness").get("reason")
            or "provenance was not measured", "audit_source_provenance_completeness",
            not_checked_because=MISSING_DATA)
    elif pv["value"] < 1:
        f = pv["features"]
        add(UNVERIFIABLE_PROVENANCE, FAILED, f"{f['rows'] - f['with_raw_ref']} of {f['rows']} rows have no raw_ref, "
            f"{f['rows'] - f['with_collector']} no collector", "audit_source_provenance_completeness")
    else:
        add(UNVERIFIABLE_PROVENANCE, PASSED, "every row names its collector and raw event",
            "audit_source_provenance_completeness")
    un = ev("audit_unverifiable_record_rate")
    if un.get("value"):
        add(UNVERIFIABLE_PROVENANCE, FAILED, f"{un['value']:.1%} of rows marked inferred name no source rows",
            "audit_unverifiable_record_rate", un.get("evidence_refs", []))

    ti = ev("audit_timestamp_integrity")
    tf = feats("audit_timestamp_integrity")
    if ti.get("value") is None:
        reason = tf.get("reason") or "timestamp integrity was not measured"
        add(INCOMPARABLE_TIMESTAMPS, NOT_CHECKED, reason, "audit_timestamp_integrity", not_checked_because=MISSING_DATA)
        add(TIMESTAMP_ORDER, NOT_CHECKED, reason, "audit_timestamp_integrity", not_checked_because=MISSING_DATA)
    else:
        if tf["unreadable"]:
            add(INCOMPARABLE_TIMESTAMPS, FAILED, f"{tf['unreadable']} of {tf['rows'] + tf['unreadable']} rows have "
                "timestamps that cannot be read or compared", "audit_timestamp_integrity")
        else:
            add(INCOMPARABLE_TIMESTAMPS, PASSED, f"all {tf['rows']} rows have comparable timestamps",
                "audit_timestamp_integrity")
        if tf["violations"]:
            add(TIMESTAMP_ORDER, FAILED, f"{tf['violations']} of {tf['rows']} rows were captured before they occurred "
                "or ingested before they were captured", "audit_timestamp_integrity", ti.get("evidence_refs", []))
        else:
            add(TIMESTAMP_ORDER, PASSED, f"occurred, captured and ingested are in order in all {tf['rows']} rows",
                "audit_timestamp_integrity")

    fr = ev("event_freshness_live")
    limit = (cfg.get("evidence_limits") or {}).get("max_ingest_lag_s")
    if fr.get("value") is None:
        add(STALE, NOT_CHECKED, feats("event_freshness_live").get("reason") or "no ingest lag could be measured",
            "event_freshness_live", not_checked_because=MISSING_DATA)
    elif limit is None:
        add(STALE, NOT_CHECKED, f"median ingest lag is {fr['value']} s; no evidence_limits.max_ingest_lag_s has "
            "been approved", "event_freshness_live", not_checked_because=LIMIT_NOT_APPROVED)
    elif fr["value"] > limit:
        add(STALE, FAILED, f"median ingest lag {fr['value']} s exceeds the approved {limit} s", "event_freshness_live")
    else:
        add(STALE, PASSED, f"median ingest lag {fr['value']} s is within the approved {limit} s",
            "event_freshness_live")
    return out


def assess(cop, question_id, events, rows, cfg=None):
    """One question: presence, reliability and sufficiency, with the evidence behind each."""
    cfg = cfg or signal_metrics.load_config()
    q = QUESTIONS[cop][question_id]
    items = []
    for metric, kind in q["requires"]:
        ev = events.get(metric)
        st, reason = item_state(ev)
        items.append({"requirement": metric, "kind": kind, "state": st, "reason": reason,
                      "value": (ev or {}).get("value"), "metric_state": (ev or {}).get("state"),
                      "evidence_refs": (ev or {}).get("evidence_refs", []),
                      "source": (ev or {}).get("source")})
    needed = [x for x in items if x["kind"] != OPTIONAL]
    missing = [x for x in needed if not (x["state"] == PRESENT or
               (x["kind"] == WHEN_APPLICABLE and x["state"] in (NOT_APPLICABLE, NOT_OBSERVED)))]
    presence = "PRESENT" if not missing else "PARTIAL" if any(x["state"] == PRESENT for x in needed) else "ABSENT"

    checks = reliability_checks(events, cfg)
    failed = [c for c in checks if c["outcome"] == FAILED and c["check"] in q["blocking"]]
    unchecked = [c for c in checks if c["outcome"] == NOT_CHECKED and c["check"] in q["blocking"]]
    other = [c for c in checks if c["outcome"] == FAILED and c["check"] not in q["blocking"]]
    reliability = "UNRELIABLE" if failed else "UNDETERMINED" if unchecked else \
        "RELIABLE_WITH_FINDINGS" if other else "RELIABLE"

    reasons = [{"code": x["state"], "requirement": x["requirement"], "detail": x["reason"]} for x in missing] + \
              [{"code": c["check"], "detail": c["detail"]} for c in failed] + \
              [{"code": c["not_checked_because"], "check": c["check"], "detail": c["detail"]} for c in unchecked]
    sufficiency = "INSUFFICIENT" if missing else "INCONCLUSIVE" if failed or unchecked else "SUFFICIENT"

    return {
        "cop": cop, "question_id": question_id, "question": q["question"],
        "presence": {"status": presence, "items": items, "fields": field_presence(rows, q["fields"])},
        "reliability": {"status": reliability, "checks": checks, "blocking_checks": q["blocking"]},
        "sufficiency": {"status": sufficiency, "reasons": reasons,
                        "missing_requirements": [x["requirement"] for x in missing]},
        "evidence_used": sorted({x["requirement"] for x in items if x["state"] == PRESENT}
                                | {c["source"] for c in checks if c["outcome"] in (PASSED, FAILED)}),
    }


def assess_session(cop, rows, session_id, cfg=None, computed_at=None):
    """All questions of one COP for one session, from raw observables."""
    cfg = cfg or signal_metrics.load_config()
    all_events, sessions = signal_metrics.compute(rows, cfg, computed_at=computed_at, catalogue=True)
    events = {e["metric_id"]: e for e in all_events if e["session_id"] == session_id}
    session = next(s for s in sessions if s.id == session_id)
    return {qid: assess(cop, qid, events, signal_metrics.unique_rows(session), cfg) for qid in QUESTIONS[cop]}
