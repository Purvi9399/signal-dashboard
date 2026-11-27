"""
Metric lenses: the COPS architecture structured at the metric level.

A metric is computed once (signal_metrics.py). It is then observed by
several COPS, and means something different through each one's question.
The same metric can also mean something different to the same COP
depending on context: a retried command is ordinary in a test loop and a
warning sign in a deploy.

So the unit here is the lens, not the COP:

    Lens(metric, cop, means, contexts)

      means      what the metric says when read through this COP's question
      contexts   (condition, meaning) pairs: a deterministic condition on the
                 session's context and what the metric means when it holds

A COP's metric list is derived from this registry (specs.metric_keys_for),
so adding a COP to a metric is one line here and nothing else changes.

Conditions are evaluated against MetricEvent.context, which signal_metrics
computes once per session, plus the metric's own features. They are named
strings so the interpreter (rules or LLM) can be told which ones hold.
Conditions marked NEEDS are not computed yet; they say what data is missing.
"""

from collections import namedtuple, defaultdict

Lens = namedtuple("Lens", "metric cop means contexts")
L = Lens

# context conditions computed today (see signal_metrics.session_context)
CONDITIONS = {
    "transcript_only":   "session seen only through its transcript",
    "terminal_only":     "session seen only through the terminal wrapper",
    "approvals_hidden":  "this tool cannot show approval prompts",
    "has_subagents":     "session delegated to subagents",
    "touched_sensitive": "session touched a tier-3 path",
    "first_for_operator": "first session recorded for this operator",
    "test_command":      "the commands involved are test runs",
    "install_command":   "the commands involved install dependencies",
    "deploy_command":    "the commands involved deploy, push or publish",
    "in_subagent_lane":  "the hit sits in a subagent lane, not the main one",
}
# conditions the meaning depends on that no collector or script provides yet
NEEDS = {
    "auto_approve_mode":  "operator ran with approvals bypassed (permission_mode not stored)",
    "human_edit_overlap": "a person was editing the repo at the same time",
    "task_type":          "what the task was (refactor, docs, debugging)",
    "tool_version_change": "the tool updated during or just before the session",
    "production_target":  "the resource is production rather than local",
}

LENSES = [
    # ---------------------------------------------------------- loops and effort
    L("insights.repetition", "behavior_analytics",
      "the agent is looping: same call, same arguments, same turn",
      [("in_subagent_lane", "a subagent stuck without progress, invisible from the main lane"),
       ("test_command", "re-running the same check after edits; often verification, not a loop")]),
    L("insights.repetition", "workload_optimization",
      "redundant work: the result was already in hand",
      [("in_subagent_lane", "redundancy multiplied across parallel subagents")]),
    L("insights.repetition", "anomaly_detection",
      "only notable if this tool does not normally repeat; repetition alone is not deviation", []),
    L("insights.repetition", "workflow_orchestration",
      "progress may have stopped if repetition clusters at the end of the session", []),

    L("insights.retry", "behavior_analytics",
      "retry pressure: a failed command reissued unchanged",
      [("test_command", "the normal red-green loop of test-driven work"),
       ("install_command", "an environment problem the agent cannot fix by retrying"),
       ("deploy_command", "repeated attempts against an external system")]),
    L("insights.retry", "recovery_resilience",
      "an attempt to recover; whether it worked is the question",
      [("deploy_command", "partial side effects may compound with each attempt")]),
    L("insights.retry", "workload_optimization", "wasted runs", []),
    L("insights.retry", "anomaly_detection",
      "notable only against how often this tool normally retries", []),

    L("insights.stalled", "workflow_orchestration",
      "the task stopped moving",
      [("approvals_hidden", "may be waiting on an approval we cannot see, not a hung agent")]),
    L("insights.stalled", "resource_management",
      "a hung process holding a terminal and its resources", []),
    L("insights.stalled", "runtime_monitoring",
      "if the hooks also went silent, the collector may have been lost, not just the agent",
      [("terminal_only", "the wrapper is the only witness; nothing else can confirm the hang")]),

    L("insights.duration_outlier", "resource_management", "a step held resources far longer than usual", []),
    L("insights.duration_outlier", "workload_optimization", "a bottleneck step", []),
    L("insights.duration_outlier", "workflow_orchestration", "progress blocked on one step", []),
    L("insights.duration_outlier", "anomaly_detection", "step timing unlike the estate", []),

    L("insights.token_outlier", "resource_management",
      "consumption far above the estate norm",
      [("has_subagents", "fan-out explains part of it; judge per lane, not per session"),
       ("transcript_only", "token counts are the only evidence of how much work was done")]),
    L("insights.token_outlier", "anomaly_detection", "a session unlike others in size", []),
    L("insights.token_outlier", "workload_optimization",
      "inefficiency when tokens are high but actions are few", []),
    L("insights.token_outlier", "risk_mitigation", "a contributor to compound risk, not a risk alone", []),

    L("activity.action_rate_ratio", "anomaly_detection", "acting much faster than this tool normally does",
      [("has_subagents", "parallel lanes raise the rate legitimately")]),
    L("activity.action_rate_ratio", "resource_management", "load generated per minute", []),
    L("activity.action_rate_ratio", "workload_optimization", "throughput relative to peers", []),

    # ---------------------------------------------------------- boundaries and exposure
    L("insights.outside", "policy_enforcement",
      "a boundary crossing: work outside the workspace",
      [("touched_sensitive", "outside and sensitive together: credentials or keys, violation-grade")]),
    L("insights.outside", "interaction_discovery", "the agent's reach extends into other repos or the home directory", []),
    L("insights.outside", "risk_mitigation", "blast radius beyond the project", []),
    L("insights.outside", "classification_tagging", "BOUNDARY", []),

    L("insights.sensitive_unasked", "policy_enforcement",
      "a sensitive resource touched with no approval in the session",
      [("approvals_hidden", "UNCERTAIN, not VIOLATION: this tool cannot show whether it asked")]),
    L("insights.sensitive_unasked", "incident_response", "an exposure candidate", []),
    L("insights.sensitive_unasked", "risk_mitigation", "raises compound risk with secrets or network steps", []),
    L("insights.sensitive_unasked", "classification_tagging", "OVERSIGHT", []),

    L("insights.secrets", "incident_response",
      "secret-shaped content handled",
      [("deploy_command", "the secret may have left the machine")]),
    L("insights.secrets", "policy_enforcement", "secret-handling rule engaged", []),
    L("insights.secrets", "risk_mitigation", "severity multiplier for every other finding in the session", []),
    L("insights.secrets", "classification_tagging", "BOUNDARY, credential", []),
    L("insights.secrets", "audit_traceability", "evidence must be kept as a hash, never as content", []),

    L("insights.blocked", "policy_enforcement",
      "a control fired and held",
      [("first_for_operator", "likely a new user learning the rules, not persistence")]),
    L("insights.blocked", "incident_response", "worth watching when repeated: persistence against a control", []),
    L("insights.blocked", "knowledge_policy_management", "a rule hit; frequent hits on ordinary work mean the rule is too strict", []),
    L("insights.blocked", "behavior_analytics", "whether the agent routes around the block in its next steps", []),

    L("insights.no_oversight", "policy_enforcement", "a long session with no approval asked",
      [("approvals_hidden", "cannot be judged on this tool")]),
    L("insights.no_oversight", "governance_intelligence", "where unsupervised work concentrates", []),
    L("insights.no_oversight", "risk_mitigation", "length of unsupervised exposure", []),

    L("insights.lockfile", "risk_mitigation", "dependency change without a pinned lockfile: supply-chain drift", []),
    L("insights.lockfile", "classification_tagging", "INTEGRITY", []),

    # ---------------------------------------------------------- outcomes and recovery
    L("insights.failed", "behavior_analytics", "a failure pattern",
      [("test_command", "failing tests mid-task are expected; failing at the end is not")]),
    L("insights.failed", "recovery_resilience", "something to recover from", []),
    L("insights.failed", "workflow_orchestration", "progress obstructed", []),
    L("insights.failed", "anomaly_detection", "notable against this tool's usual failure rate", []),

    L("insights.reverted", "recovery_resilience", "a rollback: did the state return to good", []),
    L("insights.reverted", "behavior_analytics", "rework: the agent's output did not stand", []),
    L("insights.reverted", "governance_intelligence", "rework rate over time", []),

    # ---------------------------------------------------------- evidence
    L("insights.unattributed", "audit_traceability",
      "a change on disk no actor can be tied to", []),
    L("insights.unattributed", "interaction_discovery", "an unobserved actor may be working in the repo", []),
    L("insights.unattributed", "runtime_monitoring", "an attribution gap in the evidence", []),

    L("telemetry.collector_coverage", "runtime_monitoring",
      "evidence present: the collectors this tool should have were running",
      [("transcript_only", "a known historical gap, before hooks were installed"),
       ("terminal_only", "seen from outside only; nothing about what the agent decided")]),
    L("telemetry.collector_coverage", "agent_lifecycle_management",
      "coverage that drops for an agent that used to be covered means its configuration changed", []),
    L("telemetry.collector_coverage", "governance_architecture", "where the estate is not instrumented", []),

    L("telemetry.source_agreement", "runtime_monitoring", "evidence reliable: independent collectors agree", []),
    L("telemetry.source_agreement", "audit_traceability",
      "inherited from runtime_monitoring: conflicting accounts of the same event", []),

    L("telemetry.turn_linkage", "runtime_monitoring", "evidence complete enough to group actions into turns", []),
    L("telemetry.turn_linkage", "audit_traceability",
      "inherited from runtime_monitoring: decisions can be traced to the prompt that caused them", []),
    L("telemetry.turn_linkage", "workflow_orchestration", "progress can only be judged per turn if turns are known", []),

    L("telemetry.lane_linkage", "interaction_discovery", "the delegation path is complete", []),
    L("telemetry.lane_linkage", "audit_traceability",
      "inherited from runtime_monitoring: subagent work can be traced to who started it", []),
    L("telemetry.lane_linkage", "runtime_monitoring", "subagent evidence is connected", []),

    L("telemetry.evidence_score", "runtime_monitoring",
      "overall: how much of the evidence this session needs is present, judged and collected",
      [("transcript_only", "capped by design: only the transcript was ever collected"),
       ("terminal_only", "capped by design: only the terminal wrapper was collected")]),
    L("telemetry.evidence_score", "governance_architecture", "how well the estate can be judged at all", []),
    L("telemetry.event_freshness", "runtime_monitoring",
      "events arrive late, so a live decision would act on a stale picture", []),
    L("telemetry.event_freshness", "audit_traceability", "delayed ingestion widens the gap between action and record", []),

    L("lifecycle.version_change", "agent_lifecycle_management",
      "the tool's version differs from this operator's last session", []),
    L("lifecycle.version_change", "behavior_analytics",
      "a behavioural shift after an update is expected, not a new pattern", []),
    L("lifecycle.model_change", "agent_lifecycle_management",
      "the model behind the agent changed since the operator's last session", []),
    L("lifecycle.model_change", "resource_management",
      "a different model changes the cost and token profile", []),
    L("lifecycle.new_machine", "agent_lifecycle_management",
      "the same operator is now running the agent on a different machine", []),
    L("lifecycle.new_machine", "policy_enforcement", "is this machine one the operator is entitled to use", []),

    L("telemetry.unknown_share", "runtime_monitoring", "how much of this session cannot be judged at all", []),
    L("telemetry.unknown_share", "governance_architecture", "where the architecture is blind", []),

    # ---------------------------------------------------------- reach and change
    L("path.transition_novelty", "anomaly_detection", "the agent moved between steps in ways this tool has not before",
      [("first_for_operator", "everything is new for a new user; weak signal")]),
    L("path.transition_novelty", "behavior_analytics", "a new working strategy", []),
    L("path.transition_novelty", "interaction_discovery", "a new route, when the novel steps reach network or MCP", []),

    L("access.first_seen_mcp", "interaction_discovery", "a new edge: the agent reached a server it never had", []),
    L("access.first_seen_mcp", "agent_lifecycle_management", "the agent's connectivity changed", []),
    L("access.first_seen_mcp", "policy_enforcement", "is this server entitled for this operator", []),
    L("access.first_seen_mcp", "anomaly_detection", "resource-access novelty",
      [("first_for_operator", "no history to be novel against")]),

    L("interaction.mcp_servers", "interaction_discovery", "how widely the session reaches", []),
    L("interaction.mcp_servers", "agent_lifecycle_management", "connector footprint", []),

    L("interaction.subagent_lanes", "interaction_discovery", "fan-out of delegation", []),
    L("interaction.subagent_lanes", "workflow_orchestration", "excessive delegation for the task", []),
    L("interaction.subagent_lanes", "resource_management", "parallel consumption", []),

    # ---------------------------------------------------------- oversight quality
    L("oversight.fast_approvals", "policy_enforcement",
      "approvals given too fast to have been read: oversight on paper only",
      [("touched_sensitive", "rubber-stamping on sensitive resources")]),
    L("oversight.fast_approvals", "governance_intelligence", "whether approval fatigue is spreading", []),

    L("oversight.declined_inferred", "workflow_orchestration", "a human stopped the work", []),
    L("oversight.declined_inferred", "policy_enforcement", "a refusal the agent must respect", []),
    L("oversight.declined_inferred", "behavior_analytics", "whether the agent tried another way to do the refused thing", []),
]

# When a metric stops counting for a COP. Declared here, not coded into the
# interpreters, so the registry stays the one place meaning is defined.
# A discount never applies to a CRITICAL reading.
Discount = namedtuple("Discount", "metric cop condition why")
DISCOUNTS = [
    Discount("path.transition_novelty", "anomaly_detection", "first_for_operator",
             "first session for this operator: every transition is new"),
    Discount("access.first_seen_mcp", "anomaly_detection", "first_for_operator",
             "first session for this operator: every server is new"),
    Discount("activity.action_rate_ratio", "anomaly_detection", "has_subagents",
             "parallel subagent lanes raise the action rate legitimately"),
    Discount("insights.token_outlier", "anomaly_detection", "transcript_only",
             "seen only through a transcript, which carries tokens but not the work"),
]


def discounted(metric, cop, state, context):
    """Why this reading does not count for this COP here, or None."""
    if state == "CRITICAL":
        return None
    for d in DISCOUNTS:
        if d.metric == metric and d.cop == cop and context.get(d.condition):
            return d.why
    return None


BY_METRIC = defaultdict(list)
BY_COP = defaultdict(list)
for _l in LENSES:
    BY_METRIC[_l.metric].append(_l)
    BY_COP[_l.cop].append(_l)


def lens(metric, cop):
    return next((l for l in BY_METRIC.get(metric, []) if l.cop == cop), None)


def meaning(metric, cop, context):
    """What this metric means to this COP here: base meaning plus any context that holds."""
    l = lens(metric, cop)
    if not l:
        return None
    held = [m for cond, m in l.contexts if context.get(cond)]
    return {"means": l.means, "in_this_context": held}


def table_markdown():
    """The registry as a table, generated so the document and the code cannot diverge."""
    from .specs import BY_KEY
    out = ["| Metric | Observed by | Means, through that COP's question | Context that changes the meaning |",
           "|---|---|---|---|"]
    for metric in sorted(BY_METRIC):
        for i, l in enumerate(BY_METRIC[metric]):
            ctx = "; ".join(f"*{CONDITIONS.get(c, c)}*: {m}" for c, m in l.contexts) or "–"
            out.append(f"| {'`' + metric + '`' if i == 0 else ''} | {BY_KEY[l.cop].name} | {l.means} | {ctx} |")
    return "\n".join(out)
