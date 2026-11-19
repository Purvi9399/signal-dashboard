"""
Evidence assessment per behavioural question.

For one question of one COP, three answers kept apart:

  presence     is the telemetry the question needs there?
  reliability  can what is there be trusted (sources agree, provenance is
               verifiable, timestamps are comparable, evidence is current)?
  sufficiency  is it enough to answer this question?

Sufficiency says whether the question can be answered, not whether the answer
is good or bad. Nothing here decides a COP label or a confidence. Limits are
read from metrics_config.json "evidence_limits"; when a limit has not been
approved the check is NOT_EXECUTED, never passed.
"""

import os, sys

import yaml

HERE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import signal_metrics                                    # noqa: E402
import signal_traces                                     # noqa: E402

PRESENT = "PRESENT"
MISSING_DATA = "MISSING_DATA"
MISSING_REFERENCE = "MISSING_REFERENCE"
NOT_OBSERVED = "NOT_OBSERVED"
NOT_APPLICABLE = "NOT_APPLICABLE"
INSUFFICIENT_HISTORY = "INSUFFICIENT_HISTORY"
NOT_CALCULATED = "NOT_CALCULATED"
ITEM_STATES = (PRESENT, MISSING_DATA, MISSING_REFERENCE, NOT_OBSERVED, NOT_APPLICABLE, INSUFFICIENT_HISTORY,
               NOT_CALCULATED)

CONFLICTING_SOURCES = "CONFLICTING_SOURCES"
CONFLICTING_ACTION_SOURCES = "CONFLICTING_ACTION_SOURCES"
UNVERIFIABLE_PROVENANCE = "UNVERIFIABLE_PROVENANCE"
INCOMPARABLE_TIMESTAMPS = "INCOMPARABLE_TIMESTAMPS"
TIMESTAMP_ORDER = "TIMESTAMP_ORDER"
INGEST_LATENCY = "INGEST_LATENCY"
STALE = "STALE"
DISCONTINUITY = "DISCONTINUITY"
CHECKS = (CONFLICTING_SOURCES, CONFLICTING_ACTION_SOURCES, UNVERIFIABLE_PROVENANCE, INCOMPARABLE_TIMESTAMPS,
          TIMESTAMP_ORDER, INGEST_LATENCY, STALE, DISCONTINUITY)

VERIFIED_OK = "VERIFIED_OK"
VERIFIED_FAILED = "VERIFIED_FAILED"
COMPARISON_NOT_POSSIBLE = "COMPARISON_NOT_POSSIBLE"
EVIDENCE_MISSING = "EVIDENCE_MISSING"
NOT_EXECUTED = "NOT_EXECUTED"
OUTCOMES = (VERIFIED_OK, VERIFIED_FAILED, COMPARISON_NOT_POSSIBLE, EVIDENCE_MISSING, NOT_EXECUTED)

LIMIT_NOT_APPROVED = "LIMIT_NOT_APPROVED"
NO_EVALUATION_TIME = "NO_EVALUATION_TIME"
RAW_STORE_NOT_SUPPLIED = "RAW_STORE_NOT_SUPPLIED"

REQUIRED, WHEN_APPLICABLE, OPTIONAL = "REQUIRED", "REQUIRED_WHEN_APPLICABLE", "OPTIONAL"

ACTION_TYPES = signal_metrics.CALL_TYPES | signal_metrics.CALL_REQUESTS | signal_metrics.CALL_RESULTS

PACKAGES = os.path.join(HERE, "cops", "packages")
PACKAGED = ("runtime_monitoring",)


def load_package(cop):
    """A COP's question contract, from cops/packages/<cop>/METRICS.yaml."""
    with open(os.path.join(PACKAGES, cop, "METRICS.yaml")) as f:
        return yaml.safe_load(f)


def _questions(pkg):
    return {qid: {"question": q["question"].strip(), "fields": q["fields"],
                  "requires": [(r["metric"], r["kind"]) for r in q["requires"]],
                  "verified": q["verified"], "blocking": q["blocking"]}
            for qid, q in pkg["questions"].items()}


# A "verified" check is what the question asks about: either verified outcome answers it.
# A "blocking" check qualifies the evidence: a failure, or no way to run it, leaves it inconclusive.
QUESTIONS = {cop: _questions(load_package(cop)) for cop in PACKAGED}


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


def _agreement(check, rows):
    collectors = sorted({c for r in rows for c in ([r.get("collector")] + list(r.get("contributing") or [])) if c})
    compared = [r for r in rows if r.get("collectors_agree") is not None]
    bad = [r for r in compared if r["collectors_agree"] is False]
    base = {"check": check, "value": None, "evidence_refs": [f"observable:{r['observable_id']}" for r in bad],
            "evidence": {"collectors": collectors, "rows": len(rows), "cross_checked": len(compared),
                         "disagreed": len(bad)}}
    if not rows:
        return dict(base, outcome=EVIDENCE_MISSING, detail="no rows to compare")
    if len(collectors) < 2:
        return dict(base, outcome=COMPARISON_NOT_POSSIBLE,
                    detail=f"only one source ({collectors[0] if collectors else 'unnamed'}) observed this activity")
    if not any("collectors_agree" in r for r in rows):
        return dict(base, outcome=EVIDENCE_MISSING, detail="collectors_agree was not read for these rows")
    if not compared:
        return dict(base, outcome=COMPARISON_NOT_POSSIBLE,
                    detail=f"{len(collectors)} sources present but no event was seen by more than one of them")
    if bad:
        return dict(base, outcome=VERIFIED_FAILED, value=round(1 - len(bad) / len(compared), 3),
                    detail=f"{len(bad)} of {len(compared)} cross-checked events disagree between sources")
    return dict(base, outcome=VERIFIED_OK, value=1.0, detail=f"all {len(compared)} cross-checked events agree")


def _limited(check, value, limit_key, cfg, unit, what):
    limit = (cfg.get("evidence_limits") or {}).get(limit_key)
    base = {"check": check, "value": value, "limit": limit, "limit_key": limit_key, "evidence_refs": []}
    if limit is None:
        return dict(base, outcome=NOT_EXECUTED, not_executed_because=LIMIT_NOT_APPROVED,
                    detail=f"{what} is {value}{unit}; no evidence_limits.{limit_key} has been approved")
    if value > limit:
        return dict(base, outcome=VERIFIED_FAILED, detail=f"{what} {value}{unit} exceeds the approved {limit}{unit}")
    return dict(base, outcome=VERIFIED_OK, detail=f"{what} {value}{unit} is within the approved {limit}{unit}")


def resolve_raw_refs(rows, raw_events):
    """Which rows' raw_ref and derived_from do not resolve to a stored source event of the same session.

    raw_events are source_events rows (source_id, session_id, source_ref, file_origin). A raw_ref
    resolves when it is a source_ref of the session, the file a source_ref points into, or the
    file_origin of one; each derived_from id must be a source_id of the session.
    """
    by_session = {}
    for e in raw_events:
        by_session.setdefault(e.get("session_id"), []).append(e)
    unresolved, checked = [], 0
    for r in rows:
        mine = by_session.get(r.get("session_id"), [])
        refs = {e.get("source_ref") for e in mine} | {e.get("file_origin") for e in mine}
        files = {str(e.get("source_ref") or "").rsplit(":", 1)[0] for e in mine}
        ids = {str(e.get("source_id")) for e in mine}
        ref, derived = r.get("raw_ref"), [str(x) for x in (r.get("derived_from") or [])]
        if not ref and not derived:
            continue
        checked += 1
        bad = []
        if ref and ref not in refs and ref not in files:
            bad.append(f"raw_ref {ref}")
        bad += [f"derived_from {x}" for x in derived if x not in ids]
        if bad:
            unresolved.append({"observable_id": r.get("observable_id"), "unresolved": bad})
    return checked, unresolved


def reliability_checks(events, rows, cfg, computed_at=None, raw_events=None):
    """Each reliability check for the session, from its metric events and rows.

    raw_events: the stored source events the rows' raw_ref point to. Without them provenance
    cannot be resolved and that part of UNVERIFIABLE_PROVENANCE is NOT_EXECUTED."""
    ev = lambda m: events.get(m) or {}                                       # noqa: E731
    feats = lambda m: ev(m).get("features") or {}                            # noqa: E731
    out = []

    def add(check, outcome, detail, source, refs=(), **k):
        out.append({"check": check, "outcome": outcome, "detail": detail, "source": source,
                    "evidence_refs": list(refs), **k})

    def metric_missing(check, metric):
        if metric not in events:
            add(check, NOT_EXECUTED, f"{metric} was not calculated", metric, not_executed_because=NOT_CALCULATED)
        else:
            add(check, EVIDENCE_MISSING, feats(metric).get("reason") or ev(metric).get("error")
                or f"{metric} has no value", metric)

    out.append(dict(_agreement(CONFLICTING_SOURCES, rows), source="telemetry.source_agreement",
                    metric_value=ev("telemetry.source_agreement").get("value")))
    out.append(dict(_agreement(CONFLICTING_ACTION_SOURCES,
                               [r for r in rows if r.get("observable_type") in ACTION_TYPES]),
                    source="collectors_agree on action rows"))

    pv = ev("audit_source_provenance_completeness")
    if pv.get("value") is None:
        metric_missing(UNVERIFIABLE_PROVENANCE, "audit_source_provenance_completeness")
    elif pv["value"] < 1:
        f = pv["features"]
        add(UNVERIFIABLE_PROVENANCE, VERIFIED_FAILED, f"{f['rows'] - f['with_raw_ref']} of {f['rows']} rows have no "
            f"raw_ref, {f['rows'] - f['with_collector']} no collector", "audit_source_provenance_completeness")
    else:
        add(UNVERIFIABLE_PROVENANCE, VERIFIED_OK, "every row names its collector and raw event",
            "audit_source_provenance_completeness")
    un = ev("audit_unverifiable_record_rate")
    if un.get("value"):
        add(UNVERIFIABLE_PROVENANCE, VERIFIED_FAILED, f"{un['value']:.1%} of rows marked inferred name no source rows",
            "audit_unverifiable_record_rate", un.get("evidence_refs", []))
    if raw_events is None:
        add(UNVERIFIABLE_PROVENANCE, NOT_EXECUTED, "the raw event store was not supplied, so no raw_ref was resolved",
            "source_events", not_executed_because=RAW_STORE_NOT_SUPPLIED)
    else:
        checked, bad = resolve_raw_refs(rows, raw_events)
        if not checked:
            pass                                 # nothing names a raw event: the completeness check reports it
        elif bad:
            add(UNVERIFIABLE_PROVENANCE, VERIFIED_FAILED, f"{len(bad)} of {checked} rows name raw events that are not "
                "in the raw store for this session", "source_events",
                [f"observable:{x['observable_id']}" for x in bad], unresolved=bad)
        else:
            add(UNVERIFIABLE_PROVENANCE, VERIFIED_OK, f"all {checked} rows resolve to stored source events of this "
                "session", "source_events")

    ti, tf = ev("audit_timestamp_integrity"), feats("audit_timestamp_integrity")
    if ti.get("value") is None:
        metric_missing(INCOMPARABLE_TIMESTAMPS, "audit_timestamp_integrity")
        metric_missing(TIMESTAMP_ORDER, "audit_timestamp_integrity")
    else:
        total = tf["rows"] + tf["unreadable"]
        if tf["unreadable"]:
            add(INCOMPARABLE_TIMESTAMPS, VERIFIED_FAILED, f"{tf['unreadable']} of {total} rows have timestamps that "
                "cannot be read or compared", "audit_timestamp_integrity")
        else:
            add(INCOMPARABLE_TIMESTAMPS, VERIFIED_OK, f"all {total} rows have comparable timestamps",
                "audit_timestamp_integrity")
        if tf["violations"]:
            add(TIMESTAMP_ORDER, VERIFIED_FAILED, f"{tf['violations']} of {tf['rows']} rows were captured before they "
                "occurred or ingested before they were captured", "audit_timestamp_integrity",
                ti.get("evidence_refs", []))
        else:
            add(TIMESTAMP_ORDER, VERIFIED_OK, f"occurred, captured and ingested are in order in all {tf['rows']} rows",
                "audit_timestamp_integrity")

    fr = ev("event_freshness_live")
    if fr.get("value") is None:
        metric_missing(INGEST_LATENCY, "event_freshness_live")
    else:
        out.append(dict(_limited(INGEST_LATENCY, fr["value"], "max_ingest_lag_s", cfg, " s", "median ingest lag"),
                        source="event_freshness_live"))

    out.append(dict(evidence_age(rows, computed_at, cfg), source="occurred_at against the evaluation time"))

    me = ev("missing_event_rate")
    if me.get("state") == "NOT_APPLICABLE":
        add(DISCONTINUITY, VERIFIED_OK, "no call was observed, so no result is expected", "missing_event_rate")
    elif me.get("value") is None:
        metric_missing(DISCONTINUITY, "missing_event_rate")
    else:
        f = me["features"]
        out.append(dict(_limited(DISCONTINUITY, me["value"], "max_missing_event_rate", cfg, "",
                                 f"share of calls with no result ({f['without_result']} of {f['requests']})"),
                        source="missing_event_rate", evidence_refs=me.get("evidence_refs", [])))
    return out


def evidence_age(rows, computed_at, cfg):
    """Seconds from the newest observation to the evaluation time."""
    base = {"check": STALE, "value": None, "evidence_refs": []}
    if not computed_at:
        return dict(base, outcome=NOT_EXECUTED, not_executed_because=NO_EVALUATION_TIME,
                    detail="no evaluation time was given")
    at = signal_traces.ts(computed_at)
    seen = [t for t in (signal_traces.ts(r.get("occurred_at")) for r in rows) if t]
    if not seen:
        return dict(base, outcome=EVIDENCE_MISSING, detail="no row has a readable occurred_at")
    seen = [t for t in seen if at and (t.tzinfo is None) == (at.tzinfo is None)]
    if not seen:
        return dict(base, outcome=COMPARISON_NOT_POSSIBLE,
                    detail="occurred_at and the evaluation time cannot be compared (zoned and unzoned)")
    newest = max(seen)
    if newest > at:
        return dict(base, outcome=COMPARISON_NOT_POSSIBLE, newest_observation=newest.isoformat(),
                    contradicts="evaluation time",
                    detail=f"an observation ({newest.isoformat()}) is dated after the evaluation time")
    age = round((at - newest).total_seconds(), 1)
    out = _limited(STALE, age, "max_evidence_age_s", cfg, " s", "age of the newest observation at evaluation time")
    return dict(out, newest_observation=newest.isoformat(), evaluated_at=at.isoformat())


# Inputs of the checks whose source is not a metric event.
CHECK_INPUTS = {"telemetry.source_agreement": ["collectors_agree"],
                "collectors_agree on action rows": ["collectors_agree"],
                "occurred_at against the evaluation time": ["occurred_at"]}


# signal_metrics bases that say whether a kind of event was delivered at all. When none was and the
# tool declares no capability for it, the kind was not supplied. A value basis (ingest, status) that is
# absent is evidence missing.
EVENT_KIND_BASES = ("results", "plans", "compactions", "config_events", "approvals")


def not_supplied(rows, fields):
    """Fields no row carries at all: the loader or fixture never supplied them, which is not the same
    as a collector leaving them empty."""
    return [f for f in fields if rows and not any(f in r for r in rows)]


def assess(cop, question_id, events, rows, cfg=None, computed_at=None, raw_events=None):
    """One question: presence, reliability and sufficiency, with the evidence behind each."""
    cfg = cfg or signal_metrics.load_config()
    q = QUESTIONS[cop][question_id]
    items = []
    for metric, kind in q["requires"]:
        ev = events.get(metric)
        st, reason = item_state(ev)
        items.append({"requirement": metric, "kind": kind, "state": st, "reason": reason,
                      "value": (ev or {}).get("value"), "metric_state": (ev or {}).get("state"),
                      "features": (ev or {}).get("features") or {},
                      "evidence_refs": (ev or {}).get("evidence_refs", [])})
    needed = [x for x in items if x["kind"] != OPTIONAL]
    missing = [x for x in needed if not (x["state"] == PRESENT or
               (x["kind"] == WHEN_APPLICABLE and x["state"] in (NOT_APPLICABLE, NOT_OBSERVED)))]

    checks = reliability_checks(events, rows, cfg, computed_at, raw_events)
    asked = [c for c in checks if c["check"] in q["verified"]]
    lacking = [c for c in asked if c["outcome"] in (EVIDENCE_MISSING, COMPARISON_NOT_POSSIBLE)]
    blocking = [c for c in checks if c["check"] in q["blocking"]]
    failed = [c for c in blocking if c["outcome"] == VERIFIED_FAILED]
    unassessed = [c for c in blocking if c["outcome"] in (EVIDENCE_MISSING, NOT_EXECUTED)] + \
                 [c for c in asked if c["outcome"] == NOT_EXECUTED]
    other = [c for c in checks if c["outcome"] == VERIFIED_FAILED and c not in asked and c not in blocking]

    gap = missing or lacking
    presence = "PRESENT" if not gap else "PARTIAL" if any(x["state"] == PRESENT for x in needed) else "ABSENT"
    reliability = "UNRELIABLE" if failed else "UNDETERMINED" if unassessed or lacking else \
        "RELIABLE_WITH_FINDINGS" if other else "RELIABLE"

    def code(c):
        return c.get("not_executed_because") or c["outcome"]

    def absent(source):
        ev = events.get(source) or {}
        out = not_supplied(rows, ev.get("required_fields") or CHECK_INPUTS.get(source, []))
        if ev.get("basis_present") is False and ev.get("state") == "UNKNOWN" and ev.get("basis") in EVENT_KIND_BASES:
            out.append(f"{ev['basis']} events")
        return out

    reasons = [{"code": x["state"], "requirement": x["requirement"], "detail": x["reason"],
                "inputs_not_supplied": absent(x["requirement"])} for x in missing] + \
              [{"code": code(c), "check": c["check"], "detail": c["detail"], "inputs_not_supplied": absent(c["source"]),
                **({"contradicts": c["contradicts"]} if c.get("contradicts") else {})} for c in lacking] + \
              [{"code": c["check"], "check": c["check"], "detail": c["detail"]} for c in failed] + \
              [{"code": code(c), "check": c["check"], "detail": c["detail"],
                "inputs_not_supplied": absent(c["source"])} for c in unassessed]
    sufficiency = "INSUFFICIENT" if missing or lacking else "INCONCLUSIVE" if failed or unassessed else "SUFFICIENT"
    not_assessed = [{"requirement": x["requirement"], "state": x["state"], "detail": x["reason"]}
                    for x in items if x["kind"] == OPTIONAL and x["state"] != PRESENT] + \
                   [{"check": c["check"], "state": c["outcome"], "detail": c["detail"]}
                    for c in blocking if c["outcome"] == COMPARISON_NOT_POSSIBLE]

    return {
        "cop": cop, "question_id": question_id, "question": q["question"],
        "presence": {"status": presence, "items": items, "fields": field_presence(rows, q["fields"])},
        "reliability": {"status": reliability, "checks": checks, "blocking_checks": q["blocking"],
                        "verified_checks": q["verified"]},
        "findings": [{"check": c["check"], "role": "answer" if c in asked else "blocking" if c in blocking
                      else "context", "detail": c["detail"], "evidence_refs": c["evidence_refs"]}
                     for c in checks if c["outcome"] == VERIFIED_FAILED],
        "sufficiency": {"status": sufficiency, "reasons": reasons, "not_assessed": not_assessed,
                        "missing_requirements": [x["requirement"] for x in missing]},
        "evidence_used": sorted({x["requirement"] for x in items if x["state"] == PRESENT}
                                | {c["source"] for c in checks if c["outcome"] in (VERIFIED_OK, VERIFIED_FAILED)}),
    }


def session_inputs(rows, session_id, cfg, computed_at=None):
    """The session's catalogue metric events by id, and its de-duplicated rows."""
    all_events, sessions = signal_metrics.compute(rows, cfg, computed_at=computed_at, catalogue=True)
    events = {e["metric_id"]: e for e in all_events if e["session_id"] == session_id}
    session = next(s for s in sessions if s.id == session_id)
    return events, signal_metrics.unique_rows(session)


def assess_session(cop, rows, session_id, cfg=None, computed_at=None, raw_events=None):
    """All questions of one COP for one session, from raw observables."""
    cfg = cfg or signal_metrics.load_config()
    events, srows = session_inputs(rows, session_id, cfg, computed_at)
    return {qid: assess(cop, qid, events, srows, cfg, computed_at, raw_events) for qid in QUESTIONS[cop]}
