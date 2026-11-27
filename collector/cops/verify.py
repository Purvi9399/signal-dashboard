"""
Deterministic post-hoc verifier for COP decisions.

Pure functions, no I/O. Each check returns {id, passed, detail}.
The verifier checks any decision (rule or LLM) after the fact.

Checks V01-V16: structural consistency of a COPDecision against its spec.
V07b: cited metrics must be in the spec's metric_keys (scope check).

BLOCKING_CHECK_IDS: checks that trigger a retry when failed.
"""

from .models import ACTIONS, EVIDENCE_STATUSES, DECISION_STATES
from .specs import BY_KEY


BLOCKING_CHECK_IDS = frozenset({
    "V01",   # label allowed
    "V02",   # no more favourable than rule label
    "V03",   # action in permitted_actions
    "V04",   # action in global ACTIONS
    "V07",   # cited metrics exist in metrics_read
    "V07b",  # cited metrics in spec scope
    "V08",   # claims match metric state
    "V09",   # gap claims match UNKNOWN metrics
    "V10",   # no absence from missing data
    "V13",   # evidence_status present
    "V14",   # decision_state present
    "V15",   # evidence/decision consistent
})


def _check(vid, passed, detail=""):
    return {"id": vid, "passed": passed, "detail": detail}


def v01_label_allowed(decision, spec):
    """Label is one of the allowed interpretations."""
    if decision.failed:
        ok = decision.interpretation == "" or decision.interpretation in spec.interpretations
        return _check("V01", ok,
                       f"failed decision has interpretation={decision.interpretation!r}" if not ok else "")
    ok = decision.interpretation in spec.interpretations
    return _check("V01", ok,
                  f"{decision.interpretation} not in {spec.interpretations}" if not ok else "")


def v02_label_no_more_favourable(decision, spec, rule_label):
    """Label no more favourable than the rule label on the same inputs."""
    if decision.failed or rule_label is None:
        return _check("V02", True, "skipped: decision failed or no rule label")
    order = list(spec.interpretations)
    if decision.interpretation not in order or rule_label not in order:
        return _check("V02", False,
                      f"cannot compare: {decision.interpretation} or {rule_label} not in interpretations")
    ok = order.index(decision.interpretation) >= order.index(rule_label)
    return _check("V02", ok,
                  f"{decision.interpretation} is more favourable than rule label {rule_label}" if not ok else "")


def v03_action_permitted(decision, spec):
    """Action in permitted_actions."""
    ok = decision.action in spec.permitted_actions
    return _check("V03", ok,
                  f"{decision.action} not in {spec.permitted_actions}" if not ok else "")


def v04_action_in_global(decision):
    """Action is a known global action."""
    ok = decision.action in ACTIONS
    return _check("V04", ok, f"{decision.action} not in ACTIONS" if not ok else "")


_LABEL_ACTION_CEILING = {
    "SUFFICIENT": ("NONE", "WATCH"),
    "NORMAL": ("NONE", "WATCH"),
    "NO_INCIDENT": ("NONE", "WATCH"),
}

_LABEL_ACTION_FLOOR = {
    "INSUFFICIENT": ("ALERT", "INSTRUCT", "REQUEST_CONTEXT", "RECOMMEND",
                      "REQUEST_APPROVAL", "PASS_TO_COP", "ESCALATE", "PAUSE", "BLOCK"),
    "INCIDENT": ("ALERT", "INSTRUCT", "REQUEST_CONTEXT", "RECOMMEND",
                  "REQUEST_APPROVAL", "PASS_TO_COP", "ESCALATE", "PAUSE", "BLOCK"),
}


def v05_action_consistent_with_label(decision, spec):
    """Action consistent with label — benign labels should not escalate."""
    ceiling = _LABEL_ACTION_CEILING.get(decision.interpretation)
    if ceiling and decision.action not in ceiling and decision.action != "NONE":
        return _check("V05", False,
                      f"label {decision.interpretation} should not produce action {decision.action}")
    return _check("V05", True)


_LABEL_SEV = {
    "SUFFICIENT": ("NONE",),
    "NORMAL": ("NONE",),
    "NO_INCIDENT": ("NONE",),
    "INSUFFICIENT": ("MEDIUM", "HIGH", "CRITICAL"),
    "INCIDENT": ("HIGH", "CRITICAL"),
    "ANOMALOUS": ("LOW", "MEDIUM", "HIGH", "CRITICAL"),
}


def v06_severity_consistent_with_label(decision, spec):
    """Severity consistent with label."""
    expected = _LABEL_SEV.get(decision.interpretation)
    if expected and decision.severity not in expected:
        return _check("V06", False,
                      f"label {decision.interpretation} expects severity in {expected}, got {decision.severity}")
    return _check("V06", True)


def v07_cited_metrics_exist(decision):
    """Cited metrics exist in metrics_read."""
    read_ids = {mr["metric"] for mr in decision.metrics_read}
    if decision.metric_id and decision.metric_id not in read_ids:
        return _check("V07", False, f"cites {decision.metric_id} but it was not in metrics_read")
    return _check("V07", True)


def v07b_cited_metrics_in_scope(decision, spec):
    """Cited metrics and claims must reference only metrics in the spec's metric_keys (plus context_keys)."""
    allowed = set(spec.metric_keys) | set(spec.context_keys)
    bad = []
    if decision.metric_id and decision.metric_id not in allowed:
        bad.append(f"driver {decision.metric_id}")
    for claim in decision.claims:
        if claim["metric_id"] not in allowed:
            bad.append(f"claim on {claim['metric_id']}")
    for gap in decision.gaps:
        if gap["metric_id"] not in allowed:
            bad.append(f"gap on {gap['metric_id']}")
    if bad:
        return _check("V07b", False,
                      f"out of scope for {spec.key}: {'; '.join(bad)}; "
                      f"allowed: {sorted(allowed)}")
    return _check("V07b", True)


def v08_claims_match_metric_state(decision):
    """Every claim matches the metric's actual state."""
    read_map = {mr["metric"]: mr["state"] for mr in decision.metrics_read}
    mismatches = []
    for claim in decision.claims:
        actual = read_map.get(claim["metric_id"])
        if actual is None:
            mismatches.append(f"{claim['metric_id']}: not in metrics_read")
        elif actual != claim["state"]:
            mismatches.append(f"{claim['metric_id']}: claim says {claim['state']}, actual is {actual}")
    if mismatches:
        return _check("V08", False, "; ".join(mismatches))
    return _check("V08", True)


def v09_gap_claims_match_unknown(decision):
    """Every gap claim matches an UNKNOWN or NOT_COMPUTED metric."""
    read_map = {mr["metric"]: mr["state"] for mr in decision.metrics_read}
    bad = []
    for gap in decision.gaps:
        actual = read_map.get(gap["metric_id"])
        if actual not in ("UNKNOWN", "NOT_COMPUTED", None):
            bad.append(f"{gap['metric_id']}: state is {actual}, not UNKNOWN/NOT_COMPUTED")
    if bad:
        return _check("V09", False, "; ".join(bad))
    return _check("V09", True)


def v10_no_absence_from_missing(decision):
    """No claim that an event did not occur because data is missing."""
    for claim in decision.claims:
        if claim["state"] == "NOT_COMPUTED":
            return _check("V10", False,
                          f"claim on {claim['metric_id']} with state NOT_COMPUTED: "
                          "cannot claim non-occurrence from missing data")
    return _check("V10", True)


def v11_at_most_one_context_request(decision):
    """At most one context request."""
    ok = len(decision.context_used) <= 1
    return _check("V11", ok,
                  f"{len(decision.context_used)} context requests" if not ok else "")


def v12_confidence_not_above_evidence(decision):
    """Confidence not above evidence_score unless a reason is stated (D12)."""
    if decision.confidence is None or decision.evidence_confidence is None:
        return _check("V12", True, "no confidence or no evidence_score to compare")
    if decision.confidence > decision.evidence_confidence:
        has_reason = bool(decision.rationale and len(decision.rationale) > 10)
        return _check("V12", has_reason,
                      f"confidence {decision.confidence} > evidence_score {decision.evidence_confidence}"
                      + ("" if has_reason else " and no rationale explains why"))
    return _check("V12", True)


def v13_evidence_status_present(decision):
    """evidence_status present and valid."""
    ok = decision.evidence_status in EVIDENCE_STATUSES
    return _check("V13", ok,
                  f"evidence_status={decision.evidence_status!r} not in {EVIDENCE_STATUSES}" if not ok else "")


def v14_decision_state_present(decision):
    """decision_state present and valid."""
    ok = decision.decision_state in DECISION_STATES
    return _check("V14", ok,
                  f"decision_state={decision.decision_state!r} not in {DECISION_STATES}" if not ok else "")


def v15_evidence_decision_consistent(decision):
    """evidence_status and decision_state are consistent."""
    if decision.decision_state == "UNDECIDED" and decision.evidence_status != "INSUFFICIENT":
        return _check("V15", False,
                      f"UNDECIDED requires INSUFFICIENT evidence, got {decision.evidence_status}")
    return _check("V15", True)


def v16_severity_present(decision):
    """Severity is always emitted (D12)."""
    ok = decision.severity and decision.severity != ""
    return _check("V16", ok, "severity is empty" if not ok else "")


ALL_CHECKS = [
    v01_label_allowed, v02_label_no_more_favourable, v03_action_permitted,
    v04_action_in_global, v05_action_consistent_with_label,
    v06_severity_consistent_with_label, v07_cited_metrics_exist,
    v07b_cited_metrics_in_scope,
    v08_claims_match_metric_state, v09_gap_claims_match_unknown,
    v10_no_absence_from_missing, v11_at_most_one_context_request,
    v12_confidence_not_above_evidence, v13_evidence_status_present,
    v14_decision_state_present, v15_evidence_decision_consistent,
    v16_severity_present,
]


def verify(decision, spec=None, rule_label=None):
    """Run all checks on a decision. Returns list of {id, passed, detail}."""
    spec = spec or BY_KEY.get(decision.cop)
    if spec is None:
        return [_check("V00", False, f"no spec found for {decision.cop}")]
    results = []
    for fn in ALL_CHECKS:
        if fn is v02_label_no_more_favourable:
            results.append(fn(decision, spec, rule_label))
        elif fn is v07b_cited_metrics_in_scope:
            results.append(fn(decision, spec))
        elif fn.__code__.co_varnames[:2] == ("decision", "spec"):
            results.append(fn(decision, spec))
        else:
            results.append(fn(decision))
    return results
