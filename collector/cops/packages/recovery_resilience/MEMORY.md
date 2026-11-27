# Recovery & Resilience: memory

`memory.POLICIES["recovery_resilience"]`, unchanged: writes recovery_outcome, reads recovery_outcome, tenant scope.

## Write

The shared step (`graph.validate_memory_update`) writes a recovery_outcome record for a substantive reading:
- every label except INCONCLUSIVE is written;
- RECOVERED is the COP's quiet label, so the finding sets `remember_quiet` to have it written. After a disruption,
  a verified recovery is an outcome worth keeping (D-RR12).

The payload (`memory_payload`) holds the four answers, the category, each record's checkpoint, action and state,
the intervention and recovery states, and the precedents read. A record starts VERIFIED or UNVALIDATED. Only an
independent reviewer's feedback makes it VALIDATED.

## Read

`tools.assess_recovery_questions` reads validated recovery_outcome records of the tenant (`_memory_lookup`). RR-3
lists them as precedents ("comparison only, never proof"), and those with FAILED_RECOVERY, PARTIAL_RECOVERY or
IRREVERSIBLE as recurrence across sessions. A precedent never changes an answer, the label, the severity or the
action. When a recurrence is listed, RECURRENCE_NOT_WEIGHED is recorded (U-RR4).

## Not read

This COP reads no incident_outcome memory (not in its policy). Incident Response's earlier-cycle finding arrives on
the feedback edge, not through memory; the payload records only its finding_id as `earlier_incident_context`, so a
finding offered again in a later cycle adds nothing (R-RR1). It writes nothing but its own record.
