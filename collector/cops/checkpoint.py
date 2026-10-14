"""
Where evidence disappeared.

A checkpoint inspects the collection pipeline, never the governance
conclusion. For one field from one source it walks the stages in order and
records the first that failed, which is more useful than "file_path = UNKNOWN"
because it names what to fix and who should fix it.

The stages fail differently and have different owners:

    expected            should this source supply this field at all
    collector_alive     was the collector running
    emitted             did the source produce it
    ingested            did it reach us
    normalised          did our extractor read it
    session_linked      can it be attached to the session
    usable_by_metrics   is it in a form a metric can read
"""

from dataclasses import dataclass, field as dc_field, asdict
from typing import Optional
import hashlib

STAGES = ("expected", "collector_alive", "emitted", "ingested",
          "normalised", "session_linked", "usable_by_metrics")

# Verdicts. EVENT_DID_NOT_HAPPEN and COLLECTION_FAILURE must never be conflated:
# one is a fact about the world, the other a fact about us.
OK = "OK"
NOT_EXPECTED = "NOT_EXPECTED"                      # this source never supplies this
EVENT_DID_NOT_HAPPEN = "EVENT_DID_NOT_HAPPEN"      # nothing to collect
COLLECTION_FAILURE = "COLLECTION_FAILURE"          # should have arrived, did not
LINKAGE_FAILURE = "LINKAGE_FAILURE"                # arrived, cannot be attached
CAPABILITY_GAP = "COLLECTION_CAPABILITY_GAP"       # nobody was ever designed to collect it

# Which stage failing produces which verdict.
VERDICT_AT = {
    "expected": NOT_EXPECTED,
    "collector_alive": COLLECTION_FAILURE,
    "emitted": COLLECTION_FAILURE,
    "ingested": COLLECTION_FAILURE,
    "normalised": COLLECTION_FAILURE,
    "session_linked": LINKAGE_FAILURE,
    "usable_by_metrics": LINKAGE_FAILURE,
}


@dataclass
class Checkpoint:
    session_id: str
    observable: str
    event_type: str
    collector: str
    stages: dict = dc_field(default_factory=dict)
    detail: str = ""
    event_occurred: bool = True

    @property
    def checkpoint_id(self):
        key = f"{self.session_id}|{self.observable}|{self.collector}"
        return "ckpt_" + hashlib.sha1(key.encode()).hexdigest()[:16]

    @property
    def failed_stage(self):
        for s in STAGES:
            v = self.stages.get(s)
            if v is False:
                return s
            if v is None:          # not reached, because an earlier stage failed
                return None
        return None

    @property
    def verdict(self):
        if not self.event_occurred:
            return EVENT_DID_NOT_HAPPEN
        bad = self.failed_stage
        if bad is None:
            return OK if all(self.stages.get(s) for s in STAGES) else CAPABILITY_GAP
        return VERDICT_AT[bad]

    @property
    def recovered_here(self):
        return self.verdict == OK

    def as_row(self):
        d = asdict(self)
        d.pop("stages")
        d.update({"checkpoint_id": self.checkpoint_id, "verdict": self.verdict,
                  "failed_stage": self.failed_stage,
                  **{s: self.stages.get(s) for s in STAGES}})
        return d


def resolve(session_id, observable, event_type, sources, probe, event_occurred=True):
    """Walk the precedence order until one source yields the field.

    Returns every checkpoint, not only the one that worked. Governance
    continues on whatever was recovered; collection learns from each miss, and
    both facts are needed at once.
    """
    out = []
    for src in sources:
        cp = Checkpoint(session_id, observable, event_type, src,
                        probe(src, observable), event_occurred=event_occurred)
        out.append(cp)
        if cp.recovered_here:
            break
    return out


def summarise(checkpoints):
    recovered = next((c for c in checkpoints if c.recovered_here), None)
    missed = [c for c in checkpoints if not c.recovered_here]
    return {
        "resolved": recovered is not None,
        "resolved_by": recovered.collector if recovered else None,
        "fallback_used": bool(recovered and checkpoints.index(recovered) > 0),
        "primary_source": checkpoints[0].collector if checkpoints else None,
        "primary_verdict": checkpoints[0].verdict if checkpoints else None,
        "misses": [{"collector": c.collector, "verdict": c.verdict,
                    "failed_stage": c.failed_stage, "detail": c.detail} for c in missed],
    }
