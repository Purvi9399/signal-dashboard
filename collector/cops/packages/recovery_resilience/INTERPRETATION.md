# Recovery & Resilience: interpretation

Observation (`cop_package.observe_recovery_resilience`) answers the four questions. It reads:
- the metrics;
- the session's rows;
- the recovery records and their recomputed checkpoints;
- Behavior Analytics' finding, as context;
- validated memory.

Interpretation (`decide_recovery_resilience`) turns the answers into the label, severity, confidence and action.
Both steps are deterministic, and no model is called.

## RR-1: was the workflow disrupted?

DISRUPTED when an undisputed row shows any of these:
- it failed (success false or a non-zero exit code; `requirements._had_failure`'s definition);
- it stalled (hung_seconds);
- a deny rule stopped it (policy_rule_matched with permission_decision deny);
- it reverted work;
- it ran an irreversible command.

A recovery record also makes RR-1 DISRUPTED, because a recorded restoration presupposes a disruption.

The other answers:
- Failures only on rows the collectors dispute: INCONCLUSIVE (CONFLICTING_SOURCES).
- NOT_DISRUPTED: insights.failed is observed and none of the above holds.
- Otherwise INCONCLUSIVE.

## RR-2: was the system restored?

RR-2 follows the legacy order:
1. An undisputed irreversible command: IRREVERSIBLE_CHANGE. A disputed one is listed and grounds nothing.
2. No disruption: NOTHING_TO_RESTORE.
3. No record store, or an empty one: INCONCLUSIVE (NO_RECOVERY_RECORD). Resumed work, or the absence of new
   errors, is recovery observed, never recovery verified.
4. Each record gets a state:

| State | When |
|---|---|
| APPROVAL_PENDING, APPROVAL_REJECTED, NOT_STARTED | the action was not attempted |
| DISPUTED_EXECUTION | the execution step is on a row the collectors dispute |
| ATTEMPTED_UNVERIFIED | attempted, with no post-restore checks |
| INTEGRITY_FAILED | the recomputed checkpoint hash does not match |
| VERIFIED, PARTIAL_CHECKS, FAILED_CHECKS | all, some or none of the post-restore checks passed |
| CONFLICTING | two checked records on the same checkpoint and time disagree |

5. RR-2 is decided from the record states:
   - NOT_RESTORED: a hash failed, or every checked record failed all of its checks.
   - RESTORED_VERIFIED: every record is checked and VERIFIED.
   - PARTIALLY_RESTORED: some records are restored and some are not.
   - INCONCLUSIVE: no record is checked. The reason names approval pending, not attempted, unconfirmed or
     conflicting records.

## RR-3: are failures recurring after recovery?

After a verified or partial restoration, RR-3 looks at undisputed failures or stalls later than the latest
restoration time:
- Any such failure or stall: RECURRING. A verified restoration followed by recurrence reads PARTIAL_RECOVERY (D-RR7).
- None: NOT_RECURRING.

When RR-2 found no verified restoration, RR-3 is NO_RECOVERY_TO_FOLLOW. Validated recovery_outcome precedents are
listed and have no effect.

## RR-4: is stability confirmed?

| Reading | RR-4 | Action |
|---|---|---|
| IRREVERSIBLE, FAILED_RECOVERY | INTERVENTION_REQUIRED: further restoration is recommended; a person decides and acts | ALERT |
| PARTIAL_RECOVERY; INCONCLUSIVE after a disruption or with RR-1 unanswered | MONITORING_REQUIRED | WATCH |
| RECOVERED, with undisputed successful work after the restoration time, no recurrence, no telemetry measure deviating | STABILITY_CONFIRMED | NONE |
| RECOVERED otherwise | MONITORING_REQUIRED, with STABILITY_NOT_CONFIRMED recorded | WATCH |
| no disruption | NOTHING_TO_CONFIRM | NONE |

## The seven states kept apart (`cop_specific.recovery_status`)

| Field | Meaning |
|---|---|
| `intervention.recommended` | a record names an intended action |
| `intervention.approved`, `approval_pending` | approval APPROVED or NOT_REQUIRED; PENDING |
| `intervention.attempted` | execution ATTEMPTED, EXECUTED or FAILED, or a legacy restored_at |
| `intervention.executed_successfully` | execution EXECUTED, or a legacy restored_at. The action ran; that is not recovery |
| `recovery.observed` | reverted work, or a failed command that later succeeded |
| `recovery.verified` | checkpoint integrity holds and every post-restore check passed |
| `category`, `recovery.outcome` | incomplete, failed, irreversible or inconclusive, with the reason |

`intervention.executed_by_this_cop` is always empty.

## Legacy rule

The legacy reading (`rules._recovery_resilience_legacy`) is computed first and kept in
`cop_specific.legacy_reading`. When the package reads differently, a PREVIOUS_READING_DIFFERS limitation names
both readings. With no record store, the legacy MISSING_DEPENDENCY status and its missing dependency stand, with
the package's answers.
