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


# ====================================================================== typed action taxonomy
#
# The ten legacy action names above stay valid everywhere (findings, records,
# the bench, project-signal mappings). The taxonomy below adds typed actions
# with operational semantics. Each typed action has a legacy alias: the
# closest legacy name, which is what `proposed_action.action` keeps carrying
# for consumers that only know the legacy vocabulary. Authority is decided on
# the typed name (`proposed_action.typed_action`).
#
# Permission for a typed action comes from one of three places, checked in
# this order by permitted():
#   1. the legacy name itself is in spec.permitted_actions (unchanged rule);
#   2. it is an equivalent alias of a permitted legacy action (EQUIVALENT);
#   3. it is granted to every COP (UNIVERSAL) or to this COP (GRANTS).
# A stronger new power (STOP_SESSION, RESTRICT_CAPABILITY, RELEASE_HOLD,
# RESTART_ISOLATED_SESSION, REQUEST_ROLLBACK) is never implied by an alias;
# it needs an explicit grant.

from dataclasses import dataclass as _dataclass, asdict as _asdict


@_dataclass(frozen=True)
class ActionSpec:
    name: str
    group: str
    authority_tier: str
    legacy_alias: str
    trigger: str                 # the finding type that calls for it
    required_evidence: str       # what must be established, and the minimum support score
    min_score: int               # 0 = no minimum (the action itself gathers evidence)
    quotient_band: str           # the COP band it may run in without escalation
    routing_tier: str            # the tier this action puts the decision in
    target: str                  # tool_request | operation | session | agent | tenant | cop | case
    reversibility: str           # reversible | partly_reversible | irreversible
    approval: str                # none | required | required_supervised
    execution: str               # auto | propose_only
    on_failure: str              # failure or timeout behaviour
    observable_result: str
    verified_by: str

    def to_dict(self):
        return _asdict(self)


_A = ActionSpec
TAXONOMY = {a.name: a for a in (
    # ---- no action
    _A("NONE", "none", OBSERVE, "NONE", "PASS, or FAIL/INCONCLUSIVE where no action is justified",
       "none", 0, "any", "autonomous", "case", "reversible", "none", "auto",
       "nothing to fail", "decision record only", "record exists (persistence)"),
    # ---- observation and evidence
    _A("WATCH", "observation", RECORD, "WATCH", "verified attention finding of low consequence",
       "verified finding, score >= 3", 3, "assisted", "autonomous", "session", "reversible", "none", "auto",
       "lapses with the session; nothing to undo", "watch entry against the session",
       "watch entry present in the decision record"),
    _A("REQUEST_CONTEXT", "observation", REQUEST, "REQUEST_CONTEXT",
       "INCONCLUSIVE: a required artifact is missing and cannot be retrieved by any tool",
       "the missing item is named; no score minimum", 0, "any", "assisted", "tenant", "reversible", "none",
       "auto", "on timeout the finding stays INCONCLUSIVE and the request is listed as open",
       "a context request naming the missing artifact and who can supply it",
       "the artifact appears in a later case (requirements.assess_requirements)"),
    _A("COLLECT_EVIDENCE", "observation", OBSERVE, "NONE",
       "INCONCLUSIVE: a required item is MISSING_BUT_RETRIEVABLE with a permitted tool",
       "the retrieving tool is in the COP's allowlist", 0, "any", "assisted", "case", "reversible", "none",
       "auto", "tool failure leaves the item missing; the next step becomes REQUEST_CONTEXT",
       "a bounded retrieval with the named tool", "evidence_precheck reads the item RETRIEVED_AND_VALIDATED"),
    _A("RECHECK", "observation", OBSERVE, "NONE",
       "INCONCLUSIVE: a candidate was rejected for a recoverable reason (SC-01 value/citation, DS-03)",
       "verifier feedback exists; no score minimum", 0, "specialist", "autonomous", "case", "reversible",
       "none", "auto", "one bounded re-assessment; a second rejection delegates to the specialist",
       "a re-assessment verified by the same independent checks", "independent_verify on the revised candidate"),
    _A("SCHEDULE_REVIEW", "observation", RECORD, "WATCH",
       "verified aggregate or structural finding that needs a periodic, not immediate, review",
       "verified finding, score >= 3", 3, "assisted", "autonomous", "tenant", "reversible", "none", "auto",
       "an unscheduled review is listed as overdue", "a review entry with a due date",
       "review entry present; outcome recorded by the reviewing COP"),
    # ---- agent guidance
    _A("INSTRUCT", "guidance", RECOMMEND, "INSTRUCT", "verified finding the agent can correct itself",
       "verified finding, score >= 3", 3, "autonomous", "autonomous", "agent", "reversible",
       "required_supervised", "propose_only", "no confirmation: escalate to the operator",
       "an instruction delivered to the agent", "a later turn shows the instructed change"),
    _A("REQUEST_CORRECTION", "guidance", RECOMMEND, "INSTRUCT",
       "verified drift or error the agent should correct", "verified finding, score >= 3", 3,
       "autonomous", "autonomous", "agent", "reversible", "required_supervised", "propose_only",
       "no correction observed: WATCH then ESCALATE", "a correction request to the agent",
       "the drifting metric returns to NORMAL in a later case"),
    _A("RETRY_WITH_CONSTRAINTS", "guidance", RECOMMEND, "INSTRUCT",
       "verified redundant or looping work", "verified finding, score >= 3", 3, "autonomous",
       "autonomous", "agent", "reversible", "required_supervised", "propose_only",
       "a second loop escalates to the specialist", "a retry bounded by explicit limits",
       "repetition metric below threshold after the retry"),
    _A("CHANGE_EXECUTION_PLAN", "guidance", RECOMMEND, "INSTRUCT", "verified stall or wrong branch",
       "verified finding, score >= 4", 4, "autonomous", "autonomous", "agent", "reversible",
       "required_supervised", "propose_only", "plan not adopted: ESCALATE",
       "a revised plan delivered to the agent", "milestones progress in a later case"),
    # ---- coordination
    _A("ALERT", "coordination", ALERT, "ALERT", "verified attention finding a human team should know of",
       "verified finding, score >= 3", 3, "assisted", "autonomous", "tenant", "reversible", "none", "auto",
       "delivery failure is recorded; the finding stands", "an alert to the reviewing team",
       "alert recorded with the finding id"),
    _A("REQUEST_COP_SUPPORT", "coordination", NOTIFY, "PASS_TO_COP",
       "bounded help from another COP: missing upstream finding, telemetry gap",
       "the receiving COP owns the missing evidence", 0, "any", "assisted", "cop", "reversible", "none",
       "auto", "hop limit or timeout returns the case to the initiator, then to a human",
       "a referral contract to the named COP", "the receiving COP publishes a verified finding"),
    _A("DELEGATE_TO_SPECIALIST", "coordination", NOTIFY, "PASS_TO_COP",
       "a decision the COP cannot resolve within its band: repeated failure, exhausted recovery, "
       "COP-specific specialist trigger", "verifier results and the triage record travel with it", 0,
       "specialist", "specialist", "cop", "reversible", "none", "auto",
       "cycle, hop limit, timeout or cost limit escalates to a human",
       "a specialist task with a referral contract", "the specialist's verified result returns to the initiator"),
    _A("ESCALATE", "coordination", INITIATE, "ESCALATE", "verified material finding needing a controlled workflow",
       "verified finding, score >= 4", 4, "human", "human", "tenant", "reversible", "required_supervised",
       "propose_only", "unacknowledged escalation re-raises once, then stays open",
       "an escalation opened", "acknowledgement recorded"),
    _A("OPEN_INCIDENT", "coordination", INITIATE, "ESCALATE", "verified INCIDENT",
       "verified finding, score >= 4", 4, "human", "human", "tenant", "reversible", "required_supervised",
       "propose_only", "unacknowledged incident re-raises once, then stays open",
       "an incident record with the evidence chain", "incident outcome recorded (get_incident_outcomes)"),
    # ---- permission and intervention
    _A("REQUEST_APPROVAL", "permission", REQUEST, "REQUEST_APPROVAL",
       "verified APPROVAL_REQUIRED, or an operation pending a human decision",
       "verified finding, score >= 3, and a policy basis (PE-01)", 3, "any", "human", "operation",
       "reversible", "required", "propose_only", "no answer is a rejection (fail closed)",
       "an approval request", "AU-02 records the decision"),
    _A("HOLD_TOOL_CALL", "permission", INTERVENE, "BLOCK",
       "an operation is pending and the authoritative policy needed to judge it is missing",
       "the pending operation and the missing policy are named; no score minimum", 0, "any", "human",
       "tool_request", "reversible", "required", "propose_only",
       "a hold that cannot be applied leaves the operation un-evaluated and escalates",
       "the pending tool call is held", "EX-01 observes the hold; RELEASE_HOLD ends it"),
    _A("PAUSE_SESSION", "permission", INTERVENE, "PAUSE", "verified incident or violation in a live session",
       "verified finding, score >= 4, policy basis (PE-01)", 4, "human", "human", "session",
       "reversible", "required", "propose_only", "unconfirmed pause escalates", "session paused",
       "EX-01 observes the pause"),
    _A("BLOCK_OPERATION", "permission", INTERVENE, "BLOCK", "verified VIOLATION of an explicit policy",
       "verified finding, score >= 4, policy basis (PE-01)", 4, "human", "human", "operation",
       "partly_reversible", "required", "propose_only", "unconfirmed block escalates",
       "the operation is refused", "EX-01 observes the refusal"),
    _A("RESTRICT_CAPABILITY", "permission", INTERVENE, "BLOCK", "verified repeated violation by an agent",
       "verified finding, score >= 4, policy basis", 4, "human", "human", "agent", "partly_reversible",
       "required", "propose_only", "unconfirmed restriction escalates", "a capability removed",
       "EX-01 observes the restricted capability"),
    _A("STOP_SESSION", "permission", INTERVENE, "PAUSE", "verified critical incident with ongoing harm",
       "verified finding, score 5, policy basis", 5, "human", "human", "session", "irreversible",
       "required", "propose_only", "unconfirmed stop escalates", "session terminated",
       "EX-01 observes the session ended"),
    # ---- recovery
    _A("REQUEST_ROLLBACK", "recovery", INITIATE, "ESCALATE", "verified FAILED_RECOVERY with a checkpoint",
       "verified finding, score >= 4, checkpoint validated", 4, "human", "human", "session",
       "partly_reversible", "required", "propose_only", "rollback not confirmed: VERIFY_RECOVERY fails, escalate",
       "a rollback request to the operator", "VERIFY_RECOVERY on the restored state"),
    _A("RESTART_ISOLATED_SESSION", "recovery", INTERVENE, "PAUSE", "verified compromise needing a clean restart",
       "verified finding, score >= 4", 4, "human", "human", "session", "partly_reversible", "required",
       "propose_only", "restart unconfirmed escalates", "a new isolated session", "EX-01 observes the new session"),
    _A("VERIFY_RECOVERY", "recovery", OBSERVE, "NONE", "after any intervention or rollback",
       "the intervention's receipt", 0, "any", "autonomous", "session", "reversible", "none", "auto",
       "unverifiable recovery escalates to a human", "a recovery check result",
       "recovery_resilience reads RECOVERED"),
    _A("RELEASE_HOLD", "recovery", INTERVENE, "BLOCK", "a hold whose blocking condition has been resolved",
       "the resolving evidence (policy supplied, approval given)", 0, "any", "human", "tool_request",
       "reversible", "required", "propose_only", "release not confirmed leaves the hold in place",
       "the held tool call proceeds", "EX-01 observes the release"),
)}

# legacy name -> its typed name (for reading old proposals)
LEGACY_TO_TYPED = {"NONE": "NONE", "WATCH": "WATCH", "ALERT": "ALERT", "PASS_TO_COP": "REQUEST_COP_SUPPORT",
                   "REQUEST_CONTEXT": "REQUEST_CONTEXT", "REQUEST_APPROVAL": "REQUEST_APPROVAL",
                   "INSTRUCT": "INSTRUCT", "ESCALATE": "ESCALATE", "PAUSE": "PAUSE_SESSION",
                   "BLOCK": "BLOCK_OPERATION"}
# typed actions that a permitted legacy action implies (same tier, same effect class)
EQUIVALENT = {"REQUEST_COP_SUPPORT": "PASS_TO_COP", "REQUEST_CORRECTION": "INSTRUCT",
              "RETRY_WITH_CONSTRAINTS": "INSTRUCT", "CHANGE_EXECUTION_PLAN": "INSTRUCT",
              "OPEN_INCIDENT": "ESCALATE", "PAUSE_SESSION": "PAUSE", "BLOCK_OPERATION": "BLOCK",
              "SCHEDULE_REVIEW": "WATCH"}
# granted to every COP: none of them changes what an agent may do
UNIVERSAL = ("NONE", "COLLECT_EVIDENCE", "RECHECK", "VERIFY_RECOVERY", "REQUEST_CONTEXT",
             "REQUEST_COP_SUPPORT", "DELEGATE_TO_SPECIALIST")
# explicit grants of new powers, per COP (each also needs approval: tier INTERVENE or INITIATE)
GRANTS = {
    "policy_enforcement": ("HOLD_TOOL_CALL", "RELEASE_HOLD", "RESTRICT_CAPABILITY"),
    "incident_response": ("HOLD_TOOL_CALL", "RELEASE_HOLD", "STOP_SESSION", "RESTART_ISOLATED_SESSION",
                          "REQUEST_ROLLBACK", "OPEN_INCIDENT"),
    "recovery_resilience": ("REQUEST_ROLLBACK",),
}
for _a in TAXONOMY.values():
    TIER.setdefault(_a.name, _a.authority_tier)


def typed(action):
    """Typed name of any action name, legacy or typed."""
    return LEGACY_TO_TYPED.get(action, action)


def legacy(action):
    """Legacy-compatible name of any action name."""
    if action in LEGACY_TO_TYPED:
        return action
    a = TAXONOMY.get(action)
    return a.legacy_alias if a else action


def permitted(spec, action):
    """Whether this COP may take this action (legacy or typed name). Returns (ok, basis)."""
    t = typed(action)
    if action in spec.permitted_actions or legacy(t) in spec.permitted_actions and t == typed(legacy(t)):
        return True, "spec.permitted_actions"
    if EQUIVALENT.get(t) in spec.permitted_actions:
        return True, f"equivalent of permitted {EQUIVALENT[t]}"
    if t in UNIVERSAL:
        return True, "granted to every COP (no effect on the agent)"
    if t in GRANTS.get(spec.key, ()):
        return True, f"explicit grant to {spec.key}"
    return False, f"{spec.key} may not {t}"


def typed_permitted(spec):
    return sorted(a for a in TAXONOMY if permitted(spec, a)[0])


def needs_approval(action, approval_actions=()):
    """Approval policy for a typed action: INTERVENE always; others when their
    legacy alias is in the profile's approval actions (supervised mode)."""
    t = TAXONOMY.get(typed(action))
    if t is None:
        return False
    return t.authority_tier in HUMAN_APPROVED_ONLY or t.approval == "required" \
        or legacy(t.name) in approval_actions


def typed_matrix(specs):
    """The typed capability table: per COP, each permitted typed action and why."""
    return {s.key: {a: permitted(s, a)[1] for a in typed_permitted(s)} for s in specs}


def check_typed(spec, action, mode="dry_run", live_session=False):
    """check() for a typed or legacy name. Same substitution rules."""
    t = typed(action)
    if t not in TAXONOMY:
        return False, "NONE", f"{action} is not an action"
    ok, basis = permitted(spec, t)
    if not ok:
        return False, "WATCH" if "WATCH" in spec.permitted_actions else "NONE", basis
    if TAXONOMY[t].authority_tier in HUMAN_APPROVED_ONLY:
        if mode == "dry_run":
            return False, "ALERT" if permitted(spec, "ALERT")[0] else "NONE", \
                f"{t} changes what the agent may do; recorded as a recommendation in dry-run"
        if not live_session:
            return False, "ALERT" if permitted(spec, "ALERT")[0] else "NONE", \
                f"{t} cannot apply to a finished session"
    return True, t, ""
