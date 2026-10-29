"""
Evidence semantics: what each metric row can and cannot support.

The metric layer already separates UNKNOWN from NOT_APPLICABLE. This module
adds the two cases it cannot see from inside one row: a row that is malformed
(INVALID) and a row that never arrived (MISSING), and summarises the lot for
one COP so a rule or a model reads one consistent picture.

Nothing here judges behaviour. It only says what the evidence allows.
"""

import math

from ..models import STATES
from .contracts import (OBSERVED, UNKNOWN, NOT_APPLICABLE, INVALID, MISSING,
                        JUDGED_STATES, ATTENTION_STATES)


def classify(event, case_session=None):
    """(evidence_state, reason) for one MetricEvent."""
    st = event.state
    if st not in STATES:
        return INVALID, f"state {st!r} is not a metric state"
    if case_session and event.session_id and event.session_id != case_session:
        return INVALID, f"row belongs to session {event.session_id}, not {case_session}"
    v = event.value
    if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
        return INVALID, "value is not a finite number"
    if st in JUDGED_STATES:
        if v is None:
            return INVALID, f"state {st} with no value: nothing was measured to judge"
        if not event.source or not event.metric_version:
            return INVALID, "no source or metric_version: provenance cannot be checked"
        if st in ATTENTION_STATES and not event.basis_present and not v:
            return INVALID, "attention state with no basis and a zero value"
        return OBSERVED, ""
    if st == "UNKNOWN":
        return UNKNOWN, (event.features or {}).get("reason") or "could not be observed"
    if st == "NOT_APPLICABLE":
        return NOT_APPLICABLE, (event.features or {}).get("reason") or "condition did not arise"
    return INVALID, f"unhandled state {st}"


def assess(events, wanted, case_session=None):
    """Per-metric evidence picture for one COP.

    events: MetricEvents that arrived. wanted: metric ids the COP reads.
    Returns a JSON-able dict, the same shape for every COP.
    """
    by = {}
    for e in events:
        if e.metric_id in by:                   # duplicate row for one metric: invalid
            by[e.metric_id] = (e, INVALID, "duplicate rows for one metric")
            continue
        s, why = classify(e, case_session)
        by[e.metric_id] = (e, s, why)
    rows = []
    for m in wanted:
        if m not in by:
            rows.append({"metric": m, "evidence": MISSING, "reason": "no row computed for this case",
                         "state": None, "value": None})
            continue
        e, s, why = by[m]
        rows.append({"metric": m, "evidence": s, "reason": why, "state": e.state,
                     "value": e.value, "threshold": e.threshold,
                     "evidence_refs": list(e.evidence_refs[:20]), "source": e.source,
                     "metric_version": e.metric_version})
    count = lambda k: sum(1 for r in rows if r["evidence"] == k)
    observed = [r for r in rows if r["evidence"] == OBSERVED]
    return {
        "rows": rows,
        "observed": count(OBSERVED), "unknown": count(UNKNOWN),
        "not_applicable": count(NOT_APPLICABLE), "invalid": count(INVALID),
        "missing": count(MISSING),
        "attention": [r["metric"] for r in observed if r["state"] in ATTENTION_STATES],
        # A COP may conclude only from OBSERVED rows. NOT_APPLICABLE alone
        # supports "the condition did not arise", which is a reading, but it
        # never supports an attention label.
        "judgeable": bool(observed),
        "only_not_applicable": not observed and count(NOT_APPLICABLE) > 0
                               and count(UNKNOWN) + count(MISSING) + count(INVALID) == 0,
    }


def usable(events, picture):
    """The events a rule may read: OBSERVED, UNKNOWN and NOT_APPLICABLE rows.

    INVALID rows are withheld from interpretation entirely. Rules already
    treat UNKNOWN and NOT_APPLICABLE correctly; giving them an INVALID row
    would let a malformed value be judged.
    """
    bad = {r["metric"] for r in picture["rows"] if r["evidence"] == INVALID}
    return [e for e in events if e.metric_id not in bad]
