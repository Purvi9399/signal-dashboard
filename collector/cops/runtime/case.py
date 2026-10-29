"""
CaseData: everything one case may draw on, gathered once.

A case is one session under review, inside the estate it belongs to. The
metric layer (signal_metrics.compute) is run over the estate's observables,
so baselines and novelty are computed exactly as in production; the case
graph then reads only the case session's metric events.

artifacts holds authoritative sources the repository does not have yet
(policy store, taxonomy, risk register, task plan, context snapshots,
recovery records, agent registry, quotas). When an artifact is absent, the
tools that need it return a typed missing dependency rather than a guess.
In the evaluation harness these artifacts are synthetic and labelled so.
"""

import os, sys
from dataclasses import dataclass, field
from typing import Optional

HERE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import signal_metrics                                    # noqa: E402

from ..models import MetricEvent                         # noqa: E402


@dataclass
class CaseData:
    case_id: str
    tenant_id: str
    session_id: str
    rows: list                                    # estate observables, case included
    events: list = field(default_factory=list)    # metric_event dicts for every session
    sessions: list = field(default_factory=list)  # signal_metrics.Session objects
    artifacts: dict = field(default_factory=dict)
    decision_store: Optional[object] = None       # prior decisions, for aggregate COPs
    computed_at: str = "2026-10-29T00:00:00Z"
    cfg: Optional[dict] = None
    synthetic: bool = False

    def __post_init__(self):
        self.cfg = self.cfg or signal_metrics.load_config()
        if not self.events and self.rows:
            self.events, self.sessions = signal_metrics.compute(
                self.rows, self.cfg, computed_at=self.computed_at)
        self._by_id = {s.id: s for s in self.sessions}

    @property
    def session(self):
        return self._by_id.get(self.session_id)

    @property
    def agent_scope(self):
        s = self.session
        return (s.operator if s and s.operator else "*")

    def case_events(self, metric_ids=None):
        return [MetricEvent.from_row(e) for e in self.events
                if e["session_id"] == self.session_id
                and (metric_ids is None or e["metric_id"] in metric_ids)]

    def other_sessions(self):
        s = self.session
        return [x for x in self.sessions if x.id != self.session_id
                and (s is None or x.tool == s.tool)]

    def earlier_sessions(self):
        s = self.session
        start = (s.start or "") if s else ""
        return [x for x in self.other_sessions() if (x.start or "") < start]

    def metric_history(self, metric_id):
        """This metric's value in every other session of the same tool."""
        ids = {x.id for x in self.other_sessions()}
        return [e for e in self.events if e["metric_id"] == metric_id and e["session_id"] in ids]

    def artifact(self, name):
        return self.artifacts.get(name)
