"""
Policy Engine: decides which discovered-and-viable (agent, technique) pairs
are actually APPROVED to be activated on this machine, and reports why.

SCOPE (deliberately narrow, per the project's find/decide/activate
separation):
  - Policy Engine does NOT discover anything. It consumes a
    `discovery_engine.DiscoveryReport` that has already been produced by a
    real `DiscoveryEngine.scan()` call -- it never imports collector
    classes, never calls `detect()`, never touches the filesystem/PATH
    itself.
  - Policy Engine does NOT activate anything. Its output --
    `PolicyDecisionReport` -- is a decision + reason for every (agent,
    technique) pair, intended as the input to a later, separate wiring
    step that would actually call `CollectorRegistry.register()` /
    `activate()` for approved pairs. That wiring step is explicitly not
    built here.
  - Domain-based access control (which team/org/user sees what *data* once
    it's flowing) is explicitly OUT of scope for this component -- that is
    a separate, already-decided database/access-layer concern elsewhere in
    the project. This engine only answers "is this collection technique
    allowed to run on this machine at all," not "who gets to see the
    resulting events."

CONFIG FORMAT: JSON, not YAML.
Justification: this whole framework (see README.md's very first line) is
deliberately "dependency-free (Python 3 stdlib only)." PyYAML is not
installed in this environment and is not a stdlib module -- pulling it in
as a new dependency just for a config file format would break that
project-wide constraint for a governance-critical component. `json` is
stdlib, human-editable (comments aside), and every existing collector in
this package already parses JSON config (hooks.json, mcp_config.json,
etc.) -- using JSON keeps the Policy Engine's config format consistent
with every other config file this project already reads. The lack of
native JSON comments is worked around with a convention (see below): every
rule carries its own explicit `"reason"` string field, so auditability
does not depend on comment syntax.

DEFAULT POLICY: default-deny, not default-allow.
This is a governance/telemetry product whose stated purpose is agent
oversight -- the safer failure mode for a security-relevant control is
"a newly-discovered, never-explicitly-reviewed technique does not
activate" rather than "it activates automatically and someone has to
notice and turn it off after the fact." Default-deny also forces every
approval to be an explicit, auditable line in the policy file, which
matches the project's own auditability requirement (comment/reason field
per rule). The sample policy below still ends up approving the large
majority of what Discovery Engine finds viable on a real machine --
default-deny does not mean "deny everything," it means "nothing runs
without an explicit reason recorded for why it's allowed to."
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    from discovery.discovery_engine import DiscoveryReport, AgentDiscovery, TechniqueResult
except ImportError:  # pragma: no cover - only used for type hints
    DiscoveryReport = Any  # type: ignore
    AgentDiscovery = Any  # type: ignore
    TechniqueResult = Any  # type: ignore


DEFAULT_MODE = "deny"  # "deny" | "allow" -- see module docstring for why "deny"


@dataclass
class PolicyConfig:
    """In-memory, validated form of the JSON policy configuration.

    Shape of the underlying JSON file (see sample_policy.json):

        {
          "default_mode": "deny",              # or "allow"
          "default_reason": "...",              # applied when no rule matches
          "agent_rules": {
            "<agent>": {
              "allowed": true | false,
              "reason": "..."
            }
          },
          "technique_rules": [
            {
              "agent": "<agent>" | "*",          # "*" = applies to all agents
              "technique": "<technique>" | "*",  # "*" = applies to all techniques
              "allowed": true | false,
              "reason": "..."
            },
            ...
          ]
        }

    Rule precedence (most to least specific), documented explicitly so a
    decision's provenance is always traceable:
      1. An `agent_rules[agent].allowed == False` entry denies EVERY
         technique for that agent outright, regardless of technique_rules
         (e.g. "Antigravity is not yet approved org-wide").
      2. The most specific matching `technique_rules` entry
         (agent-specific + technique-specific) wins.
      3. A technique-specific wildcard-agent rule (`agent: "*"`) wins next
         (e.g. "MCP Proxy is disabled everywhere").
      4. An agent-specific wildcard-technique rule (`technique: "*"`) wins
         next (e.g. "CLI Wrapper only, no OTel, for Codex" expressed as a
         deny-all-others rule -- see sample policy for the concrete idiom).
      5. If nothing matches, `default_mode` applies, annotated with
         `default_reason`.
    """

    default_mode: str
    default_reason: str
    agent_rules: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    technique_rules: List[Dict[str, Any]] = field(default_factory=list)
    source_path: Optional[str] = None

    @staticmethod
    def from_dict(data: Dict[str, Any], source_path: Optional[str] = None) -> "PolicyConfig":
        default_mode = data.get("default_mode", DEFAULT_MODE)
        if default_mode not in ("allow", "deny"):
            raise ValueError(
                f"policy config default_mode must be 'allow' or 'deny', got {default_mode!r}"
            )
        default_reason = data.get(
            "default_reason",
            "no matching rule; default_mode is deny" if default_mode == "deny"
            else "no matching rule; default_mode is allow",
        )
        agent_rules = data.get("agent_rules", {})
        technique_rules = data.get("technique_rules", [])
        for rule in technique_rules:
            if "allowed" not in rule or "reason" not in rule:
                raise ValueError(
                    f"technique_rules entry missing required 'allowed'/'reason' field: {rule!r}"
                )
        return PolicyConfig(
            default_mode=default_mode,
            default_reason=default_reason,
            agent_rules=agent_rules,
            technique_rules=technique_rules,
            source_path=source_path,
        )

    @staticmethod
    def load(path: str) -> "PolicyConfig":
        p = Path(path)
        data = json.loads(p.read_text())
        return PolicyConfig.from_dict(data, source_path=str(p))

    @staticmethod
    def default() -> "PolicyConfig":
        """An empty policy: default_mode=deny, no rules at all. Useful as a
        safe fallback if no policy file is supplied -- everything is denied
        with an explicit, honest reason, never silently allowed."""
        return PolicyConfig(
            default_mode=DEFAULT_MODE,
            default_reason="no policy file loaded; default-deny fallback",
        )


@dataclass
class TechniqueDecision:
    agent: str
    technique: str
    viable: bool
    approved: bool
    reason: str
    decided_by: str  # "agent_rule" | "technique_rule" | "default" | "not_viable"
    collector_id: Optional[str] = None


@dataclass
class PolicyDecisionReport:
    decisions: List[TechniqueDecision] = field(default_factory=list)
    policy_source: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "policy_source": self.policy_source,
            "decisions": [asdict(d) for d in self.decisions],
        }

    def approved(self) -> List[TechniqueDecision]:
        return [d for d in self.decisions if d.approved]

    def denied(self) -> List[TechniqueDecision]:
        return [d for d in self.decisions if not d.approved]

    def approved_pairs(self) -> List[tuple]:
        """Convenience: [(agent, technique), ...] for approved+viable pairs
        only -- this is the literal "activation-approved list" the wiring
        step (not built here) would consume."""
        return [(d.agent, d.technique) for d in self.approved() if d.viable]


class PolicyEngine:
    """Applies a PolicyConfig to a DiscoveryReport. Pure decision logic --
    no discovery, no activation, no side effects of any kind (does not even
    read from disk itself; PolicyConfig.load() is a separate, explicit step
    so this class's __call__/evaluate() stays trivially testable with an
    in-memory config).
    """

    def __init__(self, policy: PolicyConfig) -> None:
        self._policy = policy

    def evaluate(self, discovery_report: "DiscoveryReport") -> PolicyDecisionReport:
        report = PolicyDecisionReport(policy_source=self._policy.source_path)
        for agent_result in discovery_report.agents:
            for technique_result in agent_result.technique_results:
                report.decisions.append(
                    self._decide(agent_result, technique_result)
                )
        return report

    def _decide(
        self,
        agent_result: "AgentDiscovery",
        technique_result: "TechniqueResult",
    ) -> TechniqueDecision:
        agent = agent_result.agent
        technique = technique_result.technique

        # Policy has nothing to decide about a pair that can't run anyway
        # (no collector implemented, or detect() returned False / errored).
        # Still surfaced explicitly, per spec, rather than dropped.
        if not technique_result.viable:
            why = technique_result.error or technique_result.note or (
                "discovery reported this pair as not viable on this machine"
            )
            return TechniqueDecision(
                agent=agent,
                technique=technique,
                viable=False,
                approved=False,
                reason=f"not viable per discovery: {why}",
                decided_by="not_viable",
                collector_id=technique_result.collector_id,
            )

        # 1. Agent-level rule: an explicit deny here overrides everything.
        agent_rule = self._policy.agent_rules.get(agent)
        if agent_rule is not None and agent_rule.get("allowed") is False:
            return TechniqueDecision(
                agent=agent,
                technique=technique,
                viable=True,
                approved=False,
                reason=agent_rule.get("reason", f"agent '{agent}' is denied by agent_rules"),
                decided_by="agent_rule",
                collector_id=technique_result.collector_id,
            )

        # 2-4. Technique rules, most specific first.
        rule, specificity = self._best_matching_technique_rule(agent, technique)
        if rule is not None:
            return TechniqueDecision(
                agent=agent,
                technique=technique,
                viable=True,
                approved=bool(rule["allowed"]),
                reason=rule["reason"],
                decided_by="technique_rule",
                collector_id=technique_result.collector_id,
            )

        # 5. Nothing matched (including no explicit agent-level allow) --
        # if an agent-level rule explicitly ALLOWS the agent, that alone
        # does not approve every technique; it just clears the agent-level
        # deny gate. Fall through to default_mode either way.
        approved = self._policy.default_mode == "allow"
        return TechniqueDecision(
            agent=agent,
            technique=technique,
            viable=True,
            approved=approved,
            reason=self._policy.default_reason,
            decided_by="default",
            collector_id=technique_result.collector_id,
        )

    def _best_matching_technique_rule(
        self, agent: str, technique: str
    ) -> tuple:
        """Returns (rule_dict_or_None, specificity) where specificity is
        3 = exact agent + exact technique
        2 = wildcard agent + exact technique   (e.g. "MCP Proxy everywhere")
        1 = exact agent + wildcard technique    (e.g. "Codex: only cli_wrapper")
        0 = wildcard agent + wildcard technique (a global catch-all rule)
        The single highest-specificity match wins; ties broken by the
        order rules are listed in the policy file (first match at the
        winning specificity wins), so operators can order rules by
        priority within the same specificity tier if needed.
        """
        best_rule = None
        best_specificity = -1
        for rule in self._policy.technique_rules:
            rule_agent = rule.get("agent", "*")
            rule_technique = rule.get("technique", "*")

            agent_matches = rule_agent in ("*", agent)
            technique_matches = rule_technique in ("*", technique)
            if not (agent_matches and technique_matches):
                continue

            specificity = (2 if rule_agent != "*" else 0) + (1 if rule_technique != "*" else 0)
            if specificity > best_specificity:
                best_specificity = specificity
                best_rule = rule

        return best_rule, best_specificity


def _print_decision_report(report: PolicyDecisionReport) -> None:
    print("=" * 78)
    print("POLICY ENGINE DECISION REPORT")
    print(f"policy_source: {report.policy_source}")
    print("=" * 78)

    by_agent: Dict[str, List[TechniqueDecision]] = {}
    for d in report.decisions:
        by_agent.setdefault(d.agent, []).append(d)

    for agent, decisions in by_agent.items():
        print(f"\n[{agent}]")
        for d in decisions:
            if not d.viable:
                tag = "SKIP (not viable)"
            elif d.approved:
                tag = "APPROVED"
            else:
                tag = "DENIED  "
            print(f"  {d.technique:12s} {tag:20s} decided_by={d.decided_by}")
            print(f"               reason: {d.reason}")

    approved_pairs = report.approved_pairs()
    print("\n" + "=" * 78)
    print(f"SUMMARY: {len(approved_pairs)} (agent, technique) pairs approved for activation:")
    for agent, technique in approved_pairs:
        print(f"  - {agent}.{technique}")
    denied_viable = [d for d in report.decisions if d.viable and not d.approved]
    print(f"\n{len(denied_viable)} viable pairs denied by policy:")
    for d in denied_viable:
        print(f"  - {d.agent}.{d.technique}: {d.reason}")
    print("=" * 78)


if __name__ == "__main__":
    from discovery.discovery_engine import scan_this_machine

    policy_path = sys.argv[1] if len(sys.argv) > 1 else str(
        Path(__file__).resolve().parent / "sample_policy.json"
    )
    policy = PolicyConfig.load(policy_path)
    engine = PolicyEngine(policy)

    discovery_report = scan_this_machine()
    decision_report = engine.evaluate(discovery_report)

    _print_decision_report(decision_report)
    print("\nJSON:\n")
    print(json.dumps(decision_report.to_dict(), indent=2, default=str))
