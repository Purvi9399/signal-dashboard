"""
COPS runner

One runner executes every spec the same way:

    read the spec's metrics             (precomputed; nothing calculated here)
    nothing off and nothing upstream?   record the quiet reading, skip the interpreter
    interpret through the question
    asked for context?                  one narrow query, then interpret once more
    clamp to the spec's permitted actions and the run mode
    record a COPDecision with timing, inputs and any failure

Runtime Monitoring runs first and its evidence level is handed to every
COP after it, so a finding on thin evidence carries lower confidence.
"""

import time, uuid
from datetime import datetime, timezone

from .models import COPDecision, ATTENTION, INTERVENING
from .specs import BY_KEY, ORDER, ACTIVE

QUIET_EVIDENCE = 0.9


class MemoryStore:
    def __init__(self):
        self.rows = []
    def save(self, decisions):
        self.rows += [d.row() for d in decisions]


class SupabaseStore:
    def save(self, decisions):
        import signal_traces
        signal_traces.post("cop_decisions", [d.row() for d in decisions], "decision_id")


class COPRunner:
    def __init__(self, reader, interpreter, store=None, active=ACTIVE, mode="dry_run", run_id=None):
        if mode != "dry_run":
            raise ValueError("only dry_run exists: no action layer is wired to the agents yet")
        self.reader, self.interp, self.store = reader, interpreter, store or MemoryStore()
        self.active = [k for k in ORDER if k in active]
        self.mode = mode
        self.run_id = run_id or uuid.uuid4().hex[:12]

    def run_session(self, session_id):
        done = {}
        for key in self.active:
            done[key] = self.run_cop(BY_KEY[key], session_id, done)
        self.store.save(list(done.values()))
        return done

    def run_cop(self, spec, sid, done):
        t0 = time.perf_counter()
        d = COPDecision(cop=spec.key, cop_version=spec.version, session_id=sid, run_id=self.run_id,
                        behavioural_question=spec.behavioural_question, interpreter=self.interp.name,
                        mode=self.mode, decided_at=datetime.now(timezone.utc).isoformat())
        try:
            events, missing = self.reader.read_metrics(sid, spec.metric_keys)
            upstream = {k: done[k] for k in spec.consumes_decisions_from if k in done}
            rm = done.get("runtime_monitoring")
            evid = rm.evidence_confidence if rm and rm.evidence_confidence is not None else QUIET_EVIDENCE
            d.metrics_read = [{"metric": e.metric_id, "value": e.value, "state": e.state} for e in events]
            d.metrics_read += [{"metric": k, "value": None, "state": "NOT_COMPUTED"} for k in missing]
            d.upstream_decisions = [u.decision_id for u in upstream.values()]
            d.evidence_confidence = evid

            needs = any(e.state in ATTENTION for e in events) \
                or any(u.action != "NONE" for u in upstream.values()) \
                or (spec.key == "runtime_monitoring" and any(e.state == "UNKNOWN" for e in events))
            if not needs and self.interp.costly:
                out = {"interpretation": spec.interpretations[0], "confidence": evid, "severity": "NONE",
                       "action": "NONE", "action_reason": "", "downstream": [], "request_context": [],
                       "rationale": "every metric normal and nothing passed from upstream",
                       "evidence_confidence": QUIET_EVIDENCE if spec.key == "runtime_monitoring" else None}
                d.skipped_interpreter = True
            else:
                out = self.interp.interpret(spec, events, upstream, None, evid)
                if out.get("request_context"):
                    ctx, log = self.reader.read_context(
                        sid, out.get("trace_id"), out["request_context"],
                        out.get("evidence_refs", ()), allowed=spec.context_keys)
                    d.context_used = log
                    out = self.interp.interpret(spec, events, upstream, ctx, evid)

            action = out["action"]
            if action not in spec.permitted_actions:
                raise ValueError(f"{spec.key} may not {action}")
            if action == "REQUEST_CONTEXT":          # asked twice: context did not settle it
                action, out["action_reason"] = "WATCH", "context did not settle the reading"
            if action in INTERVENING:
                out["action_reason"] = f"[{action} recommended, not executed: {self.mode}] " + out["action_reason"]

            d.interpretation = out["interpretation"]
            d.rationale = out.get("rationale", "")
            d.confidence = out["confidence"]
            d.severity = out.get("severity", "NONE")
            d.action = action
            d.action_reason = out.get("action_reason", "")
            d.downstream_cops = [c for c in out.get("downstream", []) if c in spec.downstream_cops]
            d.metric_id, d.trace_id = out.get("metric_id"), out.get("trace_id")
            d.evidence_refs = out.get("evidence_refs", [])
            if out.get("evidence_confidence") is not None:
                d.evidence_confidence = out["evidence_confidence"]
        except Exception as e:                       # a failed COP is a recorded fact
            d.failed, d.error = True, f"{type(e).__name__}: {e}"[:300]
            d.interpretation, d.action = "ERROR", "NONE"
        d.evaluation_ms = int((time.perf_counter() - t0) * 1000)
        return d
