"""
Finalising a decision against the evidence.

A COP's interpreter answers its behavioural question. This completes the
decision: it attaches what the evidence allowed, adjusts confidence, and
enforces two rules that no interpreter can apply on its own because neither is
visible from inside one COP.

RULE 1  Blocked is not silent.
        A COP that cannot make its substantive determination must still return
        a useful governance outcome: what is missing, why it matters, what
        would unblock it, and a request for the missing context. "I cannot
        determine permission because file identity is unavailable" is a
        decision. NONE is not.

RULE 2  A blocked upstream is not a clean upstream.
        A COP whose input was blocked must not return its nothing-to-see
        reading. Not knowing whether a sensitive file was touched is not the
        same as knowing none was, and a downstream COP that cannot tell the
        difference will report safety it has no basis for.

Both rules can only raise attention, never lower it, and neither changes the
interpretation. The COP's answer to its own question stands; what changes is
what happens next.
"""

from .evidence import DECIDED, DEGRADED, BLOCKED

# How much attention each action represents. Floors move up this order only.
LADDER = ["NONE", "WATCH", "REQUEST_CONTEXT", "ALERT", "REQUEST_APPROVAL",
          "INSTRUCT", "ESCALATE", "PAUSE", "BLOCK"]


def _raise_to(action, floor, permitted):
    """Lift an action to at least the floor, within what this COP may do."""
    if action not in LADDER or floor not in LADDER:
        return action
    if LADDER.index(action) >= LADDER.index(floor):
        return action
    for candidate in LADDER[LADDER.index(floor):]:
        if candidate in permitted:
            return candidate
    # nothing at or above the floor is permitted: the most this COP may do
    allowed = [a for a in permitted if a in LADDER]
    return max(allowed, key=LADDER.index) if allowed else action


def finalise(spec, out, env, upstream=None):
    """out is the interpreter's dict; env the evidence envelope."""
    upstream = upstream or {}
    permitted = set(getattr(spec, "permitted_actions", LADDER))
    status = env.decision_status
    notes = []

    out = dict(out)
    out["decision_status"] = status
    out["missing_evidence"] = list(env.missing)
    out["blocking_evidence"] = list(env.blocking)
    out["event_evidence"] = [
        {"event": e["event"], "occurred": True, "detail_missing": e["detail_missing"]}
        for e in env.event_evidence]
    out["confidence"] = round(out.get("confidence", 0.5) * env.confidence_factor, 2)

    # ---- Rule 1 ----------------------------------------------------------
    if status == BLOCKED:
        what = ", ".join(env.blocking)
        events = ", ".join(sorted({e["event"] for e in env.event_evidence})) or "the activity"
        out["action"] = _raise_to(out.get("action", "NONE"), "REQUEST_CONTEXT", permitted)
        out["action_reason"] = (
            f"cannot answer this question without {what}; "
            f"{events} is known to have occurred, so the absence is a gap in the record "
            f"rather than an absence of activity")
        notes.append(
            f"blocked on {what}: {events} occurred and the detail needed to judge it "
            f"never reached the record. This is not a finding that nothing happened.")
        if not out.get("downstream"):
            out["downstream"] = ["audit_traceability"]
        elif "audit_traceability" not in out["downstream"]:
            out["downstream"].append("audit_traceability")

    # ---- Rule 2 ----------------------------------------------------------
    def _status(v):
        # upstream may be a harness dict or a COPDecision-like object
        if isinstance(v, dict):
            return v.get("decision_status")
        return getattr(v, "decision_status", None)

    blocked_up = [k for k, v in upstream.items() if v and _status(v) == BLOCKED]
    if blocked_up:
        quiet = out.get("interpretation") == _quiet_label(spec)
        if quiet or out.get("action") in ("NONE", None):
            out["action"] = _raise_to(out.get("action", "NONE"), "WATCH", permitted)
            out["action_reason"] = (
                f"{', '.join(blocked_up)} could not reach a determination; "
                f"an unjudgeable situation is not a clean one")
            notes.append(
                f"upstream blocked ({', '.join(blocked_up)}), so this reading rests on "
                f"a question nobody could answer")
        out["upstream_blocked"] = blocked_up

    # ---- degraded: carry the limitation, do not hide it -------------------
    if status == DEGRADED and env.missing:
        notes.append(f"decided with {', '.join(env.missing)} unavailable; "
                     f"confidence reduced accordingly")

    if notes:
        out["rationale"] = "; ".join(filter(None, [out.get("rationale", "")] + notes))
    return out


def _quiet_label(spec):
    """The reading that means nothing needs attention: the first, by convention."""
    labels = getattr(spec, "interpretations", ())
    return labels[0] if labels else None
