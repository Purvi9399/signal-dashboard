"""
Persistent COP memory: one gateway, one store, eighteen policies.

Three layers, kept apart:
    working state    the current investigation, in LangGraph state
    checkpoints      execution continuation, in the LangGraph checkpointer
    memory           what a COP may know next time, here

Every read and write goes through MemoryGateway, which enforces:
    the COP may only read the record types its policy lists,
    the COP may only write its own record types,
    a record belongs to one tenant and is never returned to another,
    an expired record is never returned,
    every denial is written to the access log.

Validation lifecycle (the feedback loop):

    observation          UNVALIDATED  written by a COP whose finding did not verify
    verified finding     VERIFIED     written after blocking verifiers passed
    outcome / reviewer   VALIDATED    set only by record_feedback(), with who and why
                         REJECTED     the same, when the outcome contradicts it
    expiry               EXPIRED      past expires_at; kept, never returned

Only VALIDATED records count as precedent. VERIFIED and UNVALIDATED records
are returned in separate buckets so a COP can see them without treating them
as established. Nothing here changes a threshold, a policy or a baseline:
propose_baseline_update() returns a proposal that needs a person.
"""

from datetime import datetime, timedelta, timezone

from .contracts import (MemoryRecord, UNVALIDATED, VALIDATED, REJECTED_RECORD, EXPIRED,
                        stable_id)

VERIFIED = "VERIFIED"

# Initial retention defaults in days. Configurable per deployment; they are
# not derived from any retention policy document, because none exists in
# this repository yet.
DEFAULT_RETENTION_DAYS = 90

# reads: record types the COP may retrieve. writes: types it owns.
# Two fixes against the audited branch are deliberate here:
#   anomaly_detection can read anomaly_observation, the type it writes;
#   behavior_analytics can read context_integrity_finding from Context & Memory.
POLICIES = {
    "runtime_monitoring":          {"writes": ("collector_reliability",),
                                    "reads": ("collector_reliability",), "scope": "tenant"},
    "interaction_discovery":       {"writes": ("route_record",),
                                    "reads": ("route_record",), "scope": "agent"},
    "agent_lifecycle_management":  {"writes": ("agent_profile",),
                                    "reads": ("agent_profile", "route_record"), "scope": "agent"},
    "context_memory":              {"writes": ("context_integrity_finding",),
                                    "reads": ("context_integrity_finding",), "scope": "agent"},
    "workflow_orchestration":      {"writes": ("workflow_failure",),
                                    "reads": ("workflow_failure", "context_integrity_finding"),
                                    "scope": "agent"},
    "behavior_analytics":          {"writes": ("behaviour_baseline",),
                                    "reads": ("behaviour_baseline", "context_integrity_finding",
                                              "workflow_failure"), "scope": "agent"},
    "anomaly_detection":           {"writes": ("anomaly_observation",),
                                    "reads": ("anomaly_observation", "behaviour_baseline"),
                                    "scope": "agent"},
    "resource_management":         {"writes": ("usage_baseline",),
                                    "reads": ("usage_baseline",), "scope": "agent"},
    "workload_optimization":       {"writes": ("optimisation_outcome",),
                                    "reads": ("optimisation_outcome", "usage_baseline"),
                                    "scope": "tenant"},
    "policy_enforcement":          {"writes": ("enforcement_record",),
                                    "reads": ("enforcement_record", "route_record"), "scope": "agent"},
    "classification_tagging":      {"writes": ("classification_example",),
                                    "reads": ("classification_example",), "scope": "tenant"},
    "recovery_resilience":         {"writes": ("recovery_outcome",),
                                    "reads": ("recovery_outcome",), "scope": "tenant"},
    "incident_response":           {"writes": ("incident_outcome",),
                                    "reads": ("incident_outcome", "anomaly_observation",
                                              "enforcement_record", "recovery_outcome"),
                                    "scope": "tenant"},
    "risk_mitigation":             {"writes": ("mitigation_outcome",),
                                    "reads": ("mitigation_outcome", "incident_outcome"),
                                    "scope": "tenant"},
    "audit_traceability":          {"writes": ("trace_gap",),
                                    "reads": ("trace_gap",), "scope": "tenant"},
    "governance_intelligence":     {"writes": ("governance_review",),
                                    "reads": ("governance_review", "incident_outcome",
                                              "classification_example"), "scope": "tenant"},
    "knowledge_policy_management": {"writes": ("policy_conflict",),
                                    "reads": ("policy_conflict",), "scope": "tenant"},
    "governance_architecture":     {"writes": ("coverage_gap",),
                                    "reads": ("coverage_gap", "governance_review"), "scope": "tenant"},
}


class MemoryAccessDenied(PermissionError):
    pass


def _now():
    return datetime.now(timezone.utc)


class MemoryGateway:
    def __init__(self, store, clock=_now, retention_days=None):
        """store: persistence.SQLiteStore, or any object with upsert/select."""
        self.store = store
        self.clock = clock
        self.retention = dict(retention_days or {})

    # ---------------------------------------------------------- log
    def _log(self, cop, case_id, tenant_id, op, rtype, rid, outcome, reason=""):
        at = self.clock().isoformat()
        self.store.upsert("cop_memory_access_log", [{
            "access_id": stable_id(at, cop, case_id, op, rtype, rid, outcome, reason),
            "at": at, "cop": cop, "case_id": case_id, "tenant_id": tenant_id,
            "operation": op, "record_type": rtype, "record_id": rid,
            "outcome": outcome, "reason": reason}])

    def access_log(self, tenant_id=None, outcome=None):
        rows = self.store.select("cop_memory_access_log")
        return [r for r in rows if (tenant_id is None or r["tenant_id"] == tenant_id)
                and (outcome is None or r["outcome"] == outcome)]

    # ---------------------------------------------------------- write
    def write(self, cop, case_id, tenant_id, record_type, payload, provenance,
              agent_scope="*", status=UNVALIDATED):
        pol = POLICIES.get(cop)
        if pol is None or record_type not in pol["writes"]:
            self._log(cop, case_id, tenant_id, "write", record_type, None, "DENIED",
                      f"{cop} does not own record type {record_type}")
            raise MemoryAccessDenied(f"{cop} may not write {record_type}")
        if status not in (UNVALIDATED, VERIFIED):
            # VALIDATED and REJECTED come only from feedback, never from the COP itself
            self._log(cop, case_id, tenant_id, "write", record_type, None, "DENIED",
                      f"a COP cannot write status {status}")
            raise MemoryAccessDenied(f"{cop} cannot self-validate a record")
        if not provenance.get("case_id") or not tenant_id:
            self._log(cop, case_id, tenant_id, "write", record_type, None, "DENIED",
                      "record without tenant or provenance")
            raise MemoryAccessDenied("memory records need a tenant and a case")
        now = self.clock()
        days = self.retention.get(record_type, DEFAULT_RETENTION_DAYS)
        rec = MemoryRecord(tenant_id=tenant_id, agent_scope=agent_scope, owner_cop=cop,
                           record_type=record_type, payload=payload, provenance=provenance,
                           validation_status=status, created_at=now.isoformat(),
                           expires_at=(now + timedelta(days=days)).isoformat())
        self.store.upsert("cop_memory_records", [rec.to_dict()])
        self._log(cop, case_id, tenant_id, "write", record_type, rec.record_id, "OK", status)
        return rec.to_dict()

    # ---------------------------------------------------------- feedback
    def record_feedback(self, record_id, tenant_id, outcome, reviewer, note=""):
        """Outcome or reviewer feedback: the only way a record becomes VALIDATED."""
        if outcome not in (VALIDATED, REJECTED_RECORD):
            raise ValueError("feedback outcome is VALIDATED or REJECTED")
        if not reviewer:
            raise ValueError("feedback needs a named reviewer or outcome source")
        rows = [r for r in self.store.select("cop_memory_records")
                if r["record_id"] == record_id]
        if not rows:
            raise KeyError(record_id)
        r = rows[0]
        if r["tenant_id"] != tenant_id:
            self._log("feedback", None, tenant_id, "feedback", r["record_type"], record_id,
                      "DENIED", "cross-tenant feedback")
            raise MemoryAccessDenied("cross-tenant feedback")
        if outcome == VALIDATED and r["validation_status"] == UNVALIDATED \
                and not (r["provenance"] or {}).get("allow_promote_unverified"):
            # An unverified observation is not promoted by a reviewer click alone:
            # it has to be re-investigated and verified first.
            self._log("feedback", None, tenant_id, "feedback", r["record_type"], record_id,
                      "DENIED", "unverified observation cannot be validated directly")
            raise MemoryAccessDenied("validate a verified finding, not a raw observation")
        r["validation_status"] = outcome
        r["provenance"] = {**(r["provenance"] or {}),
                           "feedback": {"outcome": outcome, "reviewer": reviewer, "note": note,
                                        "at": self.clock().isoformat()}}
        self.store.upsert("cop_memory_records", [r])
        self._log("feedback", None, tenant_id, "feedback", r["record_type"], record_id, "OK", outcome)
        return r

    # ---------------------------------------------------------- read
    def read(self, cop, case_id, tenant_id, record_types=None, agent_scope=None,
             requested_tenant=None):
        """Records this COP may see, in three buckets by standing."""
        pol = POLICIES.get(cop)
        want = tuple(record_types or (pol["reads"] if pol else ()))
        if requested_tenant is not None and requested_tenant != tenant_id:
            self._log(cop, case_id, tenant_id, "read", ",".join(want), None, "DENIED",
                      f"cross-tenant request for {requested_tenant}")
            raise MemoryAccessDenied(f"{cop} in tenant {tenant_id} asked for tenant {requested_tenant}")
        bad = [t for t in want if not pol or t not in pol["reads"]]
        if bad:
            self._log(cop, case_id, tenant_id, "read", ",".join(bad), None, "DENIED",
                      "record type not in this COP's read policy")
            raise MemoryAccessDenied(f"{cop} may not read {bad}")
        now = self.clock().isoformat()
        out = {"established": [], "verified_pending": [], "unverified": [], "excluded": 0}
        for r in self.store.select("cop_memory_records"):
            if r["record_type"] not in want:
                continue
            if r["tenant_id"] != tenant_id:
                continue                       # never visible, not even as a count
            if agent_scope and pol["scope"] == "agent" and r["agent_scope"] not in ("*", agent_scope):
                continue
            if r["expires_at"] and r["expires_at"] < now:
                out["excluded"] += 1
                continue
            s = r["validation_status"]
            if s == VALIDATED:
                out["established"].append(r)
            elif s == VERIFIED:
                out["verified_pending"].append(r)
            elif s == UNVALIDATED:
                out["unverified"].append(r)
            else:
                out["excluded"] += 1
        for k in ("established", "verified_pending", "unverified"):
            out[k].sort(key=lambda r: r["created_at"])
        self._log(cop, case_id, tenant_id, "read", ",".join(want), None, "OK",
                  f"{len(out['established'])} established, {len(out['verified_pending'])} pending, "
                  f"{len(out['unverified'])} unverified")
        return out

    def get(self, record_id, tenant_id, cop, case_id):
        """Fetch one record by id, under the same tenant and type rules."""
        rows = [r for r in self.store.select("cop_memory_records") if r["record_id"] == record_id]
        if not rows:
            return None
        r = rows[0]
        if r["tenant_id"] != tenant_id:
            self._log(cop, case_id, tenant_id, "get", r["record_type"], record_id, "DENIED",
                      f"record belongs to another tenant")
            raise MemoryAccessDenied("cross-tenant record")
        if r["record_type"] not in POLICIES.get(cop, {}).get("reads", ()):
            self._log(cop, case_id, tenant_id, "get", r["record_type"], record_id, "DENIED",
                      "record type not in this COP's read policy")
            raise MemoryAccessDenied("record type not readable by this COP")
        return r

    # ---------------------------------------------------------- adaptation
    def propose_baseline_update(self, cop, tenant_id, record_ids, change):
        """A proposal, never an applied change. Refuses unvalidated support."""
        support, refused = [], []
        for rid in record_ids:
            rows = [r for r in self.store.select("cop_memory_records") if r["record_id"] == rid]
            if not rows or rows[0]["tenant_id"] != tenant_id:
                refused.append({"record_id": rid, "reason": "not found in this tenant"})
            elif rows[0]["validation_status"] != VALIDATED:
                refused.append({"record_id": rid,
                                "reason": f"status {rows[0]['validation_status']}, not VALIDATED"})
            else:
                support.append(rid)
        return {"cop": cop, "change": change, "supported_by": support, "refused": refused,
                "applied": False, "requires_human_approval": True,
                "admissible": bool(support) and not refused}
