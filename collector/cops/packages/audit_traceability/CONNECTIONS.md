# Audit & Traceability: connections

## Upstream

Each is read only when verified and substantive (`rules.verified`), and recorded in `upstream_used` and as an
upstream claim.

| COP | Read | Where |
|---|---|---|
| Runtime Monitoring | its label scales confidence and, if not SUFFICIENT, degrades AT-3; RM-3 PARTIALLY_LINKED or UNLINKED is a gap in AT-2; INSUFFICIENT withholds COMPLETE (`graph._gate_required`) | `cop_specific.evidence_assessment.questions.RM-3.answer` |
| Interaction Discovery | ID-2 NOT_RECONSTRUCTABLE breaks AT-2, PARTIALLY_RECONSTRUCTED is a gap; ID-5 RECONSTRUCTION_IMPAIRED impairs AT-5 | `cop_specific.questions` |
| Policy Enforcement | its claims' observable and step refs must resolve in the case (AT-4) | `check_decision_linkage` |

Any upstream decision's evidence refs are checked in AT-4, whichever COP made it. An upstream answer that the
record contradicts (ID-2 FULLY_RECONSTRUCTED, RM-3 ALL_ACTIONS_LINKED while a lane has no parent step) is kept as
contrary evidence; the record decides.

## Downstream

| Consumer | Reads | Effect |
|---|---|---|
| Governance Architecture | the verified finding: label, unclean answers, AT-4's untraced items (`rules.governance_architecture`) | recorded in `gap_levels.audit` and `upstream_used`; its label is unchanged, because a gap Audit & Traceability reports is owned by a COP (D9) |
| Incident Response | any verified finding (`rules.incident_response`) | recorded as used with its label |

The decision record names Governance Architecture as the recipient of BROKEN_TRACE and UNATTRIBUTABLE.

## Tested

`tests/test_cop_package_audit_traceability.py` and the harness: RM-3 changes the label; ID-2 and a contradicting
upstream reading; a Policy Enforcement decision whose evidence does not resolve; Governance Architecture receives
the finding, and its rule reads different answers from a COMPLETE and a BROKEN_TRACE finding.

## What a consumer receives

The published finding: label, status, verified, confidence, claims, lineage, `cop_specific.questions` with the
five answers and their bases, `drivers`, `also_held`, `contrary`, `trace`, `decision_linkage`. Governance
Architecture reads the question findings.
