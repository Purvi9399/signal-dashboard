"""
Individual COP packages (cops/packages/<cop>/): validation of the machine-readable
contract, and the per-question observation that turns an evidence assessment
into an answer.

An answer is given only when the question's evidence is SUFFICIENT; otherwise
the outcome is INCONCLUSIVE with the assessment's reasons. Observation uses
metric values, the states the approved thresholds in metrics_config.json give
them, and the reliability checks. It does not change the COP's label, action or
finding: rules.py still decides those.
"""

import json, os

from . import evidence_assessment as EA
from ..specs import BY_KEY

INCONCLUSIVE = "INCONCLUSIVE"
KINDS = (EA.REQUIRED, EA.WHEN_APPLICABLE, EA.OPTIONAL)
BY = ("deterministic", "llm")


def _registry():
    with open(os.path.join(EA.HERE, "metric_registry.json")) as f:
        return {m["id"]: m for m in json.load(f)["metrics"]}


def validate(cop):
    """Problems with a COP's METRICS.yaml, as a list of strings. Empty when valid."""
    sm = EA.signal_metrics
    pkg = EA.load_package(cop)
    reg = _registry()
    built = set(sm.METRIC_IDS) | set(sm.CATALOGUE_IDS) | {"telemetry.unknown_share"}
    spec = BY_KEY[cop]
    probs = []
    if pkg.get("cop") != cop:
        probs.append(f"cop is {pkg.get('cop')!r}, expected {cop!r}")
    mapped = {m for m, r in reg.items() if cop in r.get("consumers", [])}
    listed = set(pkg.get("mapping") or {})
    for m in sorted(mapped - listed):
        probs.append(f"mapping document assigns {m} but METRICS.yaml does not list it")
    for m in sorted(listed - mapped):
        probs.append(f"METRICS.yaml lists {m} but the mapping document does not assign it")
    for m in spec.metric_keys:
        if m not in listed:
            probs.append(f"specs.py metric_keys has {m}, missing from METRICS.yaml")
    used = {}
    for qid, q in (pkg.get("questions") or {}).items():
        for k in ("question", "fields", "requires", "verified", "blocking", "outcomes"):
            if k not in q:
                probs.append(f"{qid}: no {k}")
        for r in q.get("requires", []):
            if r.get("kind") not in KINDS:
                probs.append(f"{qid}: {r.get('metric')} has kind {r.get('kind')!r}")
            if r.get("metric") not in built and r.get("kind") != EA.OPTIONAL:
                probs.append(f"{qid}: {r.get('metric')} is not built and is not OPTIONAL")
            if r.get("metric") not in listed:
                probs.append(f"{qid}: {r.get('metric')} is not in the mapping list")
            used.setdefault(r.get("metric"), set()).add(qid)
        for c in q.get("verified", []) + q.get("blocking", []):
            if c not in EA.CHECKS:
                probs.append(f"{qid}: unknown check {c}")
        if set(q.get("verified", [])) & set(q.get("blocking", [])):
            probs.append(f"{qid}: a check is both verified and blocking")
        if INCONCLUSIVE in q.get("outcomes", []):
            probs.append(f"{qid}: INCONCLUSIVE is implied, not listed as an outcome")
        for d in q.get("depends_on", []):
            if d not in pkg["questions"] or d == qid:
                probs.append(f"{qid}: depends_on {d} is not another question")
    for m, entry in (pkg.get("mapping") or {}).items():
        declared = set((entry or {}).get("used_by") or [])
        actual = used.get(m, set())
        if declared != actual:
            probs.append(f"{m}: used_by says {sorted(declared)}, questions use {sorted(actual)}")
        if not declared and not (entry or {}).get("note"):
            probs.append(f"{m}: unused without a note saying why")
    for key, lim in (pkg.get("limits") or {}).items():
        if lim.get("check") not in EA.CHECKS:
            probs.append(f"limit {key}: unknown check {lim.get('check')}")
    for name, op in (pkg.get("operations") or {}).items():
        if op.get("by") not in BY:
            probs.append(f"operation {name}: by is {op.get('by')!r}")
    return probs


# ---------------------------------------------------------------- Runtime Monitoring

def _item(a, metric):
    return next(x for x in a["presence"]["items"] if x["requirement"] == metric)


def _check(a, name):
    return next(c for c in a["reliability"]["checks"] if c["check"] == name)


def _rm1(a):
    cov = _item(a, "telemetry.collector_coverage")
    f = cov["features"]
    basis = {"value": cov["value"], "state": cov["metric_state"], "expected": f.get("expected"),
             "present": f.get("present"), "missing": f.get("missing")}
    return ("EXPECTED_COLLECTORS_MISSING" if f.get("missing") else "ALL_EXPECTED_COLLECTORS_CONTRIBUTED"), basis


def _rm2(a):
    c = _check(a, EA.CONFLICTING_SOURCES)
    return ("SOURCES_DISAGREE" if c["outcome"] == EA.VERIFIED_FAILED else "SOURCES_AGREE"), \
        {**c["evidence"], "agreement": c["value"], "disagreeing": c["evidence_refs"]}


def _rm3(a):
    turn, lane = _item(a, "telemetry.turn_linkage"), _item(a, "telemetry.lane_linkage")
    basis = {"turn_linkage": turn["value"], "turn_state": turn["metric_state"],
             "steps_without_turn": turn["features"].get("without_turn"),
             "lane_linkage": lane["value"], "lane_state": lane["state"]}
    if turn["value"] == 0:
        return "UNLINKED", basis
    if turn["value"] == 1 and (lane["state"] != EA.PRESENT or lane["value"] == 1):
        return "ALL_ACTIONS_LINKED", basis
    return "PARTIALLY_LINKED", basis


def _rm4(a):
    checks = {k: _check(a, k) for k in (EA.INGEST_LATENCY, EA.STALE, EA.DISCONTINUITY)}
    failed = sorted(k for k, c in checks.items() if c["outcome"] == EA.VERIFIED_FAILED)
    basis = {k: {"value": c.get("value"), "limit": c.get("limit"), "outcome": c["outcome"]} for k, c in checks.items()}
    return ("NOT_CURRENT_OR_CONTINUOUS" if failed else "CURRENT_AND_CONTINUOUS"), {**basis, "failed": failed}


def _rm5(a, earlier):
    unk = _item(a, "telemetry.unknown_share")
    causes = []
    if unk["metric_state"] != "NORMAL":
        causes.append(f"telemetry.unknown_share {unk['value']} is {unk['metric_state']} against its approved "
                      "threshold")
    for qid, ans in earlier.items():
        if ans["outcome"] == INCONCLUSIVE:
            causes.append(f"{qid} could not be answered ({ans['sufficiency']})")
        elif ans["outcome"] in INCOMPLETE:
            causes.append(f"{qid}: {ans['outcome']}")
    basis = {"unknown_share": unk["value"], "unknown_share_state": unk["metric_state"],
             "unknown": unk["features"].get("unknown"), "causes": causes}
    return ("MUST_REMAIN_UNCERTAIN" if causes else "COMPLETE_AND_TRUSTWORTHY"), basis


# Answers to RM-1..RM-4 that mean part of the evidence is missing or unusable.
INCOMPLETE = ("EXPECTED_COLLECTORS_MISSING", "PARTIALLY_LINKED", "UNLINKED", "NOT_CURRENT_OR_CONTINUOUS")

OBSERVE = {"RM-1": _rm1, "RM-2": _rm2, "RM-3": _rm3, "RM-4": _rm4}


def observe_runtime_monitoring(assessments):
    """{question_id: assessment} -> {question_id: answer}."""
    pkg = EA.load_package("runtime_monitoring")["questions"]
    out = {}
    for qid in pkg:
        a = assessments[qid]
        suf = a["sufficiency"]["status"]
        if suf != "SUFFICIENT":
            out[qid] = {"outcome": INCONCLUSIVE, "sufficiency": suf, "basis": {},
                        "because": a["sufficiency"]["reasons"]}
            continue
        if qid == "RM-5":
            outcome, basis = _rm5(a, {k: out[k] for k in pkg[qid].get("depends_on", [])})
        else:
            outcome, basis = OBSERVE[qid](a)
        if outcome not in pkg[qid]["outcomes"]:
            raise ValueError(f"{qid}: outcome {outcome} is not in METRICS.yaml")
        out[qid] = {"outcome": outcome, "sufficiency": suf, "basis": basis,
                    "findings": a["findings"], "not_assessed": a["sufficiency"]["not_assessed"]}
    return out


def assess_runtime_monitoring(rows, session_id, cfg=None, computed_at=None):
    """The five questions for one session: each assessment with its answer."""
    assessments = EA.assess_session("runtime_monitoring", rows, session_id, cfg, computed_at)
    answers = observe_runtime_monitoring(assessments)
    return {qid: {"assessment": assessments[qid], "answer": answers[qid]} for qid in assessments}
