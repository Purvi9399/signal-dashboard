"""
Controlled evolution: proposals, never self-modification.

When a replay finds an evidence gap, proposals_from() writes down what could close it.
A proposal is data. No function here, or anywhere it is passed, changes a collector, a
schema, a policy, a threshold, a COP's authority or a configuration. The lifecycle is
the same as activation.MappingRegistry's:

    PROPOSED -> APPROVED (a named person, not the proposer, in the required role)
             -> IMPLEMENTED (a person records the versioned change that was made, and its rollback)
             -> ROLLED_BACK
    PROPOSED -> REJECTED

Five kinds of state stay apart (replay.memory_layers counts them):
    current evaluation        LangGraph state of one run
    historical observations   MemoryGateway records, UNVALIDATED or VERIFIED, and decision rows
    verified outcomes         MemoryGateway records VALIDATED by an independent reviewer
    proposed improvements     this registry, PROPOSED
    approved changes          this registry, APPROVED or IMPLEMENTED, each with a version and rollback
"""

import json
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone

from .activation import HUMAN, NotApproved
from .contracts import stable_id

COLLECT_FIELD = "COLLECT_FIELD"
COLLECTOR_CONFIG = "COLLECTOR_CONFIG"
OBSERVABLE_DEFINITION = "OBSERVABLE_DEFINITION"
METRIC_CALCULATION = "METRIC_CALCULATION"
HISTORICAL_BASELINE = "HISTORICAL_BASELINE"
INTERPRETATION_RULE = "INTERPRETATION_RULE"
TEST_SCENARIO = "TEST_SCENARIO"
KINDS = (COLLECT_FIELD, COLLECTOR_CONFIG, OBSERVABLE_DEFINITION, METRIC_CALCULATION, HISTORICAL_BASELINE,
         INTERPRETATION_RULE, TEST_SCENARIO)

PROPOSED, APPROVED, REJECTED, IMPLEMENTED, ROLLED_BACK = (
    "PROPOSED", "APPROVED", "REJECTED", "IMPLEMENTED", "ROLLED_BACK")

# Who must approve each kind. A role, checked against the approver's stated role.
APPROVER = {COLLECT_FIELD: "collector_owner", COLLECTOR_CONFIG: "collector_owner",
            OBSERVABLE_DEFINITION: "schema_owner", METRIC_CALCULATION: "metric_owner",
            HISTORICAL_BASELINE: "cop_owner", INTERPRETATION_RULE: "cop_owner", TEST_SCENARIO: "cop_owner"}
REQUIRED = ("trigger", "affected", "change", "expected_benefit", "compatibility", "verification", "approval",
            "versioning")


@dataclass
class Proposal:
    kind: str
    trigger: dict            # the gap: code, metric or field, tool, counts, replay_id
    affected: dict           # {"metrics": [...], "questions": [...], "cops": [...]}
    change: str
    expected_benefit: str
    compatibility: str
    verification: str
    approval: str            # role that must approve
    versioning: dict         # {"target": ..., "from": ..., "to": ..., "rollback": ...}
    proposed_by: str = "replay"
    proposal_id: str = ""
    status: str = PROPOSED
    history: list = field(default_factory=list)

    def __post_init__(self):
        if not self.proposal_id:
            key = {k: getattr(self, k) for k in ("kind", "trigger", "affected", "change")}
            self.proposal_id = stable_id("evolution", json.dumps(key, sort_keys=True, default=str))

    def to_dict(self):
        return asdict(self)


def validate(p):
    errs = []
    if p.kind not in KINDS:
        errs.append(f"kind {p.kind!r} is not one of {KINDS}")
    for k in REQUIRED:
        if not getattr(p, k):
            errs.append(f"{k} is empty")
    if p.approval != APPROVER.get(p.kind):
        errs.append(f"{p.kind} needs approval by {APPROVER.get(p.kind)}, not {p.approval!r}")
    if not (p.versioning or {}).get("rollback"):
        errs.append("versioning has no rollback")
    return errs


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class EvolutionRegistry:
    """Proposals and their decisions. It holds records; it applies nothing."""

    def __init__(self, clock=_now):
        self.clock = clock
        self.items = {}

    def propose(self, p):
        if p.proposal_id in self.items:
            return self.items[p.proposal_id]
        errs = validate(p)
        if errs:
            raise ValueError("; ".join(errs))
        p.history.append({"at": self.clock(), "event": PROPOSED, "by": p.proposed_by})
        self.items[p.proposal_id] = p
        return p

    def _person(self, pid, by, kind, want):
        p = self.items[pid]
        if kind != HUMAN:
            raise NotApproved(f"{kind} {by!r} cannot decide a proposal; only a named person can")
        if p.status != want:
            raise NotApproved(f"{pid} is {p.status}, not {want}")
        return p

    def approve(self, pid, by, kind, role):
        p = self._person(pid, by, kind, PROPOSED)
        if by == p.proposed_by:
            raise NotApproved("the proposer cannot approve their own proposal")
        if role != p.approval:
            raise NotApproved(f"{p.kind} needs {p.approval}; {by!r} approved as {role!r}")
        p.status = APPROVED
        p.history.append({"at": self.clock(), "event": APPROVED, "by": by, "role": role})
        return p

    def reject(self, pid, by, kind, reason):
        p = self._person(pid, by, kind, PROPOSED)
        p.status = REJECTED
        p.history.append({"at": self.clock(), "event": REJECTED, "by": by, "reason": reason})
        return p

    def record_implemented(self, pid, by, kind, version, change_ref):
        """A person records the change they made (commit, migration, config version). Nothing is applied here."""
        p = self._person(pid, by, kind, APPROVED)
        if not version or not change_ref:
            raise ValueError("an implemented change needs a version and a change reference")
        p.status = IMPLEMENTED
        p.history.append({"at": self.clock(), "event": IMPLEMENTED, "by": by, "version": version,
                          "change_ref": change_ref})
        return p

    def roll_back(self, pid, by, kind, reason):
        p = self._person(pid, by, kind, IMPLEMENTED)
        p.status = ROLLED_BACK
        p.history.append({"at": self.clock(), "event": ROLLED_BACK, "by": by, "reason": reason,
                          "rollback": p.versioning["rollback"]})
        return p

    def active_changes(self):
        return [p for p in self.items.values() if p.status == IMPLEMENTED]

    def to_dict(self):
        return {"proposals": [p.to_dict() for p in self.items.values()]}

    def save(self, path):
        with open(path, "w") as f:
            json.dump(self.to_dict(), f, indent=1, default=str)

    @classmethod
    def load(cls, path, clock=_now):
        r = cls(clock)
        with open(path) as f:
            for d in json.load(f)["proposals"]:
                p = Proposal(**d)
                r.items[p.proposal_id] = p
        return r


# ---------------------------------------------------------------- proposals from a replay

def _questions_using(metrics):
    from . import evidence_assessment as EA
    out = []
    for cop in EA.PACKAGED:
        for qid, q in EA.QUESTIONS[cop].items():
            if any(m in metrics for m, _ in q["requires"]):
                out.append(f"{cop}:{qid}")
    return sorted(out)


def proposals_from(profile, rep):
    """Proposals for the gaps a replay's evidence profile shows. Deterministic; nothing is applied."""
    rid = profile["replay_id"]
    out = []
    by_field = {}
    for key, m in profile["metrics"].items():
        mid, _ = key.split("|", 1)
        for f in m["required_fields"]:
            by_field.setdefault(f, set()).add(mid)
    for tool, t in sorted(profile["tools"].items()):
        for f, st in sorted(t["fields"].items()):
            if st["rows"] and st["populated"] == 0 and f in by_field:
                ms = sorted(by_field[f])
                out.append(Proposal(
                    COLLECT_FIELD, {"code": "FIELD_NEVER_POPULATED", "field": f, "tool": tool, "rows": st["rows"],
                                    "supplied": st["supplied"], "replay_id": rid},
                    {"metrics": ms, "questions": _questions_using(ms), "cops": []},
                    f"populate {f} for {tool} rows", f"{len(ms)} metric(s) become calculable for {tool}",
                    "additive column value; existing rows stay as they are",
                    "re-run the replay on new rows; the field's population and the metrics' calculability rise",
                    APPROVER[COLLECT_FIELD],
                    {"target": f"collector capture for {tool}", "from": "not populated", "to": "populated",
                     "rollback": "stop populating the field; metrics return to their reported missing state"}))
        for c, n in sorted(t["sessions_missing_collector"].items()):
            out.append(Proposal(
                COLLECTOR_CONFIG, {"code": "EXPECTED_COLLECTOR_MISSING", "collector": c, "tool": tool,
                                   "sessions": n, "of": t["sessions"], "replay_id": rid},
                {"metrics": ["telemetry.collector_coverage"], "questions": ["runtime_monitoring:RM-1"],
                 "cops": ["runtime_monitoring"]},
                f"check that {c} is configured for {tool} where it is expected", "fewer sessions with a missing source",
                "configuration on the operator's machine; no schema change",
                "collector_capability shows the collector reporting; RM-1 answers ALL_EXPECTED_COLLECTORS_CONTRIBUTED",
                APPROVER[COLLECTOR_CONFIG],
                {"target": f"{c} configuration for {tool}", "from": "missing in some sessions", "to": "configured",
                 "rollback": "restore the previous collector configuration"}))
        unresolved = t["provenance"].get("unresolved", 0)
        if unresolved:
            out.append(Proposal(
                OBSERVABLE_DEFINITION, {"code": "UNRESOLVED_RAW_REF", "tool": tool, "sessions": unresolved,
                                        "replay_id": rid},
                {"metrics": ["audit_source_provenance_completeness"],
                 "questions": ["runtime_monitoring:RM-3", "runtime_monitoring:RM-5"], "cops": ["runtime_monitoring"]},
                "make observables.raw_ref name the source_events.source_ref it came from",
                "provenance resolves to the event, not only to a file",
                "changes what raw_ref holds for new rows; old rows keep the transcript path",
                "resolution outcome in the profile becomes resolved for new sessions",
                APPROVER[OBSERVABLE_DEFINITION],
                {"target": "observables.raw_ref", "from": "transcript path", "to": "source_ref",
                 "rollback": "write the transcript path again; resolution falls back to file level"}))
        if t["history"].get("insufficient"):
            out.append(Proposal(
                HISTORICAL_BASELINE, {"code": "INSUFFICIENT_HISTORY", "tool": tool,
                                      "sessions": t["history"]["insufficient"],
                                      "min_sessions_for_baseline": t["history"]["min_sessions_for_baseline"],
                                      "replay_id": rid},
                {"metrics": [], "questions": [], "cops": []},
                f"build a baseline for {tool} from VALIDATED outcomes only",
                "history-dependent metrics become judgeable for these operators",
                "no change to metric definitions; needs reviewer validation of earlier findings",
                "MemoryGateway.propose_baseline_update is admissible only when every supporting record is VALIDATED",
                APPROVER[HISTORICAL_BASELINE],
                {"target": f"baseline for {tool}", "from": "none", "to": "validated baseline version",
                 "rollback": "withdraw the baseline version; metrics return to INSUFFICIENT_HISTORY"}))
    for mid in profile.get("not_calculated", []):
        out.append(Proposal(
            METRIC_CALCULATION, {"code": "NOT_CALCULATED", "metric": mid, "replay_id": rid},
            {"metrics": [mid], "questions": _questions_using([mid]), "cops": []},
            f"define and build {mid} at the end of signal_metrics.py", f"{mid} can be assessed instead of NOT_CALCULATED",
            "additive metric; existing metric line numbers must not move",
            "a harness scenario with independent ground truth for the new metric",
            APPROVER[METRIC_CALCULATION],
            {"target": "signal_metrics.py", "from": "not built", "to": "new metric_version",
             "rollback": "pin the previous metric_version; replays under it are unchanged"}))
    limits = Counter()
    for sid, r in rep["results"].items():
        for qual in (r.get("runtime_monitoring") or {}).get("qualifications") or []:
            if "LIMIT_NOT_APPROVED" in qual:
                limits[qual.split(":")[0]] += 1
    for qid, n in sorted(limits.items()):
        out.append(Proposal(
            INTERPRETATION_RULE, {"code": "LIMIT_NOT_APPROVED", "question": qid, "sessions": n, "replay_id": rid},
            {"metrics": ["event_freshness_live", "missing_event_rate"], "questions": [f"runtime_monitoring:{qid}"],
             "cops": ["runtime_monitoring"]},
            "the owner sets evidence_limits values in metrics_config.json; no value is proposed here",
            f"{qid} can be answered instead of qualified", "config change under a new metric_version",
            "re-run the harness and this replay under the new version; compare with the pinned old version",
            APPROVER[INTERPRETATION_RULE],
            {"target": "metrics_config.json evidence_limits", "from": "absent", "to": "owner-approved values",
             "rollback": "remove evidence_limits; checks return to NOT_EXECUTED"}))
    return _unique(out)


def scenario_proposals(rep, covered_codes):
    """TEST_SCENARIO proposals for reason codes seen in a replay that no harness scenario reaches."""
    seen = Counter()
    for r in rep["results"].values():
        for qual in (r.get("runtime_monitoring") or {}).get("qualifications") or []:
            for item in qual.split(": ", 1)[-1].split("; "):
                detail = item.split(": ", 1)[-1]
                if detail.isupper() and " " not in detail:
                    seen[detail] += 1
    out = []
    for code, n in sorted(seen.items()):
        if code not in covered_codes:
            out.append(Proposal(
                TEST_SCENARIO, {"code": code, "sessions": n, "replay_id": rep["manifest"]["replay_id"]},
                {"metrics": [], "questions": [], "cops": ["runtime_monitoring"]},
                f"add a harness scenario whose rows produce {code}, with ground truth written first",
                "the real-data case is covered by an independent expectation",
                "test only", "the new scenario's expectations pass without changing existing ones",
                APPROVER[TEST_SCENARIO],
                {"target": "GROUND_TRUTH.yaml", "from": "no scenario", "to": "new scenario",
                 "rollback": "remove the scenario"}))
    return out


def _unique(props):
    seen, out = set(), []
    for p in props:
        if p.proposal_id not in seen:
            seen.add(p.proposal_id)
            out.append(p)
    return out
