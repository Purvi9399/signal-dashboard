"""
What a COP may do.

The action vocabulary already exists in models.ACTIONS and each spec already
declares permitted_actions. This file adds the missing half: an authority tier
per action, and one place that enforces it. Deriving the matrix from the specs
rather than keeping a second dictionary avoids two sources of truth drifting.

Tiers, in ascending order of what they cost if wrong:

    OBSERVE    look, conclude nothing anyone acts on
    RECORD     write a finding
    ALERT      tell a human something needs attention
    NOTIFY     hand a finding to another COP
    REQUEST    ask for context, or ask a human to approve
    RECOMMEND  state what should be done, without doing it
    INITIATE   start a controlled workflow such as escalation
    INTERVENE  change what the agent may do
"""

OBSERVE, RECORD, ALERT, NOTIFY = "OBSERVE", "RECORD", "ALERT", "NOTIFY"
REQUEST, RECOMMEND, INITIATE, INTERVENE = "REQUEST", "RECOMMEND", "INITIATE", "INTERVENE"

TIER = {
    "NONE": OBSERVE,
    "WATCH": RECORD,
    "ALERT": ALERT,
    "PASS_TO_COP": NOTIFY,
    "REQUEST_CONTEXT": REQUEST,
    "REQUEST_APPROVAL": REQUEST,
    "INSTRUCT": RECOMMEND,
    "ESCALATE": INITIATE,
    "PAUSE": INTERVENE,
    "BLOCK": INTERVENE,
}

# Never taken automatically, whatever a spec permits and whatever a model asks
# for. Released only by an explicit mode change and a human decision.
HUMAN_APPROVED_ONLY = (INTERVENE,)


def tiers_for(spec):
    return {TIER[a] for a in spec.permitted_actions if a in TIER}


def matrix(specs):
    """The capability table, derived from the specs themselves."""
    return {s.key: sorted(tiers_for(s), key=list(TIER.values()).index)
            for s in specs}


def check(spec, action, mode="dry_run", live_session=False):
    """Deterministic authority. Returns (allowed, substitute, reason)."""
    if action not in TIER:
        return False, "NONE", f"{action} is not an action"
    if action not in spec.permitted_actions:
        return False, "WATCH", f"{spec.key} may not {action}; its permitted actions are " \
                               f"{', '.join(spec.permitted_actions)}"
    tier = TIER[action]
    if tier in HUMAN_APPROVED_ONLY:
        if mode == "dry_run":
            return False, "ALERT", f"{action} changes what the agent may do; " \
                                   f"recorded as a recommendation in dry-run"
        if not live_session:
            return False, "ALERT", f"{action} cannot apply to a finished session"
    return True, action, ""
