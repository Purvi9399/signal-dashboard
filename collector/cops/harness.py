"""
The COP agent harness.

A bounded operating envelope around an interpretation. The model, or the rule
set, is one phase inside it; everything else is the controlled structure that
makes the result defensible: what the COP may read, what it may do, how far it
may go, and what is checked before anything leaves.

One harness, eighteen configurations. A COP differs by its contract, never by
having its own code path.

Phases are explicit and the machine may only move between declared ones. A COP
chooses between permitted branches; it cannot invent a stage. That is the
difference between orchestration and wandering.
"""

import time, types, uuid
from dataclasses import dataclass, field, asdict
from enum import Enum


class Phase(str, Enum):
    INIT = "INIT"
    ASSESS_EVIDENCE = "ASSESS_EVIDENCE"
    RETRIEVE_CONTEXT = "RETRIEVE_CONTEXT"
    INTERPRET = "INTERPRET"
    REVERIFY = "REVERIFY"
    AUTHORIZE = "AUTHORIZE"
    ACT = "ACT"
    CONFIRM = "CONFIRM"
    PERSIST = "PERSIST"
    FEEDBACK = "FEEDBACK"
    COMPLETE = "COMPLETE"
    FAILED = "FAILED"


# Where a phase may go. Anything else is a bug, not a decision.
ALLOWED = {
    Phase.INIT:             (Phase.ASSESS_EVIDENCE, Phase.FAILED),
    Phase.ASSESS_EVIDENCE:  (Phase.RETRIEVE_CONTEXT, Phase.INTERPRET, Phase.FAILED),
    Phase.RETRIEVE_CONTEXT: (Phase.INTERPRET, Phase.ASSESS_EVIDENCE, Phase.FAILED),
    Phase.INTERPRET:        (Phase.REVERIFY, Phase.RETRIEVE_CONTEXT, Phase.FAILED),
    Phase.REVERIFY:         (Phase.AUTHORIZE, Phase.INTERPRET, Phase.FAILED),
    Phase.AUTHORIZE:        (Phase.ACT, Phase.PERSIST, Phase.FAILED),
    Phase.ACT:              (Phase.CONFIRM, Phase.FAILED),
    Phase.CONFIRM:          (Phase.PERSIST, Phase.FAILED),
    Phase.PERSIST:          (Phase.FEEDBACK, Phase.FAILED),
    Phase.FEEDBACK:         (Phase.COMPLETE, Phase.FAILED),
    Phase.COMPLETE:         (),
    Phase.FAILED:           (),
}

STOP = ("decision_validated", "blocking_evidence_established", "approval_requested",
        "handoff_completed", "tool_failure_unrecoverable", "budget_exhausted",
        "invalid_transition")


@dataclass(frozen=True)
class Budgets:
    """A COP that cannot exhaust itself cannot be trusted to run unattended."""
    max_steps: int = 10
    max_tool_calls: int = 6
    max_context_requests: int = 2
    max_handoffs: int = 3
    max_retries: int = 1
    max_runtime_s: float = 60.0


@dataclass(frozen=True)
class HarnessConfig:
    """What a COP may do, beyond what its spec already says it reads.

    Additive to COPReadSpec: the spec keeps the behavioural question, metrics
    and permitted actions; this adds the operating envelope.
    """
    cop: str
    skills: tuple = ()
    tools: tuple = ()
    memory: dict = field(default_factory=lambda: {
        "working": True, "session": False, "historical": "none"})
    budgets: Budgets = Budgets()
    reverify: str = "standard"            # standard | strict for high-impact
    cache_ttl_s: int = 60
    autonomous_actions: tuple = ("NONE", "WATCH")
    approval_actions: tuple = ("ALERT", "INSTRUCT", "REQUEST_APPROVAL", "ESCALATE")
    prohibited_actions: tuple = ("PAUSE", "BLOCK")


@dataclass
class RunState:
    """Where this run has reached. Not memory: memory is what it knows,
    state is where it is."""
    run_id: str
    cop: str
    session_id: str
    phase: Phase = Phase.INIT
    steps: int = 0
    tool_calls: int = 0
    context_requests: int = 0
    handoffs: int = 0
    retries: int = 0
    started_at: float = field(default_factory=time.time)
    gathered: list = field(default_factory=list)
    missing: list = field(default_factory=list)
    completed_skills: list = field(default_factory=list)
    recommended_action: str = None
    attempted_action: str = None
    action_status: str = None               # NOT_ATTEMPTED | ATTEMPTED | CONFIRMED | FAILED
    stop_reason: str = None
    trail: list = field(default_factory=list)

    def to(self, phase, note=""):
        if phase not in ALLOWED[self.phase]:
            self.trail.append((self.phase.value, "INVALID", f"-> {phase.value}"))
            self.stop_reason = "invalid_transition"
            self.phase = Phase.FAILED
            return False
        self.trail.append((self.phase.value, phase.value, note))
        self.phase = phase
        self.steps += 1
        return True

    def exhausted(self, b: Budgets):
        if self.steps > b.max_steps:            return "max_steps"
        if self.tool_calls > b.max_tool_calls:  return "max_tool_calls"
        if self.context_requests > b.max_context_requests: return "max_context_requests"
        if self.handoffs > b.max_handoffs:      return "max_handoffs"
        if self.retries > b.max_retries:        return "max_retries"
        if time.time() - self.started_at > b.max_runtime_s: return "max_runtime_s"
        return None

    def elapsed_ms(self):
        return int((time.time() - self.started_at) * 1000)


# ---------------------------------------------------------------- skills, tools

@dataclass(frozen=True)
class Tool:
    name: str
    fn: object
    read_only: bool = True
    permitted_for: tuple = ()               # empty means every COP


class Registry:
    """Skills are what a COP asks for; tools are what answers. Keeping them
    apart means the harness chooses the service, and a COP never holds a
    connection or a query."""

    def __init__(self):
        self.tools, self.skills = {}, {}

    def tool(self, name, fn, read_only=True, permitted_for=()):
        self.tools[name] = Tool(name, fn, read_only, tuple(permitted_for))

    def skill(self, name, tool_name, transform=None):
        self.skills[name] = (tool_name, transform)

    def call(self, cop, skill, state, cfg, *a, **kw):
        if skill not in cfg.skills:
            raise PermissionError(f"{cop} does not have the skill {skill}")
        if skill not in self.skills:
            raise LookupError(f"no skill registered as {skill}")
        tool_name, transform = self.skills[skill]
        tool = self.tools.get(tool_name)
        if tool is None:
            raise LookupError(f"skill {skill} needs the tool {tool_name}, which is absent")
        if tool_name not in cfg.tools:
            raise PermissionError(f"{cop} may not use {tool_name}")
        if tool.permitted_for and cop not in tool.permitted_for:
            raise PermissionError(f"{tool_name} is not available to {cop}")
        state.tool_calls += 1
        out = tool.fn(*a, **kw)
        state.completed_skills.append(skill)
        return transform(out) if transform else out


# ---------------------------------------------------------------- reverification

def reverify(spec, out, events, strict=False):
    """Check the interpretation against what was actually supplied.

    This is a schema and vocabulary check, not an independent verifier: it
    does not recompute anything and cannot catch a wrong reading that uses
    allowed labels. The independent checks live in cops/runtime/verifiers.py.

    Nothing here trusts the interpreter. An interpretation outside the COP's
    vocabulary, an action it may not take, a confidence out of range, or a
    metric it cited but was never given are all rejected rather than recorded.
    """
    problems = []
    supplied = {e.metric_id for e in events} if events else set()

    from .models import RESERVED_INTERPRETATIONS
    if out.get("interpretation") not in tuple(getattr(spec, "interpretations", ())) + RESERVED_INTERPRETATIONS:
        problems.append(f"interpretation {out.get('interpretation')!r} is not one of "
                        f"{spec.interpretations}")
    if out.get("action") not in getattr(spec, "permitted_actions", ()):
        problems.append(f"action {out.get('action')!r} is not permitted for {spec.key}")
    c = out.get("confidence")
    if c is None or not (0.0 <= float(c) <= 1.0):
        problems.append(f"confidence {c!r} is out of range")
    cited = out.get("metric_id")
    if cited and supplied and cited not in supplied:
        problems.append(f"cited metric {cited} was never supplied to this COP")
    for d in out.get("downstream", ()):
        if d not in getattr(spec, "downstream_cops", ()):
            problems.append(f"{spec.key} may not hand off to {d}")
    if strict and not out.get("rationale"):
        problems.append("no rationale given for a high-impact decision")
    return problems


# ---------------------------------------------------------------- the agent

class COPAgent:
    """One COP, run as a bounded state machine.

    The interpreter answers the behavioural question. Everything around it
    here is the harness: evidence first, context only if it would change the
    answer, reverification before authority, authority before action, and
    confirmation before anything is called done.
    """

    def __init__(self, spec, cfg, interpreter, evidence_service, registry,
                 finalise, authority_check, gap_registry=None, mode="dry_run",
                 confirm=None):
        self.spec, self.cfg = spec, cfg
        self.interpreter = interpreter
        self.evidence = evidence_service
        self.registry = registry
        self.finalise = finalise
        self.authority = authority_check
        self.gaps = gap_registry
        self.mode = mode
        # confirm(action, session_id) -> bool: observes whether an attempted
        # action took effect. Without one, an attempt is never called CONFIRMED.
        self.confirm = confirm

    def run(self, session_id, events, upstream=None, context=None):
        st = RunState(run_id=uuid.uuid4().hex[:12], cop=self.spec.key,
                      session_id=session_id)
        upstream, context = upstream or {}, dict(context or {})
        out, env = {}, None

        try:
            # ---- evidence first, always ---------------------------------
            if not st.to(Phase.ASSESS_EVIDENCE):
                return self._end(st, out, env)
            env = self.evidence.compile(session_id, self.spec.key,
                                        [e.metric_id for e in events])
            st.gathered, st.missing = list(env.available), list(env.missing)

            # ---- retrieve context only if it could change the answer -----
            wanted = [k for k in getattr(self.spec, "context_keys", ())
                      if k not in context]
            if wanted and env.decision_status != "DECIDED":
                if not st.to(Phase.RETRIEVE_CONTEXT, f"wants {wanted}"):
                    return self._end(st, out, env)
                for key in wanted[: self.cfg.budgets.max_context_requests]:
                    st.context_requests += 1
                    try:
                        context[key] = self.registry.call(
                            self.spec.key, f"retrieve_{key}", st, self.cfg, session_id)
                    except (PermissionError, LookupError) as e:
                        st.trail.append(("RETRIEVE_CONTEXT", "SKIPPED", str(e)))
                if (why := st.exhausted(self.cfg.budgets)):
                    st.stop_reason = f"budget_exhausted:{why}"
                    st.to(Phase.FAILED)
                    return self._end(st, out, env)

            # ---- interpret ----------------------------------------------
            if not st.to(Phase.INTERPRET):
                return self._end(st, out, env)
            # interpreters read upstream decisions by attribute, so pass
            # objects rather than dicts; a decision that is already an object
            # is left alone
            # Interpreters read an upstream decision by attribute, including
            # which COP produced it (interpret.py:225 reads d.cop). A dict
            # loses that, so carry the key in as well.
            up = {k: (v if hasattr(v, "interpretation")
                      else types.SimpleNamespace(**{"cop": k, **v}))
                  for k, v in upstream.items() if v}
            out = self.interpreter.interpret(self.spec, events, up, context,
                                             env.confidence_factor * 0.9)

            # ---- reverify before anything is believed --------------------
            if not st.to(Phase.REVERIFY):
                return self._end(st, out, env)
            problems = reverify(self.spec, out, events,
                                strict=self.cfg.reverify == "strict")
            if problems:
                st.retries += 1
                st.trail.append(("REVERIFY", "REJECTED", "; ".join(problems)))
                if st.retries > self.cfg.budgets.max_retries:
                    st.stop_reason = "reverification_failed"
                    st.to(Phase.FAILED)
                    return self._end(st, out, env, problems)
                # fall back to the quiet reading rather than trusting the output
                out["interpretation"] = self.spec.interpretations[0]
                out["action"] = "WATCH" if "WATCH" in self.spec.permitted_actions else "NONE"
                # an output that failed checking cannot keep the confidence it
                # claimed: the claim is part of what failed
                out["confidence"] = 0.2
                out["severity"] = "NONE"
                out["downstream"] = []
                out["rationale"] = ("output failed verification (" +
                                    "; ".join(problems) + "); reduced to a watch")

            # ---- evidence status, centrally, not per COP -----------------
            out = self.finalise(self.spec, out, env, upstream=upstream)
            st.recommended_action = out.get("action")

            # ---- authority: declared is not the same as enforced ---------
            if not st.to(Phase.AUTHORIZE):
                return self._end(st, out, env)
            allowed, substitute, why = self.authority(
                self.spec, st.recommended_action, mode=self.mode)
            if not allowed:
                st.trail.append(("AUTHORIZE", "SUBSTITUTED", why))
                out["action"] = substitute
                out["authority_note"] = why

            # ---- act, in dry-run: recommended is not attempted -----------
            if out["action"] in self.cfg.autonomous_actions and self.mode != "dry_run":
                if not st.to(Phase.ACT):
                    return self._end(st, out, env)
                st.attempted_action = out["action"]
                st.action_status = "ATTEMPTED"
                if not st.to(Phase.CONFIRM):
                    return self._end(st, out, env)
                if self.confirm is None:
                    st.action_status = "ATTEMPTED_UNCONFIRMED"
                elif self.confirm(out["action"], session_id):
                    st.action_status = "CONFIRMED"
                else:
                    # a failed outcome check is a failed action, not a done one
                    st.action_status = "FAILED"
                    st.stop_reason = "action_outcome_unconfirmed"
                    st.to(Phase.FAILED)
                    return self._end(st, out, env)
            else:
                st.action_status = "NOT_ATTEMPTED"
                st.stop_reason = ("blocking_evidence_established"
                                  if env.decision_status == "BLOCKED_BY_EVIDENCE"
                                  else "decision_validated")

            if not st.to(Phase.PERSIST):
                return self._end(st, out, env)
            if not st.to(Phase.FEEDBACK):
                return self._end(st, out, env)
            if self.gaps and env.gaps:
                self.gaps.record(list(env.gaps), session_id,
                                 "BLOCKED" if env.decision_status == "BLOCKED_BY_EVIDENCE"
                                 else "DEGRADED")
            st.to(Phase.COMPLETE)

        except Exception as e:                       # a failed COP is a result
            st.trail.append((st.phase.value, "ERROR", f"{type(e).__name__}: {e}"))
            st.stop_reason = "tool_failure_unrecoverable"
            st.phase = Phase.FAILED

        return self._end(st, out, env)

    def _end(self, st, out, env, problems=None):
        return {
            "run_id": st.run_id, "cop": self.spec.key, "session_id": st.session_id,
            "interpretation": out.get("interpretation"),
            "decision_status": out.get("decision_status",
                                       env.decision_status if env else None),
            "confidence": out.get("confidence"),
            "severity": out.get("severity"),
            "rationale": out.get("rationale"),
            "missing_evidence": st.missing,
            "blocking_evidence": out.get("blocking_evidence", []),
            # the action after authority; interpreters downstream read it as
            # .action, the same attribute a COPDecision carries
            "action": out.get("action"),
            "recommended_action": st.recommended_action,
            "attempted_action": st.attempted_action,
            "action_status": st.action_status,
            "authority_note": out.get("authority_note"),
            "downstream_cops": out.get("downstream", []),
            # observability: the harness watching itself
            "phase": st.phase.value, "steps": st.steps, "tool_calls": st.tool_calls,
            "context_requests": st.context_requests, "retries": st.retries,
            "stop_reason": st.stop_reason, "evaluation_ms": st.elapsed_ms(),
            "verification_problems": problems or [],
            "trail": [f"{a}->{b}" + (f" ({c})" if c else "") for a, b, c in st.trail],
        }
