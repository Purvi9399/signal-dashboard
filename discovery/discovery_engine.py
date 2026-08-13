"""
Discovery Engine: standalone, independent machine-scanning component for
Project Signal's Deployment phase.

SCOPE: this module answers two questions, and two questions only:
  1. "Which of the five target agents (Claude Code, Cursor, GitHub Copilot,
     Antigravity, Codex) are actually installed on THIS machine right now?"
  2. "For each agent found, which of the six collection techniques (Hooks,
     OpenTelemetry, CLI Wrapper, MCP Proxy, FS Watcher, Local Hooks) are
     viable on THIS machine right now?"

It does NOT register anything into a CollectorRegistry, does NOT call
activate() on anything, and does NOT write/create/modify anything on disk.
Every check in this file is read-only: directory/file existence checks,
`shutil.which()` PATH lookups, and (for technique viability) calling the
already-built-and-vetted `detect()` method on a freshly-constructed,
never-activated instance of the real collector class for that
agent/technique pair. Wiring a DiscoveryReport's findings into an actual
CollectorRegistry (deciding which collectors to instantiate-for-real and
register) is a deliberately separate, later step -- not implemented here.

WHY REUSE THE REAL COLLECTORS' OWN detect() INSTEAD OF REIMPLEMENTING
DETECTION HEURISTICS FROM SCRATCH:
Every one of the 28 collectors in this package already has a detect() that
is, per core.py's contract, "cheap, side-effect-free... may be called
speculatively/often." Those detect() implementations are the real, already
empirically-grounded source of truth for what "this technique is viable for
this agent" means (binary-on-PATH checks, config-file schema checks, hook
dependency checks like Codex's trust-approval gate, etc.) -- reimplementing
that logic here would just create a second, driftable copy of the same
knowledge. Instead, this module imports each collector class, constructs it
with its default (side-effect-free-on-__init__) constructor -- never with
a bare live-mode default for collectors documented as dangerous to
construct-and-activate live (see claude_code_cli_wrapper.py's README-
documented hazard) -- and calls ONLY detect(), never activate(). This is
safe: every constructor in this package was verified (by reading each
collector's __init__) to do nothing beyond storing its (all-optional,
all-default-None/True) arguments; the only side-effecting lifecycle method
is activate(), which this module never calls.

INDEPENDENCE FROM CollectorRegistry:
CollectorRegistry.run_detection() only iterates over collectors that a
caller has already explicitly register()'d -- it has no way to discover an
agent/technique combination nobody thought to register in advance. This
module keeps its own static map (AGENT_TECHNIQUE_COLLECTORS below) of every
agent/technique pair this package has a real collector class for, and an
entirely separate, collector-independent "is this agent installed at all"
scan (see _detect_agent_installation) that doesn't require ANY collector
class to exist for an agent to still be reported as installed (just with an
empty/best-effort viable_techniques list). This means adding a 29th
collector later only requires one new entry in the map below -- no changes
to how installation itself is detected.
"""

from __future__ import annotations

import importlib
import json
import os
import shutil
import sys
import traceback
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

AGENT_NAMES = ["claude_code", "cursor", "copilot", "antigravity", "codex"]
TECHNIQUES = ["hooks", "otel", "cli_wrapper", "mcp_proxy", "fs_watcher", "local_hooks"]

# Static map: (agent, technique) -> (module_name, class_name).
# Mirrors exactly the 28 real collector files/classes in this folder.
# Deliberately has NO entry for (claude_code, local_hooks) or
# (cursor, local_hooks) -- per README.md, those were never built because
# they'd just duplicate the existing Hooks collector for those two agents.
AGENT_TECHNIQUE_COLLECTORS: Dict[str, Dict[str, tuple]] = {
    "claude_code": {
        "hooks": ("collectors.claude_code.claude_code_hooks", "ClaudeCodeHooksCollector"),
        "otel": ("collectors.claude_code.claude_code_otel", "ClaudeCodeOTelCollector"),
        "cli_wrapper": ("collectors.claude_code.claude_code_cli_wrapper", "ClaudeCodeCLIWrapperCollector"),
        "mcp_proxy": ("collectors.claude_code.claude_code_mcp_proxy", "ClaudeCodeMCPProxyCollector"),
        "fs_watcher": ("collectors.claude_code.claude_code_fs_watcher", "ClaudeCodeFSWatcherCollector"),
    },
    "cursor": {
        "hooks": ("collectors.cursor.cursor_hooks", "CursorHooksCollector"),
        "otel": ("collectors.cursor.cursor_otel", "CursorOTelCollector"),
        "cli_wrapper": ("collectors.cursor.cursor_cli_wrapper", "CursorCLIWrapperCollector"),
        "mcp_proxy": ("collectors.cursor.cursor_mcp_proxy", "CursorMCPProxyCollector"),
        "fs_watcher": ("collectors.cursor.cursor_fs_watcher", "CursorFSWatcherCollector"),
    },
    "copilot": {
        "hooks": ("collectors.copilot.copilot_hooks", "CopilotHooksCollector"),
        "otel": ("collectors.copilot.copilot_otel", "CopilotOTelCollector"),
        "cli_wrapper": ("collectors.copilot.copilot_cli_wrapper", "CopilotCLIWrapperCollector"),
        "mcp_proxy": ("collectors.copilot.copilot_mcp_proxy", "CopilotMCPProxyCollector"),
        "fs_watcher": ("collectors.copilot.copilot_fs_watcher", "CopilotFSWatcherCollector"),
        "local_hooks": ("collectors.copilot.copilot_local_hooks", "CopilotLocalHooksCollector"),
    },
    "antigravity": {
        "hooks": ("collectors.antigravity.antigravity_hooks", "AntigravityHooksCollector"),
        "otel": ("collectors.antigravity.antigravity_otel", "AntigravityOTelCollector"),
        "cli_wrapper": ("collectors.antigravity.antigravity_cli_wrapper", "AntigravityCLIWrapperCollector"),
        "mcp_proxy": ("collectors.antigravity.antigravity_mcp_proxy", "AntigravityMCPProxyCollector"),
        "fs_watcher": ("collectors.antigravity.antigravity_fs_watcher", "AntigravityFSWatcherCollector"),
        "local_hooks": ("collectors.antigravity.antigravity_local_hooks", "AntigravityLocalHooksCollector"),
    },
    "codex": {
        "hooks": ("collectors.codex.codex_hooks", "CodexHooksCollector"),
        "otel": ("collectors.codex.codex_otel", "CodexOTelCollector"),
        "cli_wrapper": ("collectors.codex.codex_cli_wrapper", "CodexCLIWrapperCollector"),
        "mcp_proxy": ("collectors.codex.codex_mcp_proxy", "CodexMCPProxyCollector"),
        "fs_watcher": ("collectors.codex.codex_fs_watcher", "CodexFSWatcherCollector"),
        "local_hooks": ("collectors.codex.codex_local_hooks", "CodexLocalHooksCollector"),
    },
}

# Collectors whose activate() -- confirmed by reading every CLI Wrapper
# collector's actual method body, not just claude_code_cli_wrapper.py's
# README-documented case -- forks+execs a REAL child process when
# constructed with its replay parameter left at the None default:
# claude_code_cli_wrapper.py and antigravity_cli_wrapper.py always do this
# unreplayed; copilot_cli_wrapper.py does too (its activate() calls
# self._spawn_and_pump() when self._replay_events is None, same pattern,
# just not previously called out by name outside its own docstring).
# codex_cli_wrapper.py and cursor_cli_wrapper.py are, by contrast,
# confirmed NOT hazardous: both are explicitly documented, unimplemented
# live-mode placeholders (their activate() only sets self._activated=True,
# no spawn logic exists to trigger either way).
# We still construct+detect() every one of these five (their __init__ is
# verified side-effect-free), but we never call activate() anywhere in
# this file, and we flag the three real hazards explicitly in the
# per-technique note for operator awareness.
LIVE_MODE_HAZARD_COLLECTORS = {
    ("claude_code", "cli_wrapper"),
    ("copilot", "cli_wrapper"),
    ("antigravity", "cli_wrapper"),
}


def _which(binary: str) -> Optional[str]:
    return shutil.which(binary)


def _detect_agent_installation(agent: str) -> Dict[str, Any]:
    """Collector-independent "is this agent installed at all" scan.

    Deliberately does not import or instantiate any Collector subclass --
    this must work even for an agent with zero collectors built for it.
    Read-only: directory/file existence + PATH lookups only, no writes,
    no subprocess spawns, no network calls.
    """
    home = Path.home()
    vscode_ext_dir = home / ".vscode" / "extensions"
    signals: Dict[str, Any] = {}
    install_paths: Dict[str, str] = {}

    def note_path(label: str, path: Path) -> bool:
        exists = path.exists()
        if exists:
            install_paths[label] = str(path)
        return exists

    if agent == "claude_code":
        signals["binary_on_path"] = _which("claude") is not None
        if signals["binary_on_path"]:
            install_paths["claude_binary"] = _which("claude")
        signals["config_dir"] = note_path("claude_home", home / ".claude")
        signals["config_file"] = note_path("claude_json", home / ".claude.json")

    elif agent == "cursor":
        signals["binary_on_path"] = _which("cursor") is not None
        if signals["binary_on_path"]:
            install_paths["cursor_binary"] = _which("cursor")
        signals["mac_app_bundle"] = note_path(
            "cursor_app_bundle", Path("/Applications/Cursor.app")
        )
        signals["config_dir"] = note_path("cursor_home", home / ".cursor")

    elif agent == "copilot":
        signals["binary_on_path"] = _which("copilot") is not None
        if signals["binary_on_path"]:
            install_paths["copilot_binary"] = _which("copilot")
        signals["config_dir"] = note_path("copilot_home", home / ".copilot")
        ext_found = False
        if vscode_ext_dir.is_dir():
            try:
                for entry in vscode_ext_dir.iterdir():
                    if entry.name.lower().startswith("github.copilot"):
                        ext_found = True
                        install_paths["vscode_extension"] = str(entry)
                        break
            except OSError:
                pass
        signals["vscode_extension"] = ext_found

    elif agent == "antigravity":
        signals["binary_on_path"] = _which("agy") is not None
        if signals["binary_on_path"]:
            install_paths["agy_binary"] = _which("agy")
        signals["mac_app_bundle"] = note_path(
            "antigravity_app_bundle", Path("/Applications/Antigravity.app")
        )
        signals["config_dir"] = note_path(
            "gemini_config_dir", home / ".gemini" / "config"
        )

    elif agent == "codex":
        signals["binary_on_path"] = _which("codex") is not None
        if signals["binary_on_path"]:
            install_paths["codex_binary"] = _which("codex")
        signals["config_dir"] = note_path("codex_home", home / ".codex")
        ext_found = False
        if vscode_ext_dir.is_dir():
            try:
                for entry in vscode_ext_dir.iterdir():
                    if entry.name.lower().startswith("openai.chatgpt"):
                        ext_found = True
                        install_paths["vscode_extension"] = str(entry)
                        break
            except OSError:
                pass
        signals["vscode_extension"] = ext_found

    installed = any(signals.values())
    return {"installed": installed, "signals": signals, "install_paths": install_paths}


def _instantiate_collector(module_name: str, class_name: str):
    """Import the module and construct the collector class with its
    default (all-optional, side-effect-free) constructor. Raises on
    failure -- caller catches and records it as a technique-level error
    rather than letting one bad import take down the whole scan."""
    module = importlib.import_module(module_name)
    cls = getattr(module, class_name)
    return cls()


@dataclass
class TechniqueResult:
    technique: str
    viable: bool
    collector_id: Optional[str] = None
    note: Optional[str] = None
    error: Optional[str] = None


@dataclass
class AgentDiscovery:
    agent: str
    installed: bool
    confidence: float
    install_paths: Dict[str, str] = field(default_factory=dict)
    installation_signals: Dict[str, bool] = field(default_factory=dict)
    viable_techniques: List[str] = field(default_factory=list)
    technique_results: List[TechniqueResult] = field(default_factory=list)
    version_if_known: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        return d


@dataclass
class DiscoveryReport:
    agents: List[AgentDiscovery] = field(default_factory=list)
    scan_errors: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "agents": [a.to_dict() for a in self.agents],
            "scan_errors": list(self.scan_errors),
        }

    def found_agents(self) -> List[AgentDiscovery]:
        return [a for a in self.agents if a.installed]


class DiscoveryEngine:
    """Standalone, registry-independent machine scanner.

    Usage:
        engine = DiscoveryEngine()
        report = engine.scan()

    `scan()` is safe to call repeatedly (e.g. from a Continuous Ops health
    check) -- it performs only read-only filesystem/PATH inspection and
    construct-then-detect() calls against never-activated collector
    instances. It never registers or activates anything.
    """

    def __init__(self, agents: Optional[List[str]] = None) -> None:
        self._agents = agents or list(AGENT_NAMES)

    def scan(self) -> DiscoveryReport:
        report = DiscoveryReport()
        for agent in self._agents:
            try:
                report.agents.append(self._scan_agent(agent))
            except Exception as exc:  # noqa: BLE001 - isolate per-agent failures
                report.scan_errors.append(
                    f"{agent}: unexpected error during scan: "
                    f"{type(exc).__name__}: {exc}"
                )
        return report

    def _scan_agent(self, agent: str) -> AgentDiscovery:
        install_info = _detect_agent_installation(agent)
        installed = install_info["installed"]
        signals = install_info["signals"]
        install_paths = install_info["install_paths"]

        technique_results: List[TechniqueResult] = []
        viable_techniques: List[str] = []

        technique_map = AGENT_TECHNIQUE_COLLECTORS.get(agent, {})
        for technique in TECHNIQUES:
            entry = technique_map.get(technique)
            if entry is None:
                technique_results.append(
                    TechniqueResult(
                        technique=technique,
                        viable=False,
                        note=(
                            "no collector implemented for this agent/technique "
                            "pair in this package (see README.md)"
                        ),
                    )
                )
                continue

            module_name, class_name = entry
            note = None
            if (agent, technique) in LIVE_MODE_HAZARD_COLLECTORS:
                note = (
                    "constructed with default (replay_events=None) args for "
                    "detect() only; NEVER call activate() on a default-"
                    "constructed instance of this collector outside a real "
                    "live deployment -- it will spawn a live child process "
                    "(see README.md hazard note)"
                )
            try:
                instance = _instantiate_collector(module_name, class_name)
                viable = bool(instance.detect())
                technique_results.append(
                    TechniqueResult(
                        technique=technique,
                        viable=viable,
                        collector_id=instance.collector_id,
                        note=note,
                    )
                )
                if viable:
                    viable_techniques.append(technique)
                    # detect() succeeding is itself a strong installation
                    # signal, even for an agent whose broader installation
                    # scan above came up empty (e.g. a collector-specific
                    # config file existing in a location the generic scan
                    # doesn't check).
                    installed = True
            except Exception as exc:  # noqa: BLE001 - isolate per-technique failures
                technique_results.append(
                    TechniqueResult(
                        technique=technique,
                        viable=False,
                        note=note,
                        error=f"{type(exc).__name__}: {exc}",
                    )
                )

        confidence = self._confidence(signals, viable_techniques, technique_map)

        return AgentDiscovery(
            agent=agent,
            installed=installed,
            confidence=confidence,
            install_paths=install_paths,
            installation_signals=signals,
            viable_techniques=viable_techniques,
            technique_results=technique_results,
        )

    @staticmethod
    def _confidence(
        signals: Dict[str, bool],
        viable_techniques: List[str],
        technique_map: Dict[str, tuple],
    ) -> float:
        """A simple, transparent (not machine-learned) confidence score in
        [0.0, 1.0]: fraction of independent installation signals that
        fired, plus a bonus for having at least one viable technique with
        a real, already-implemented collector backing it. Intended as a
        rough triage signal for the Configuration phase, not a precise
        probability."""
        if not signals and not technique_map:
            return 0.0
        signal_score = (sum(1 for v in signals.values() if v) / len(signals)) if signals else 0.0
        technique_bonus = 0.2 if viable_techniques else 0.0
        return round(min(1.0, signal_score * 0.8 + technique_bonus), 2)


def scan_this_machine(agents: Optional[List[str]] = None) -> DiscoveryReport:
    """Convenience module-level entry point: `DiscoveryEngine(agents).scan()`."""
    return DiscoveryEngine(agents).scan()


def _print_report(report: DiscoveryReport) -> None:
    print("=" * 78)
    print("DISCOVERY ENGINE REPORT")
    print("=" * 78)
    for agent_result in report.agents:
        status = "FOUND" if agent_result.installed else "NOT FOUND"
        print(f"\n[{agent_result.agent}] {status}  (confidence={agent_result.confidence})")
        if agent_result.install_paths:
            print("  install_paths:")
            for label, path in agent_result.install_paths.items():
                print(f"    - {label}: {path}")
        else:
            print("  install_paths: (none)")
        print("  installation_signals:")
        for sig, val in agent_result.installation_signals.items():
            print(f"    - {sig}: {val}")
        print(f"  viable_techniques: {agent_result.viable_techniques or '(none)'}")
        print("  technique_results:")
        for tr in agent_result.technique_results:
            line = f"    - {tr.technique}: viable={tr.viable}"
            if tr.collector_id:
                line += f" collector_id={tr.collector_id}"
            print(line)
            if tr.note:
                print(f"        note: {tr.note}")
            if tr.error:
                print(f"        error: {tr.error}")
    if report.scan_errors:
        print("\nscan_errors:")
        for err in report.scan_errors:
            print(f"  - {err}")
    print("\n" + "=" * 78)
    found = [a.agent for a in report.found_agents()]
    print(f"SUMMARY: {len(found)}/{len(report.agents)} agents found: {found or '(none)'}")
    print("=" * 78)


if __name__ == "__main__":
    _report = scan_this_machine()
    _print_report(_report)
    print("\nJSON:\n")
    print(json.dumps(_report.to_dict(), indent=2, default=str))
