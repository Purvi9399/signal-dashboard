"""
EvidenceAvailabilityService

Compiles the epistemic situation for one session. Factual only: what exists,
what does not, whether the event happened anyway, and which source was supposed
to supply the missing part.

It never says whether something is dangerous, a violation, or acceptable. That
reasoning belongs to a COP. The service answers "delegation occurred, the
destination lane is known, the parent initiator cannot be reconstructed"; the
COP decides what that means.
"""

from dataclasses import dataclass, field as dc_field, asdict

from . import checkpoint as C
from .evidence_map import (EVENT_DATA_MAP, METRIC_NEEDS, BLOCKING, DEGRADING,
                          IMMATERIAL, materiality)

DECIDED, DEGRADED, BLOCKED = "DECIDED", "DEGRADED", "BLOCKED_BY_EVIDENCE"


@dataclass
class Envelope:
    session_id: str
    requested_by: str
    metrics: tuple = ()
    available: tuple = ()
    missing: tuple = ()
    event_evidence: tuple = ()      # events known to have occurred, detail missing
    collector_status: tuple = ()
    materiality: dict = dc_field(default_factory=dict)
    gaps: tuple = ()                # one record per missing field

    @property
    def blocking(self):
        return tuple(f for f in self.missing
                     if self.materiality.get(f) == BLOCKING)

    @property
    def decision_status(self):
        """Derived, not chosen. Same rule for every COP, which is why the
        interpreters do not need to compute it."""
        if self.blocking:
            return BLOCKED
        if any(self.materiality.get(f) == DEGRADING for f in self.missing):
            return DEGRADED
        return DECIDED

    @property
    def confidence_factor(self):
        """What the evidence situation does to a COP's own confidence."""
        if self.decision_status == BLOCKED:
            return 0.0
        if self.decision_status == DEGRADED:
            return max(0.4, 1.0 - 0.2 * len(self.missing))
        return 1.0

    def for_cop(self):
        """What a COP receives. Deliberately small."""
        return {
            "status": self.decision_status,
            "available": list(self.available),
            "missing": list(self.missing),
            "blocking": list(self.blocking),
            "known_events_with_missing_detail": [
                e["event"] for e in self.event_evidence if e.get("occurred")],
            "confidence_factor": round(self.confidence_factor, 2),
        }

    def as_dict(self):
        d = asdict(self)
        d.update({"decision_status": self.decision_status,
                  "blocking": list(self.blocking)})
        return d


class EvidenceAvailabilityService:
    def __init__(self, probe, occurred):
        """probe(source, field) -> stage dict; occurred(event) -> bool|basis"""
        self.probe = probe
        self.occurred = occurred

    def compile(self, session_id, cop, metric_ids):
        available, missing, evidence, status, mat, gaps = [], [], [], [], {}, []

        for metric_id in metric_ids:
            spec = METRIC_NEEDS.get(metric_id)
            if not spec:
                continue
            for event_type in spec["events"]:
                ev = EVENT_DATA_MAP[event_type]
                happened = self.occurred(event_type)
                for fieldname in ev["required"] + ev.get("optional", []):
                    if fieldname in available or fieldname in missing:
                        continue
                    if not happened:
                        # nothing to collect, and this is not a gap
                        continue
                    cps = C.resolve(session_id, fieldname, event_type,
                                    ev["collectors"], self.probe)
                    summary = C.summarise(cps)
                    status.append({"field": fieldname, **summary})
                    if summary["resolved"]:
                        available.append(fieldname)
                    else:
                        missing.append(fieldname)
                        m = materiality(metric_id, fieldname, cop)
                        mat[fieldname] = _worst(mat.get(fieldname), m)
                        evidence.append({
                            "event": event_type, "occurred": True,
                            "detail_missing": fieldname,
                            "basis": ev["occurrence_basis"],
                        })
                        gaps.append({
                            "observable": fieldname, "event_type": event_type,
                            "metric_id": metric_id, "cop": cop,
                            "expected_collectors": ev["collectors"],
                            "observed_collectors": [c["collector"] for c in summary["misses"]],
                            "gap_type": summary["primary_verdict"],
                            "per_source": {m["collector"]: m["verdict"]
                                           for m in summary["misses"]},
                            "reason_required": spec["why"],
                            "event_known_to_have_occurred": True,
                            "recurrence_key": f"{fieldname}:{summary['primary_source']}:"
                                              f"{summary['primary_verdict']}",
                        })

        return Envelope(session_id, cop, tuple(metric_ids), tuple(available),
                        tuple(missing), tuple(evidence), tuple(status), mat,
                        tuple(gaps))


_RANK = {IMMATERIAL: 0, DEGRADING: 1, BLOCKING: 2}

def _worst(a, b):
    """A field needed by two metrics takes the stricter reading."""
    if a is None:
        return b
    return a if _RANK[a] >= _RANK[b] else b
