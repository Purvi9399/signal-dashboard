"""
Outcome and reviewer feedback that outlives an investigation.

Two kinds of entry, one table (cop_outcome_feedback), in the same SQLite file
as the decisions and memory (persistence.SQLiteStore), so they survive a
restart and are read by later, separate investigations:

    execution_outcome   what the gateway observed: VERIFIED, FAILED,
                        APPROVAL_DENIED, RELEASED, CANCELLED. It says the action
                        happened (or not); it says nothing about whether the
                        finding was right. Never eligible for precedent or scoring.
    review              a named, independent reviewer's verdict on the finding:
                        CONFIRMED or REFUTED. Eligible for precedent and scoring
                        only when the reviewer is independent
                        (memory.independent_reviewer) and the finding verified.
                        An eligible review is applied to the COP's memory record
                        through MemoryGateway.record_feedback, the only route to
                        VALIDATED / REJECTED.

scoring_history() returns eligible entries only. Nothing here changes a
threshold or a weight: a calibration would read this history and still need a
person to approve the change.

Entry ids are content ids (stable_id of case, finding, kind, source, outcome),
and an entry is inserted only if absent, so re-running a case can never erase
or rewrite earlier feedback.
"""

import json
from datetime import datetime, timezone

from .contracts import stable_id, VALIDATED, REJECTED_RECORD
from .memory import independent_reviewer, MemoryAccessDenied

LEDGER_VERSION = "outcomes-1"
COLUMNS = ("feedback_id", "kind", "case_id", "tenant_id", "cop", "finding_id", "decision_id", "memory_record_id",
           "action_id", "investigation_id", "outcome", "source", "source_kind", "independent",
           "eligible_for_precedent", "eligible_for_scoring", "applied_to_memory", "note", "at", "detail")
REVIEW_OUTCOMES = ("CONFIRMED", "REFUTED")
EXECUTION_OUTCOMES = ("VERIFIED", "FAILED", "APPROVAL_DENIED", "RELEASED", "CANCELLED", "REFUSED",
                      "AWAITING_APPROVAL", "NOT_EXECUTED")


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class OutcomeLedger:
    def __init__(self, store, memory=None, clock=_now):
        """store: persistence.SQLiteStore (its connection and lock are reused)."""
        self.store, self.memory, self.clock = store, memory, clock
        with store.lock:
            store.conn.execute(f"CREATE TABLE IF NOT EXISTS cop_outcome_feedback ("
                               + ", ".join(f"{c} TEXT" + (" PRIMARY KEY" if c == "feedback_id" else "")
                                           for c in COLUMNS) + ")")
            store.conn.commit()

    def _insert_once(self, row):
        with self.store.lock:
            cur = self.store.conn.execute(
                f"INSERT OR IGNORE INTO cop_outcome_feedback ({', '.join(COLUMNS)}) VALUES "
                f"({', '.join('?' * len(COLUMNS))})",
                [json.dumps(row.get(c)) if isinstance(row.get(c), (dict, list, bool)) else row.get(c)
                 for c in COLUMNS])
            self.store.conn.commit()
            return cur.rowcount == 1

    def entries(self, **eq):
        where = " AND ".join(f"{k} = ?" for k in eq)
        with self.store.lock:
            cur = self.store.conn.execute(f"SELECT {', '.join(COLUMNS)} FROM cop_outcome_feedback"
                                          + (f" WHERE {where}" if where else "") + " ORDER BY at",
                                          tuple(eq.values()))
            rows = cur.fetchall()
        out = []
        for v in rows:
            d = dict(zip(COLUMNS, v))
            for k in ("independent", "eligible_for_precedent", "eligible_for_scoring", "applied_to_memory",
                      "detail"):
                if d.get(k) is not None:
                    d[k] = json.loads(d[k])
            out.append(d)
        return out

    # ---------------------------------------------------------- execution outcomes
    def record_execution(self, case_id, tenant_id, cop, finding_id, decision_id, action_id, outcome,
                         investigation_id=None, detail=None):
        assert outcome in EXECUTION_OUTCOMES, outcome
        row = {"feedback_id": stable_id(case_id, finding_id, action_id, "execution", outcome),
               "kind": "execution_outcome", "case_id": case_id, "tenant_id": tenant_id, "cop": cop,
               "finding_id": finding_id, "decision_id": decision_id, "memory_record_id": None,
               "action_id": action_id, "investigation_id": investigation_id, "outcome": outcome,
               "source": "gateway", "source_kind": "gateway", "independent": False,
               "eligible_for_precedent": False, "eligible_for_scoring": False, "applied_to_memory": False,
               "note": "the gateway observed the action; this does not say the finding was right",
               "at": self.clock(), "detail": detail or {}}
        new = self._insert_once(row)
        return {**row, "inserted": new}

    # ---------------------------------------------------------- reviewer feedback
    def record_review(self, case_id, tenant_id, cop, finding_id, decision_id, outcome, reviewer,
                      finding_verified, memory_record_id=None, investigation_id=None, note=""):
        """A reviewer's verdict on a finding. Eligible only if the reviewer is independent
        and the finding verified; an eligible verdict is applied to the memory record."""
        assert outcome in REVIEW_OUTCOMES, outcome
        indep = independent_reviewer(reviewer)
        eligible = indep and bool(finding_verified)
        why = [] if eligible else (["the reviewer is not independent of the finding"] if not indep else []) + \
            (["the finding did not verify: there is nothing independent to confirm"] if not finding_verified else [])
        applied, mem_error = False, None
        if eligible and memory_record_id and self.memory is not None:
            try:
                self.memory.record_feedback(memory_record_id, tenant_id,
                                            VALIDATED if outcome == "CONFIRMED" else REJECTED_RECORD, reviewer, note)
                applied = True
            except (MemoryAccessDenied, KeyError, ValueError) as e:
                mem_error = f"{type(e).__name__}: {e}"
        row = {"feedback_id": stable_id(case_id, finding_id, "review", reviewer, outcome),
               "kind": "review", "case_id": case_id, "tenant_id": tenant_id, "cop": cop, "finding_id": finding_id,
               "decision_id": decision_id, "memory_record_id": memory_record_id, "action_id": None,
               "investigation_id": investigation_id, "outcome": outcome, "source": reviewer,
               "source_kind": "human" if indep else "not_independent", "independent": indep,
               "eligible_for_precedent": eligible and applied, "eligible_for_scoring": eligible,
               "applied_to_memory": applied, "note": note[:300], "at": self.clock(),
               "detail": {"ineligible_because": why, "memory_error": mem_error}}
        new = self._insert_once(row)
        return {**row, "inserted": new}

    # ---------------------------------------------------------- reads
    def scoring_history(self, cop=None, tenant_id=None):
        """Independently validated reviews only: what a calibration may learn from."""
        rows = [r for r in self.entries(kind="review") if r["eligible_for_scoring"]]
        return [r for r in rows if (cop is None or r["cop"] == cop) and (tenant_id is None or r["tenant_id"] == tenant_id)]

    def for_case(self, case_id):
        return self.entries(case_id=case_id)
