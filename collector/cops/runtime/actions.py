"""
The action lifecycle: what a COP proposed, and what became of the proposal.

Every COP run ends in authority_check -> (await_approval) -> execute_action
(graph.py). Those nodes decide; this module records the decision as one typed
object, ActionLifecycle, attached to the proposed action as `lifecycle`. It
is additive: it reads the state the graph already keeps (the finding, its
rejected candidate, the original proposal, the authority checks, the execution
status) and changes none of them.

Stages, in the order the demo distinguishes them:

    NO_ACTION_PROPOSED      the COP proposed NONE
    PROPOSAL_WITHDRAWN      the COP proposed an action, but its finding failed
                            verification, so the action was withdrawn
    REJECTED_BY_AUTHORITY   the proposed action failed AU-01 for this COP and
                            mode; a substitute, if any, is recorded separately
    AWAITING_APPROVAL       proposed and authorised, waiting for a human
    APPROVAL_REJECTED       a human (or a malformed answer) declined it
    AUTHORISED_DRY_RUN      authorised; recorded by a dry-run adapter, no effect
    AUTHORISED_NO_EFFECT    authorised informational action outside dry-run
                            (WATCH, ALERT, PASS_TO_COP...): nothing to enforce
    EXECUTED                applied by the enforcement gateway and confirmed.
                            In this runtime the gateway is the mock one, so
                            `simulated` is True
    FAILED_OR_UNCONFIRMED   the gateway raised, or the outcome was not observed

Dry-run adapters describe what an action would have done. They have no
external effect: no block, no notification, no message. external_effect is
False on every receipt.
"""

from dataclasses import dataclass, field, asdict
from typing import Optional

from ..authority import (TIER, OBSERVE, RECORD, ALERT, NOTIFY, REQUEST, RECOMMEND, INITIATE,
                         INTERVENE)

NO_ACTION_PROPOSED = "NO_ACTION_PROPOSED"
PROPOSAL_WITHDRAWN = "PROPOSAL_WITHDRAWN"
REJECTED_BY_AUTHORITY = "REJECTED_BY_AUTHORITY"
AWAITING_APPROVAL = "AWAITING_APPROVAL"
APPROVAL_REJECTED = "APPROVAL_REJECTED"
AUTHORISED_DRY_RUN = "AUTHORISED_DRY_RUN"
AUTHORISED_NO_EFFECT = "AUTHORISED_NO_EFFECT"
EXECUTED = "EXECUTED"
FAILED_OR_UNCONFIRMED = "FAILED_OR_UNCONFIRMED"
STAGES = (NO_ACTION_PROPOSED, PROPOSAL_WITHDRAWN, REJECTED_BY_AUTHORITY, AWAITING_APPROVAL,
          APPROVAL_REJECTED, AUTHORISED_DRY_RUN, AUTHORISED_NO_EFFECT, EXECUTED, FAILED_OR_UNCONFIRMED)


@dataclass
class ActionLifecycle:
    stage: str
    candidate_action: str                 # what the COP's candidate finding proposed
    verified_action: str                  # what reached authority after verification
    final_action: str                     # what authority let stand (substitute or fallback)
    execution_status: str
    mode: str
    authorised: bool
    authority_reason: str = ""
    withdrawn_reason: str = ""
    substitute: Optional[dict] = None     # {"action", "stage"} when authority replaced the proposal
    dry_run_receipt: Optional[dict] = None
    execution_outcome: Optional[dict] = None
    approval: Optional[dict] = None
    external_effect: bool = False
    simulated: bool = True                # no real enforcement exists in this runtime
    checks: list = field(default_factory=list)

    def to_dict(self):
        return asdict(self)


# ---------------------------------------------------------------- dry-run adapters

_WOULD = {
    OBSERVE: "would take no action",
    RECORD: "would record a watch entry against the session",
    ALERT: "would raise an alert to the reviewing team",
    NOTIFY: "would hand the finding to a downstream COP",
    REQUEST: "would ask the operator for context or approval",
    RECOMMEND: "would send the agent an instruction",
    INITIATE: "would open an escalation",
    INTERVENE: "would pause or block the agent",
}


class DryRunAdapter:
    """Records what an authorised action would do. No external effect."""
    name = "dry_run"

    def __init__(self):
        self.log = []

    def record(self, cop, case_id, action, target):
        tier = TIER.get(action, "")
        r = {"adapter": self.name, "cop": cop, "case_id": case_id, "action": action, "tier": tier,
             "target": target or "session", "would": _WOULD.get(tier, f"would apply {action}"),
             "external_effect": False, "simulated": True}
        self.log.append(r)
        return r


# ---------------------------------------------------------------- the lifecycle

def candidate_action(finding):
    """The action the COP's own candidate proposed, before verification or gating."""
    rc = finding.get("rejected_candidate") or {}
    if rc.get("proposed_action"):
        return rc["proposed_action"].get("action") or "NONE"
    if finding.get("gated_action"):
        return finding["gated_action"]
    return (finding.get("proposed_action") or {}).get("action") or "NONE"


def lifecycle(finding, pa, mode):
    """One ActionLifecycle from the finding and the proposed action as the graph left them."""
    cand = candidate_action(finding)
    verified = (finding.get("proposed_action") or {}).get("action") or "NONE"
    requested = pa.get("requested_action") or verified
    final = pa.get("action") or "NONE"
    status = pa.get("execution_status")
    orig = pa.get("original_verification") or {}
    fa = pa.get("final_authority") or {}
    authorised = bool(fa.get("authorised", True))
    # a rejected finding withdraws its proposal even when the fallback happens to be the same action
    withdrawn = finding.get("status") == "REJECTED_BY_VERIFIER"
    gated = bool(finding.get("gated_action")) and finding["gated_action"] != verified
    rejected = orig.get("result") == "FAIL" or status == "NOT_EXECUTED_BLOCKED"

    def stage_of(action):
        if status == "AWAITING_APPROVAL":
            return AWAITING_APPROVAL
        if status == "APPROVAL_REJECTED":
            return APPROVAL_REJECTED
        if status == "EXECUTED_MOCK":
            return EXECUTED
        if status == "EXECUTION_FAILED":
            return FAILED_OR_UNCONFIRMED
        if status == "NOT_EXECUTED_BLOCKED":
            return REJECTED_BY_AUTHORITY
        if action == "NONE":
            return NO_ACTION_PROPOSED
        return AUTHORISED_DRY_RUN if mode == "dry_run" else AUTHORISED_NO_EFFECT

    sub = None
    if withdrawn or gated:
        stage = PROPOSAL_WITHDRAWN
        if final != "NONE":
            sub = {"action": final, "stage": stage_of(final), "source": "fallback after withdrawal"
                   if withdrawn else "fallback: required evidence unavailable"}
    elif rejected:
        stage = REJECTED_BY_AUTHORITY
        if final != requested and status != "NOT_EXECUTED_BLOCKED":
            sub = {"action": final, "stage": stage_of(final), "source": "authority substitute"}
    else:
        stage = stage_of(final)
    why = ""
    if withdrawn:
        why = (finding.get("proposed_action") or {}).get("reason") or "finding failed verification"
    elif gated:
        why = f"required evidence unavailable; {finding.get('gated_label')} withheld"
    return ActionLifecycle(
        stage=stage, candidate_action=cand, verified_action=verified, final_action=final,
        execution_status=status, mode=mode, authorised=authorised and not rejected,
        authority_reason=pa.get("authority_reason") or "", withdrawn_reason=why, substitute=sub,
        dry_run_receipt=pa.get("dry_run_receipt"), execution_outcome=pa.get("execution_outcome"),
        approval=pa.get("approval"),
        external_effect=bool((pa.get("execution_outcome") or {}).get("applied")
                             and not (pa.get("execution_outcome") or {}).get("simulated", True)),
        checks=[{k: c.get(k) for k in ("check_id", "result", "reason_code")}
                for c in [orig] + list(fa.get("checks") or []) if c]).to_dict()
