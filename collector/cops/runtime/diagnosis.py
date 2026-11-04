"""
Why a bench case ended where it did: three separate aspects, never merged.

    decision_status   what the COP's finding is (SUBSTANTIVE, INCONCLUSIVE,
                      MISSING_DEPENDENCY, REJECTED_BY_VERIFIER, FAILED)
    root_causes       why, as one or more codes (they can co-occur: a verifier
                      rejection can be caused by a missing dependency)
    evaluation        how the result compares with the independent expectation

Root-cause codes:
    EXPECTED_INCONCLUSIVE  INCONCLUSIVE was the expected, correct outcome
    MISSING_EVIDENCE       required evidence (an artifact, observable telemetry) is unavailable
    MISSING_DEPENDENCY     a required source or upstream finding is unavailable or unverified
    VERIFIER_REJECTED      a candidate was rejected by a named blocking check
    MODEL_UNCERTAIN        the model stopped without a conclusion although the evidence it
                           needed was present
    EVALUATION_MISMATCH    the final outcome differs from the expected one

Everything is derived from the recorded trace. The engineering notes in
ENGINEERING_NOTES are this branch's analysis of specific cases and are
labelled as such where they are shown.
"""

INCONCLUSIVE = "INCONCLUSIVE"
CODES = ("EXPECTED_INCONCLUSIVE", "MISSING_EVIDENCE", "MISSING_DEPENDENCY", "VERIFIER_REJECTED",
         "MODEL_UNCERTAIN", "EVALUATION_MISMATCH")

# Analysis of the ten cases that did not pass on 2026-11-03 and fleet F2 (see
# docs/cops/COPS_INCONCLUSIVE_ROOT_CAUSE_REPORT.md). fix: what was changed in code;
# remaining: what still needs a live call or an agreement.
ENGINEERING_NOTES = {
    "workflow_orchestration:abnormal": {
        "cause": "The claim's number was right and its citation wrong: 5 identical TEST steps make 4 "
                 "self-transitions, but the claim cited longest_run.length, which counts steps (5).",
        "fix": "analyse_execution_transitions now returns longest_run.steps and "
               "longest_run.self_transitions beside length; the prompt says what each counts. SC-01 "
               "is unchanged and still rejects the recorded claim.",
        "remaining": "A live re-assessment (recovery path) or a fresh run must cite the right field."},
    "classification_tagging:normal": {
        "cause": "inspect_event_metadata returns only rows that carry an indicator, so on a clean "
                 "session it returned an empty list; the model read that as 'metadata unavailable'.",
        "fix": "The tool now reports scanned_rows, metadata_available and the indicator fields checked, "
               "and the prompt defines UNCLASSIFIED as 'examined, no defining indicator'.",
        "remaining": "The recorded reply is still inconclusive; a fresh call is needed to show the "
                     "clarified evidence changes the model's reading."},
    "risk_mitigation:normal": {
        "cause": "The model disclosed that Resource Management could not decide ('INCONCLUSIVE, not "
                 "evidence'). SC-01 rejected any citation of an unverified upstream finding, even one "
                 "stating exactly that it supports nothing. Resource Management is context for Risk "
                 "Mitigation, not a requirement of LOW.",
        "fix": "SC-01 accepts an upstream claim that states the upstream's INCONCLUSIVE label exactly, "
               "as a disclosure that counts as no support. A claim relying on an unverified "
               "substantive upstream label still fails.",
        "remaining": None},
    "governance_intelligence:normal": {
        "cause": "Same as Risk Mitigation: an accurate disclosure that Recovery & Resilience was "
                 "INCONCLUSIVE was rejected. Governance Intelligence reads the decision store; Recovery "
                 "& Resilience is context only.",
        "fix": "As for Risk Mitigation (SC-01 upstream disclosure).", "remaining": None},
    "governance_intelligence:abnormal": {
        "cause": "Same as the normal case.", "fix": "As for Risk Mitigation (SC-01 upstream disclosure).",
        "remaining": None},
    "workload_optimization:abnormal": {
        "cause": "Two separate faults. (1) The model's token claim had kind metric_value but no "
                 "metric_id; the metric was only in evidence_refs, so SC-01 had nothing to check. "
                 "(2) The rule baseline read EFFICIENT because insights.repetition counts a repeat only "
                 "when the whole argument payload matches within a turn; these repeats differ in an "
                 "incidental argument field, so the metric read 0.",
        "fix": "inspect_repeated_operations now counts repeats by target with nothing written in "
               "between; the rule reads REDUNDANT when that count is in an attention state on "
               "insights.repetition's own thresholds. SC-01 now names the malformed citation exactly.",
        "remaining": "The recorded claim is still malformed; a live re-assessment must cite metric_id."},
    "audit_traceability:abnormal": {
        "cause": "insights.unattributed = 3 is WARNING on the configured thresholds (warning 1, "
                 "exception 5), and the rule mapped WARNING to PARTIAL ('gaps that do not yet break "
                 "attribution'). But three disk changes that no step accounts for are a break in "
                 "attribution.",
        "fix": "Labels now follow meaning, with severity reported separately (decision 2026-11-04): "
               "PARTIAL = incomplete but reconstructable; BROKEN_TRACE = an essential causal or "
               "provenance link is broken; UNATTRIBUTABLE = the responsible actor cannot be "
               "established. Here the operator and session are recorded but no step accounts for "
               "the three disk changes, so BROKEN_TRACE.",
        "remaining": "None (approved)."},
    "knowledge_policy_management:abnormal": {
        "cause": "The rules found CONFLICT with compare_policy_documents. The model never called that "
                 "tool and never saw its result (the payload did not include baseline tool results), "
                 "so it concluded CURRENT. DS-03 rejected the quiet reading, correctly.",
        "fix": "The payload now carries baseline_tool_results (results only; the baseline label stays "
               "hidden), the prompt requires a conflict check before CURRENT, and a DS-03 rejection is "
               "recoverable by one re-assessment.",
        "remaining": "A fresh call is needed; DS-03 is unchanged."},
    "governance_architecture:normal": {
        "cause": "The model judged two computed metrics with no COP consumer (insights.no_oversight, "
                 "insights.lockfile) to be a GAP. Under the two-level assessment (decision 2026-11-04) "
                 "neither is a required gap: no_oversight is covered by Policy Enforcement's "
                 "approval-requirement check, and lockfile is documented in cops_topology.json but "
                 "assigned by no spec and covered by no control. There is no active gap.",
        "fix": "query_coverage_matrix adds estate_gap_assessment (required_by_spec, documented "
               "consumers, alternative controls, classification); the rule and prompt read GAP only "
               "for an active gap or a required, uncovered latent gap. Expectation unchanged (ADEQUATE).",
        "remaining": "A fresh call must read the two-level evidence; whether lockfile should become "
                     "a required responsibility is a specification decision."},
    "governance_architecture:abnormal": {
        "cause": "One claim (telemetry unknown_share is 0.0, NORMAL) had kind metric_state with no "
                 "metric_id, no asserted_state and no reference: it cited nothing. The other claims "
                 "were correct and browser_navigation was correctly named.",
        "fix": "SC-01 now names the uncited claim; it is recoverable by one re-assessment.",
        "remaining": "A live re-assessment is needed; the claim is not dropped or rewritten."},
    "fleet:F2": {
        "cause": "Risk Mitigation's EMERGING (which matches the rule baseline) was rejected by SC-01: one "
                 "claim had kind metric_state with no metric_id and no asserted_state, naming "
                 "metric:insights.secrets only in evidence_refs. Policy Enforcement (VIOLATION) and "
                 "Incident Response (INCIDENT) were verified and propagated correctly.",
        "fix": "Same citation guidance and recovery path as Workload Optimization.",
        "remaining": "The fleet run predates reply recording, so it cannot be replayed; it needs a "
                     "live re-run."},
}


def _fails(trace):
    return [v for v in trace.get("verification", []) if v["result"] == "FAIL"
            and v["check"] not in ("AU-01", "PE-01", "AU-02", "EX-01")]


def explain_check(v):
    """One sentence per failed check, with field, asserted and observed values when known."""
    out = []
    for f in v.get("failures") or []:
        what = f"{v['check']} rejected claim {f['claim']} ({f['kind']}): {f['message']}"
        out.append({"check": v["check"], "code": f["code"], "field": f.get("field"),
                    "asserted": f.get("asserted"), "observed": f.get("observed"),
                    "recoverable": f.get("recoverable"), "sentence": what, "statement": f.get("statement")})
    if not out:
        out.append({"check": v["check"], "code": v.get("reason"), "field": None, "asserted": None,
                    "observed": None, "recoverable": v["check"] == "DS-03",
                    "sentence": f"{v['check']} {v.get('reason')}: {v.get('detail')}"})
    return out


def diagnose(rec):
    """rec: a bench case record (test_bench.record shape)."""
    t = rec.get("trace") or {}
    label, status = rec.get("label"), rec.get("status")
    accept = rec.get("expected") or []
    interp = t.get("interpretation") or {}
    causes = []
    if label == INCONCLUSIVE and INCONCLUSIVE in accept:
        causes.append("EXPECTED_INCONCLUSIVE")
    fails = _fails(t)
    sentences = [s for v in fails for s in explain_check(v)]
    codes = {s["code"] for s in sentences}
    reason_codes = set(t.get("reason_codes") or [])
    ds01 = [v for v in fails if v["check"] == "DS-01"]
    suf = [r for r in ((t.get("sufficiency") or {}).get("reasons") or [])
           if r.get("code") == "MISSING_EVIDENCE" and r.get("gates") in ("quiet_label", "all_labels")]
    # required evidence for the label in question was unavailable (an absent artifact that
    # gates the quiet label counts only when the outcome is inconclusive or the quiet label)
    quietish = label == INCONCLUSIVE or label == (accept[0] if accept else None)
    if "MISSING_EVIDENCE" in reason_codes or any("required evidence unavailable" in v["detail"]
                                                  or "INSUFFICIENT" in v["detail"] for v in ds01) \
            or (suf and quietish and label == INCONCLUSIVE):
        causes.append("MISSING_EVIDENCE")
    if status == "MISSING_DEPENDENCY" or "UPSTREAM_UNVERIFIED" in codes or t.get("missing_dependencies"):
        causes.append("MISSING_DEPENDENCY")
    if status == "REJECTED_BY_VERIFIER":
        causes.append("VERIFIER_REJECTED")
    llm = t.get("llm") or []
    if label == INCONCLUSIVE and llm and llm[-1].get("decision") == "inconclusive" \
            and "MISSING_EVIDENCE" not in causes and "MISSING_DEPENDENCY" not in causes:
        causes.append("MODEL_UNCERTAIN")
    if rec.get("verdict") != "PASS":
        causes.append("EVALUATION_MISMATCH")
    unexpected_inc = label == INCONCLUSIVE and INCONCLUSIVE not in accept
    rc = t.get("recovery") or {}
    recoverable = rc.get("recoverable") if rc else (bool(sentences) and all(s["recoverable"] for s in sentences)
                                                  if sentences else None)
    cand = interp.get("rejected_candidate")
    if cand is None and llm:
        cand = llm[-1].get("candidate_label")
    if cand is None and interp.get("gated_label"):
        cand = interp.get("gated_label")
    return {"decision_status": status, "root_causes": causes,
            "evaluation": {"verdict": rec.get("verdict"), "outcome": rec.get("outcome"),
                           "inconclusive_class": ("expected" if label == INCONCLUSIVE and not unexpected_inc
                                                  else "unexpected" if unexpected_inc else None)},
            "expected": accept, "candidate": cand, "final": label,
            "checks": sentences, "responsible_checks": sorted({s["check"] for s in sentences}),
            "missing": t.get("missing_evidence", []) + t.get("missing_dependencies", []),
            "recoverable": recoverable, "recovery": rc or None,
            "note": ENGINEERING_NOTES.get(rec["id"])}
