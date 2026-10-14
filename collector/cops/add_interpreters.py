"""
The four interpreters the package did not have.

Attached to RuleInterpreter rather than written inside the class body, so this
can be applied without touching the four that already work. Move them into the
class when convenient; the dispatch in interpret.py:60 looks up the method by
spec.key either way.

Each follows the existing shape: read metrics it was given, never recompute
them, answer only its own question, and hand the rest downstream.
"""

import pathlib, sys

ADDITION = '''

# ======================================================================
# Interpreters added for the remaining active COPS.
#
# Attached to the class rather than nested in it so the four that already
# work are untouched. RuleInterpreter.interpret dispatches on spec.key, so
# an attached method is found the same way a nested one is.
# ======================================================================

def _policy_enforcement(self, events, up, ctx, evid):
    """Was it permitted? Not: was it unusual, and not: was it harmful.

    There is no policy store yet, so this reads conditions rather than rules:
    a matched deny rule, a boundary crossing, a credential reached with nobody
    asked. Those are policy-relevant facts, and the rationale says plainly
    that they are not the same as a rule having been evaluated.
    """
    m = _by(events)
    watched = ("insights.blocked", "insights.outside", "insights.sensitive_unasked",
               "insights.secrets", "oversight.declined_inferred")
    present = [m[k] for k in watched if k in m]
    judged = [e for e in present if e.state not in ("UNKNOWN", "NOT_APPLICABLE")]
    hot = sorted(_hot(present, states=("EXCEPTION", "CRITICAL")), key=_ratio, reverse=True)
    scale = min(1.0, evid / 0.9)

    if not judged:
        return _out("UNCERTAIN", 0.2 * scale, "NONE", "WATCH",
                    "nothing here can be read as permitted or prohibited; "
                    "an unjudgeable action is not an allowed one",
                    "unknown: " + ", ".join(
                        f"{e.metric_id} ({e.features.get('reason')})" for e in present))

    if not hot:
        return _out("ALLOWED", 0.75 * scale, "NONE", "NONE", "",
                    f"{len(judged)} policy-relevant conditions within bounds; "
                    f"no deny rule matched, no boundary crossed" +
                    (f"; {len(present) - len(judged)} could not be judged"
                     if len(judged) < len(judged) + 1 and len(judged) < len(present) else ""))

    by_id = {e.metric_id: e for e in hot}
    driver = hot[0]
    parts = [f"{e.metric_id}={e.value} vs {e.threshold} ({e.state})" for e in hot]
    down = ["incident_response", "audit_traceability"]

    # A rule that fired, or a line that was crossed, is a violation. Sensitive
    # access with nobody asked is a missing approval, which is a different
    # failure and calls for a different response.
    violation = ("insights.blocked" in by_id or "insights.outside" in by_id
                 or "insights.secrets" in by_id)
    if "insights.secrets" in by_id:
        down.append("risk_mitigation")

    if violation:
        reading, action = "VIOLATION", "ALERT"
        reason = ("a deny rule matched or a boundary was crossed; this is a "
                  "permission finding, not a statistical one")
    else:
        reading, action = "APPROVAL_REQUIRED", "REQUEST_APPROVAL"
        reason = ("sensitive access with no approval recorded anywhere in the "
                  "session; the access may be legitimate and nobody was asked")

    parts.append("read from detected conditions rather than from an evaluated "
                 "rule set, because no policy store exists yet")
    sev = max((SEV[e.state] for e in hot), key=RANK.get)
    return _out(reading, min(0.9, 0.6 + 0.1 * len(hot)) * scale, sev, action, reason,
                "; ".join(parts), down, driver)


def _interaction_discovery(self, events, up, ctx, evid):
    """Who reached what, by which route, and how far did it travel?

    Structure only. Whether a route was permitted belongs to Policy
    Enforcement, which is why a new one is handed there rather than judged
    here.
    """
    m = _by(events)
    linkage = m.get("telemetry.lane_linkage")
    novel = m.get("access.first_seen_mcp")
    lanes = m.get("interaction.subagent_lanes")
    outside = m.get("insights.outside")
    present = [e for e in (linkage, novel, lanes, outside, m.get("interaction.mcp_servers"))
               if e is not None]
    judged = [e for e in present if e.state not in ("UNKNOWN", "NOT_APPLICABLE")]
    scale = min(1.0, evid / 0.9)

    if not judged:
        return _out("PATH_PARTIAL", 0.2 * scale, "NONE", "WATCH",
                    "no route can be reconstructed from what arrived",
                    "unknown: " + ", ".join(
                        f"{e.metric_id} ({e.features.get('reason')})" for e in present))

    # Lanes that cannot be attributed mean the edges between agents are lost,
    # even though the agents themselves are known to have run.
    if linkage is not None and linkage.state in ("EXCEPTION", "CRITICAL"):
        seen = (linkage.features or {}).get("lanes_seen")
        return _out("PATH_PARTIAL", 0.6 * scale, SEV[linkage.state], "WATCH",
                    "the agents are known; the handoffs between them are not",
                    f"lane linkage {linkage.value} vs {linkage.threshold}" +
                    (f"; {seen} lanes started and cannot be attributed" if seen else ""),
                    ["audit_traceability"], linkage)

    if novel is not None and novel.state in ("EXCEPTION", "CRITICAL"):
        server = (novel.features or {}).get("server", "a server not seen before")
        return _out("NEW_ROUTE", 0.8 * scale, SEV[novel.state], "PASS_TO_COP",
                    "a route that did not exist before now does; whether it is "
                    "permitted is Policy Enforcement's question",
                    f"first contact with {server}",
                    ["policy_enforcement", "audit_traceability"], novel)

    spreading = [e for e in (outside, lanes)
                 if e is not None and e.state in ("EXCEPTION", "CRITICAL")]
    if spreading:
        driver = spreading[0]
        return _out("PROPAGATING", 0.7 * scale, SEV[driver.state], "PASS_TO_COP",
                    "activity reached beyond where it started",
                    "; ".join(f"{e.metric_id}={e.value} vs {e.threshold}" for e in spreading),
                    ["policy_enforcement", "risk_mitigation"], driver)

    return _out("CONTAINED", 0.8 * scale, "NONE", "NONE", "",
                f"{len(judged)} route measures within bounds" +
                (f"; {len(present) - len(judged)} could not be judged"
                 if len(judged) < len(present) else ""))


def _audit_traceability(self, events, up, ctx, evid):
    """Can what happened be attributed and reconstructed?

    This COP also owns the case nobody else does: an action another COP could
    not judge is still an action in the record, and leaving it unowned would
    let a refusal evaporate.
    """
    m = _by(events)
    unattributed = m.get("insights.unattributed")
    turn = m.get("telemetry.turn_linkage")
    lane = m.get("telemetry.lane_linkage")
    agree = m.get("telemetry.source_agreement")
    present = [e for e in (unattributed, turn, lane, agree) if e is not None]
    judged = [e for e in present if e.state not in ("UNKNOWN", "NOT_APPLICABLE")]
    hot = sorted(_hot(present, states=("EXCEPTION", "CRITICAL")), key=_ratio, reverse=True)
    soft = _hot(present, states=("WARNING",))
    scale = min(1.0, evid / 0.9)

    # An upstream COP that could not decide leaves something in the record
    # that nobody has accounted for.
    blocked_up = [k for k, v in (up or {}).items()
                  if getattr(v, "decision_status", None) == "BLOCKED_BY_EVIDENCE"]

    if not judged and not blocked_up:
        return _out("UNATTRIBUTABLE", 0.3 * scale, "MEDIUM", "ALERT",
                    "nothing here can be attributed or reconstructed",
                    "unknown: " + ", ".join(
                        f"{e.metric_id} ({e.features.get('reason')})" for e in present))

    parts = [f"{e.metric_id}={e.value} vs {e.threshold} ({e.state})" for e in hot + soft]
    if blocked_up:
        parts.append(f"{', '.join(blocked_up)} could not reach a determination, so a "
                     f"consequential action sits in the record unjudged")

    severe = [e for e in hot if e.state == "CRITICAL"]
    if severe or (unattributed is not None and unattributed.state == "CRITICAL"):
        return _out("UNATTRIBUTABLE", 0.85 * scale, "HIGH", "ALERT",
                    "actions in this session cannot be tied to whoever took them",
                    "; ".join(parts), ["governance_architecture"],
                    (severe or hot)[0])
    if hot or blocked_up:
        return _out("BROKEN_TRACE", 0.8 * scale,
                    max((SEV[e.state] for e in hot), key=RANK.get) if hot else "MEDIUM",
                    "ALERT",
                    "the record has holes where a governance-critical action should be",
                    "; ".join(parts), ["governance_architecture"],
                    hot[0] if hot else None)
    if soft:
        return _out("PARTIAL", 0.75 * scale, "LOW", "WATCH",
                    "reconstructable, with gaps that do not yet break attribution",
                    "; ".join(parts), (), soft[0])
    return _out("COMPLETE", 0.85 * scale, "NONE", "NONE", "",
                f"{len(judged)} attribution measures intact")


def _classification_tagging(self, events, up, ctx, evid):
    """What kind of thing is this?

    Kind only. Not how serious, not what to do. It records and marks; its spec
    permits no other action, which is the point of having it separate.
    """
    m = _by(events)
    # Which metric fired decides the family. Boundary before oversight, since
    # a crossing describes the event better than the approval that was missing.
    FAMILY = (("insights.outside", "BOUNDARY"),
              ("insights.sensitive_unasked", "BOUNDARY"),
              ("insights.secrets", "INTEGRITY"),
              ("insights.blocked", "OVERSIGHT"))
    present = [m[k] for k, _ in FAMILY if k in m]
    judged = [e for e in present if e.state not in ("UNKNOWN", "NOT_APPLICABLE")]
    hot = _hot(present, states=("EXCEPTION", "CRITICAL"))
    scale = min(1.0, evid / 0.9)

    if not hot:
        return _out("UNCLASSIFIED", (0.7 if judged else 0.2) * scale, "NONE", "NONE", "",
                    f"{len(judged)} classifiable conditions, none above bounds"
                    if judged else "nothing classifiable could be judged")

    ranked = [(fam, m[k]) for k, fam in FAMILY
              if k in m and m[k] in hot]
    family, driver = ranked[0]
    others = sorted({f for f, _ in ranked[1:]})
    return _out(family, min(0.9, 0.65 + 0.1 * len(ranked)) * scale,
                max((SEV[e.state] for _, e in ranked), key=RANK.get),
                "NONE", "",
                f"classified as {family} on {driver.metric_id}={driver.value}" +
                (f"; also carries {', '.join(others)}" if others else "") +
                "; kind only, severity and response belong elsewhere",
                ["governance_intelligence"], driver)


RuleInterpreter.policy_enforcement = _policy_enforcement
RuleInterpreter.interaction_discovery = _interaction_discovery
RuleInterpreter.audit_traceability = _audit_traceability
RuleInterpreter.classification_tagging = _classification_tagging
'''


def main():
    p = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "interpret.py")
    s = p.read_text()
    if "_policy_enforcement" in s:
        print("already applied"); return
    anchor = "\nINSTRUCTION = "
    if anchor in s:
        s = s.replace(anchor, ADDITION + anchor, 1)      # keep rules above the LLM part
    else:
        s += ADDITION
    p.write_text(s)
    import ast; ast.parse(s)
    print(f"four interpreters added to {p} ({len(s.splitlines())} lines)")


if __name__ == "__main__":
    main()
