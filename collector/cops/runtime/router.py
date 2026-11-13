"""
COPS activation router: from collected events to scheduled COP investigations.

    events (observables rows) ─► group by tenant+session into cases ─► dedupe events
      ─► CaseData (signal_metrics over the estate) ─► normalised evidence per metric
      ─► each COP's ActivationPolicy, evaluated independently: ELIGIBLE | NOT_ELIGIBLE | UNKNOWN
      ─► dedupe activations already made on the same evidence under the same mapping
      ─► schedule: eligible COPs in dependency waves (specs.CONSUMES); a wave runs concurrently
      ─► each COP through the existing LangGraph COP subgraph (graph.run_cop), or through the
         referral executor (referrals.run_investigation) when it holds consultation contracts
      ─► ActivationRecords: CANDIDATE, ELIGIBLE / NOT_ELIGIBLE / UNKNOWN, WAITING, ACTIVATED,
         COMPLETED, TIMED_OUT, SUPPRESSED; each linked to the decision record it produced

What the router never does
    - run a COP whose policy is not in an APPROVED mapping (MappingRegistry)
    - turn an UNDEFINED interpretation into a trigger (activation.validate_bundle)
    - bypass the COP graph: evidence verification, VS-01, triage and the authority gate run as
      they always do, in dry-run unless the runtime says otherwise
    - call a model it was not given, query a database, or act outside the sandbox

Determinism: the record sequence is a function of the events, the estate and the pinned
mapping version. Concurrent completions are logged in COP order, not arrival order, and
record ids exclude wall time, so a replay with the same version reproduces every record.
"""

import copy
import json
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from dataclasses import asdict
from datetime import datetime, timezone

from ..specs import topological_order
from .contracts import stable_id, digest
from .persistence import decision_id
from . import activation as A
from . import referrals as RF

ROUTER_VERSION = "cops-router-1"
MAX_PARALLEL = 4                       # concurrent COP runs per wave
MAX_RUNS = RF.MAX_RUNS                 # COP runs per case, the referral tree's budget
TIMEOUT_S = RF.TIMEOUT_S               # per COP run, the referral timeout


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


# ====================================================================== append-only log

class ActivationLog:
    """Immutable, hash-chained activation records. Optionally mirrored to SQLite with
    INSERT only: a record id can be written once and never updated."""

    COLUMNS = ("record_id", "seq", "case_id", "investigation_id", "cop", "transition", "mapping_version",
               "policy_id", "decision_id", "prev_record_id", "at", "body")

    def __init__(self, path=None, clock=_now):
        self.records, self.clock, self._lock = [], clock, threading.Lock()
        self.conn = None
        if path:
            self.conn = sqlite3.connect(path, check_same_thread=False)
            self.conn.execute("CREATE TABLE IF NOT EXISTS cop_activation_records ("
                              + ", ".join(f"{c} TEXT" + (" PRIMARY KEY" if c == "record_id" else "")
                                          for c in self.COLUMNS) + ")")
            self.conn.commit()

    def append(self, case_id, investigation_id, cop, transition, reasons, mapping_version, policy_id=None,
               evidence=(), decision_id=None, detail=()):
        assert transition in A.TRANSITIONS, transition
        with self._lock:
            mine = [r for r in self.records if r.investigation_id == investigation_id]
            prev = mine[-1].record_id if mine else None
            seq = len(mine) + 1
            body = dict(case_id=case_id, investigation_id=investigation_id, cop=cop, transition=transition,
                        reasons=tuple(reasons), mapping_version=mapping_version, policy_id=policy_id,
                        evidence=tuple(evidence), prev_record_id=prev, decision_id=decision_id,
                        detail=tuple(detail), seq=seq)
            rid = stable_id("activation", digest(body))
            rec = A.ActivationRecord(record_id=rid, at=self.clock(), **body)
            self.records.append(rec)
            if self.conn is not None:
                self.conn.execute(f"INSERT INTO cop_activation_records VALUES ({','.join('?' * len(self.COLUMNS))})",
                                  (rid, seq, case_id, investigation_id, cop, transition, mapping_version,
                                   policy_id, decision_id, prev, rec.at,
                                   json.dumps(asdict(rec), default=str)))
                self.conn.commit()
            return rec

    def of(self, investigation_id):
        return [r for r in self.records if r.investigation_id == investigation_id]

    def verify_chain(self, investigation_id):
        prev = None
        for r in self.of(investigation_id):
            if r.prev_record_id != prev:
                return False
            prev = r.record_id
        return True


# ====================================================================== grouping and evidence

def group_events(events):
    """Case-scoped groups: one case per tenant and session. Exact duplicate deliveries
    (same observable_id) are dropped and counted."""
    cases, seen, dup = {}, set(), 0
    for e in events:
        oid = e.get("observable_id")
        key = (e.get("tenant_id"), e.get("session_id"), oid)
        if oid and key in seen:
            dup += 1
            continue
        seen.add(key)
        cases.setdefault((e.get("tenant_id"), e["session_id"]), []).append(e)
    return cases, dup


def normalise(case):
    """{metric_id: {state, value, refs}} for the case session, from the metric layer."""
    ev = {e.metric_id: {"metric": e.metric_id, "state": e.state, "value": e.value,
                        "refs": list(e.evidence_refs[:6])} for e in case.case_events()}
    rows = [r for r in case.rows if r.get("session_id") == case.session_id]
    ev["__rows__"] = {"metric": "__rows__", "state": "NORMAL", "value": len(rows), "refs": []}
    return ev


def evaluate(policy, evidence):
    fn = A.RULE_KINDS[policy.rule_ref]["fn"]
    verdict, reasons = fn(policy, evidence)
    used = tuple(sorted({c.metric_id for c in policy.conditions} | set(policy.required_context)))
    snap = tuple({k: evidence[m][k] for k in ("metric", "state", "value", "refs")} if m in evidence
                 else {"metric": m, "state": "MISSING", "value": None, "refs": []} for m in used)
    return verdict, reasons, snap


def evidence_fingerprint(snap):
    return digest([(s["metric"], s["state"], s["value"]) for s in snap])


# ====================================================================== router

class Router:
    def __init__(self, registry, runtime_factory, log=None, version=None, max_parallel=MAX_PARALLEL,
                 max_runs=MAX_RUNS, timeout_s=TIMEOUT_S, run_hook=None):
        """runtime_factory(case_id, session_id, rows) -> graph.Runtime (rows include the estate).
        version: pin a mapping version (replay); default the registry's active version."""
        self.registry = registry
        self.bundle = registry.pinned(version) if version else registry.active()
        self.version = self.bundle.version
        self.factory, self.log = runtime_factory, log or ActivationLog()
        self.max_parallel, self.max_runs, self.timeout_s = max_parallel, max_runs, timeout_s
        self.run_hook = run_hook
        self.done = {}                     # dedupe key -> record_id of the activation it duplicates

    # ------------------------------------------------------------------ public
    def route(self, events, estate_rows=()):
        cases, dup = group_events(events)
        out = []
        for (tenant, sid), rows in sorted(cases.items(), key=lambda kv: (str(kv[0][0]), kv[0][1])):
            out.append(self._route_case(tenant, sid, rows, list(estate_rows), dup))
        return out

    # ------------------------------------------------------------------ one case
    def _route_case(self, tenant, sid, rows, estate, dup_events):
        rt = self.factory(f"case:{tenant}:{sid}", sid, estate + rows)
        case = rt.case
        n_prior = len([r for r in self.log.records if r.case_id == case.case_id])
        inv = stable_id(case.case_id, self.version, digest(sorted(r.get("observable_id") or "" for r in rows)),
                        n_prior)[:18]
        rt = copy.copy(rt)
        rt.run_id = f"route:{inv}"
        ev = normalise(case)
        L = lambda cop, tr, why, **k: self.log.append(case.case_id, inv, cop, tr, why, self.version, **k)  # noqa

        order = [c for c in topological_order() if self.bundle.policy(c)]
        verdicts = {}
        for cop in order:                                   # independent evaluation
            p = self.bundle.policy(cop)
            L(cop, A.CANDIDATE, (f"policy {p.policy_id} in mapping {self.version}",), policy_id=p.policy_id)
            v, why, snap = evaluate(p, ev)
            if v == A.UNKNOWN and p.missing_evidence == A.ACTIVATE_TO_ASSESS:
                why = why + (f"missing evidence: {A.ACTIVATE_TO_ASSESS}",)
                v = A.ELIGIBLE
            L(cop, v, why, policy_id=p.policy_id, evidence=snap,
              detail=(("missing_evidence", p.missing_evidence),))
            verdicts[cop] = (v, snap, p)

        eligible = []
        for cop in order:                                   # dedupe on the same evidence and mapping
            v, snap, p = verdicts[cop]
            if v != A.ELIGIBLE:
                continue
            key = (case.case_id, cop, self.version, evidence_fingerprint(snap))
            if key in self.done:
                L(cop, A.SUPPRESSED, (f"duplicate of activation {self.done[key]}: same evidence, same mapping",),
                  policy_id=p.policy_id, detail=(("duplicate_of", self.done[key]),))
                continue
            eligible.append(cop)

        results, published, runs = {}, {}, 0
        waves = self._waves(eligible)
        for w, wave in enumerate(waves):
            for cop in wave:
                p = self.bundle.policy(cop)
                deps = [d for d in p.depends_on if d in eligible]
                absent = [d for d in p.depends_on if d not in eligible]
                note = tuple(f"dependency {d} not activated in this case: consumed as absent" for d in absent)
                if deps:
                    L(cop, A.WAITING, (f"waiting on {', '.join(deps)}",) + note, policy_id=p.policy_id,
                      detail=(("waiting_on", tuple(deps)), ("wave", w)))
            runnable = []
            for cop in wave:
                p = self.bundle.policy(cop)
                if runs >= self.max_runs:
                    L(cop, A.SUPPRESSED, (f"run budget {self.max_runs} for this case is spent",), policy_id=p.policy_id)
                    continue
                failed_deps = [d for d in p.depends_on if d in eligible and d not in published]
                L(cop, A.ACTIVATED, (f"wave {w}; concurrent with {', '.join(c for c in wave if c != cop) or 'none'}",)
                  + tuple(f"dependency {d} produced no finding: consumed as absent" for d in failed_deps),
                  policy_id=p.policy_id, detail=(("wave", w), ("concurrent_with", tuple(c for c in wave if c != cop))))
                runnable.append(cop)
                runs += 1
            done = self._run_wave(rt, runnable, published)
            for cop in sorted(done):                        # deterministic log order
                p = self.bundle.policy(cop)
                status, out = done[cop]
                if status == "timeout":
                    L(cop, A.TIMED_OUT, (f"no answer within {self.timeout_s}s; a late answer is discarded",),
                      policy_id=p.policy_id)
                    continue
                if status == "error":
                    L(cop, A.SUPPRESSED, (f"run failed: {out}",), policy_id=p.policy_id)
                    continue
                res, pub, consult = out
                results[cop], published[cop] = res, pub
                if consult:                                  # consultation runs count against the case budget
                    runs += max(0, consult["runs"] - 1)
                key = (case.case_id, cop, self.version, evidence_fingerprint(verdicts[cop][1]))
                rec = L(cop, A.COMPLETED, (f"{res['finding']['label']} ({res['finding']['status']})",),
                        policy_id=p.policy_id, decision_id=res.get("_decision_id"),
                        detail=self._outcome(res, consult))
                self.done[key] = rec.record_id
        return {"case_id": case.case_id, "investigation_id": inv, "session_id": sid, "tenant_id": tenant,
                "mapping_version": self.version, "duplicate_events_dropped": dup_events,
                "records": [r for r in self.log.of(inv)], "results": results, "published": published,
                "runtime": rt}

    def _waves(self, eligible):
        left, waves, placed = list(eligible), [], set()
        while left:
            wave = [c for c in left if all(d in placed or d not in eligible
                                           for d in self.bundle.policy(c).depends_on)]
            if not wave:                                    # validate_bundle forbids this
                raise A.InvalidMapping([f"dependency cycle among {left}"])
            waves.append(sorted(wave))
            placed |= set(wave)
            left = [c for c in left if c not in wave]
        return waves

    def _run_one(self, rt, cop, upstream):
        if self.run_hook:
            self.run_hook(cop)
        allowed = self.bundle.consultation_map()
        if cop in allowed:
            inv = RF.run_investigation(rt, cop, upstream=upstream, consultations=allowed,
                                       max_depth=min(c.max_depth for c in self.bundle.consultations
                                                     if c.initiator == cop),
                                       timeout_s=self.timeout_s)
            out = inv["last_out"]
            consult = {"via": "referrals.run_investigation", "resolution": inv["investigation"]["resolution"],
                       "responses": [{k: r.get(k) for k in ("referral_id", "receiver", "status", "delivered_as")}
                                     for r in inv["investigation"].get("responses") or []],
                       "not_referred": [{k: n.get(k) for k in ("receiver", "reason")}
                                        for n in inv["investigation"].get("not_referred") or []],
                       "runs": inv["runs"], "spent_usd": inv["spent_usd"]}
            run_id = out["result"].get("run_id")
        else:
            from .graph import run_cop
            out = run_cop(rt, cop, upstream=upstream, thread_id=f"{rt.run_id}:{cop}")
            consult, run_id = None, out["result"].get("run_id")
        res = dict(out["result"])
        res["_decision_id"] = decision_id(run_id, rt.case.session_id, cop)
        return res, out["published"], consult

    def _run_wave(self, rt, cops, published):
        if not cops:
            return {}
        done = {}
        ups = {c: {d: published[d] for d in self.bundle.policy(c).depends_on if d in published} for c in cops}
        ex = ThreadPoolExecutor(max_workers=min(self.max_parallel, len(cops)))
        futs = {c: ex.submit(self._run_one, rt, c, ups[c]) for c in cops}
        deadline = time.monotonic() + self.timeout_s
        for c, f in futs.items():
            try:
                done[c] = ("ok", f.result(timeout=max(0.0, deadline - time.monotonic())))
            except FutureTimeout:
                done[c] = ("timeout", None)
            except Exception as e:                          # noqa: BLE001 - recorded, not hidden
                done[c] = ("error", f"{type(e).__name__}: {e}")
        ex.shutdown(wait=False, cancel_futures=True)         # a timed-out run finishes in the background, discarded
        return done

    @staticmethod
    def _outcome(res, consult):
        f = res.get("finding") or {}
        pa = res.get("proposed_action") or {}
        life = pa.get("lifecycle") or {}
        vs = f.get("verification_support") or {}
        return (("label", f.get("label")), ("status", f.get("status")), ("verified", f.get("verified")),
                ("vs01", vs.get("decision")), ("selected_action", pa.get("typed_action")),
                ("authority_stage", life.get("stage")), ("execution_status", pa.get("execution_status")),
                ("finding_id", f.get("finding_id")), ("run_id", res.get("run_id")),
                ("consultation", json.dumps(consult, sort_keys=True, default=str) if consult else None))


def records_json(records):
    return [{**asdict(r), "reasons": list(r.reasons), "evidence": list(r.evidence), "detail": dict(r.detail)}
            for r in records]
