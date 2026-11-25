"""
Activation Engine: the wiring step every earlier component in this package
deliberately deferred. Takes a `policy.policy_engine.PolicyDecisionReport`
(itself built from a real `discovery.discovery_engine.DiscoveryReport`) and
actually instantiates, registers, and activates the approved real
collectors into a live `core.registry.CollectorRegistry`.

SCOPE (completes the find -> decide -> activate chain):
  - Discovery Engine: finds what's installed/viable. Does not decide, does
    not activate.
  - Policy Engine: decides what's approved. Does not discover, does not
    activate.
  - Activation Engine (this file): activates what's approved. Does not
    discover (reuses Discovery Engine's own agent/technique -> collector
    class map rather than re-deriving it) and does not decide (only ever
    acts on `PolicyDecisionReport.approved()` -- it has no policy logic of
    its own and cannot override a denial).

This is the first component in the chain that has real side effects by
design -- registering and activating collectors is the whole point. Safety
here means something different from "read-only": it means never silently
reintroducing a real live-process-spawn hazard. Reading every CLI Wrapper
collector's actual `activate()` body (not just the one hazard
`claude_code_cli_wrapper.py` names explicitly in its own docstring) found
three, not one: `claude_code_cli_wrapper.py`, `copilot_cli_wrapper.py`, and
`antigravity_cli_wrapper.py` all fork+exec a real child process if
constructed with their replay parameter left at its `None` default.
`codex_cli_wrapper.py` and `cursor_cli_wrapper.py` are, by contrast,
confirmed non-hazardous -- both are explicitly documented, unimplemented
live-mode placeholders whose `activate()` only sets `self._activated =
True`, with no spawn logic to trigger either way.
`discovery_engine.py`'s `LIVE_MODE_HAZARD_COLLECTORS` set (imported below,
not redefined here) now reflects all three real cases.

REPLAY-SAFE CONSTRUCTION, GENERICALLY:
Every one of the 28 collectors' constructors was inspected (see
`_replay_kwargs_for`). Whenever a constructor parameter's name suggests it
takes a *list* of replayable records (`replay_events`, `replay_records`,
`replay_flows` -- confirmed the naming convention across every collector in
this package, including all five CLI Wrapper collectors), this module
passes an explicit value (real data if supplied via `replay_data=`,
otherwise an explicit empty list) instead of ever leaving it at its
`None` default. This is what actually neutralizes the live-spawn hazard for
the three collectors above: their `activate()` checks `if
self._replay_events is None: <spawn live process>`, so `replay_events=[]`
(empty list, not `None`) reliably keeps them in inert replay mode.
Parameters that take a single optional replay object instead of a list
(`replay_path`, e.g. `antigravity_local_hooks.py` /
`antigravity_fs_watcher.py`) are deliberately left at their `None` default
-- their own `activate()` was read and confirmed to fail closed (raise a
caught `RuntimeError` asking for the `watchdog` package) rather than spawn
anything when unreplayed, so there is no equivalent hazard to neutralize
there.

`mode="live"` exists for a real deployment (bare, default construction --
what a real machine's registry would actually want) but requires the
caller to explicitly pass `allow_live_hazards=True` before this module
will construct any of the three real hazard collectors that way; otherwise
it raises rather than silently allowing a live child process to spawn.
"""

from __future__ import annotations

import importlib
import inspect
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import Collector, CollectorRegistry
from discovery.discovery_engine import AGENT_TECHNIQUE_COLLECTORS, LIVE_MODE_HAZARD_COLLECTORS

try:
    from policy.policy_engine import PolicyDecisionReport, TechniqueDecision
except ImportError:  # pragma: no cover - only used for type hints
    PolicyDecisionReport = Any  # type: ignore
    TechniqueDecision = Any  # type: ignore


# Constructor parameter names, across all 28 collectors, that take a LIST of
# replayable records (as opposed to a single optional replay object like
# `replay_path`). Confirmed by inspecting every collector's __init__.
_LIST_REPLAY_PARAM_MARKERS = ("events", "records", "flows")


@dataclass
class WiringResult:
    """What actually happened when `ActivationEngine.wire()` ran, per
    approved pair -- registered, activated, skipped (no collector built for
    this pair), or errored during construction. Mirrors the same
    traceability posture as `PolicyDecisionReport`."""

    registered: List[str] = field(default_factory=list)
    activated: List[str] = field(default_factory=list)
    skipped: List[Tuple[str, str, str]] = field(default_factory=list)
    construction_errors: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "registered": list(self.registered),
            "activated": list(self.activated),
            "skipped": [{"agent": a, "technique": t, "reason": r} for a, t, r in self.skipped],
            "construction_errors": list(self.construction_errors),
        }


class ActivationEngine:
    """Wires a PolicyDecisionReport's approved pairs into a live
    CollectorRegistry.

    Usage:
        registry, wiring_result = ActivationEngine(mode="replay", replay_data={
            ("claude_code", "hooks"): [...real captured hook payloads...],
        }).wire(decision_report)

        events = list(registry.merged_capture())
        registry.deactivate_all()

    `mode="replay"` (the default) is the only safe mode for automated/CI/
    demo contexts: every collector is constructed with its replay-shaped
    constructor argument explicitly set (real data if supplied, otherwise
    an explicit empty list) rather than left at its live-mode default.

    `mode="live"` constructs collectors with their bare default arguments
    -- appropriate for an actual deployed machine, where a collector's
    whole job is to listen to something real. It will refuse to construct
    any of the four documented CLI Wrapper hazard collectors
    (`LIVE_MODE_HAZARD_COLLECTORS`, sourced from `discovery_engine.py`
    rather than redefined here) unless `allow_live_hazards=True` is passed
    explicitly.
    """

    def __init__(
        self,
        mode: str = "replay",
        replay_data: Optional[Dict[Tuple[str, str], List[dict]]] = None,
        allow_live_hazards: bool = False,
    ) -> None:
        if mode not in ("replay", "live"):
            raise ValueError(f"mode must be 'replay' or 'live', got {mode!r}")
        self._mode = mode
        self._replay_data = replay_data or {}
        self._allow_live_hazards = allow_live_hazards

    def wire(self, policy_report: "PolicyDecisionReport") -> Tuple[CollectorRegistry, WiringResult]:
        """Registers a real collector instance for every approved+viable
        pair, then calls the registry's own `activate_detected()` -- which
        re-runs each collector's real `detect()` right now (not reusing
        Discovery Engine's earlier, possibly-stale scan) and only
        `activate()`s the ones still true, with the registry's existing
        per-collector failure isolation. This means a pair Policy Engine
        approved a few minutes ago that's since disappeared (e.g. the
        agent was uninstalled) simply won't activate -- it does not raise,
        it's just absent from `WiringResult.activated`.
        """
        registry = CollectorRegistry()
        result = WiringResult()

        for decision in policy_report.approved():
            pair = (decision.agent, decision.technique)
            entry = AGENT_TECHNIQUE_COLLECTORS.get(decision.agent, {}).get(decision.technique)
            if entry is None:
                result.skipped.append(
                    (decision.agent, decision.technique, "no collector implementation exists for this pair")
                )
                continue

            module_name, class_name = entry
            try:
                instance = self._construct(pair, module_name, class_name)
            except Exception as exc:  # noqa: BLE001 - isolate per-pair construction failures
                result.construction_errors.append(
                    f"{decision.agent}.{decision.technique}: {type(exc).__name__}: {exc}"
                )
                continue

            registry.register(instance)
            result.registered.append(instance.collector_id)

        result.activated = registry.activate_detected()
        return registry, result

    def _construct(self, pair: Tuple[str, str], module_name: str, class_name: str) -> Collector:
        module = importlib.import_module(module_name)
        cls = getattr(module, class_name)

        if self._mode == "live":
            if pair in LIVE_MODE_HAZARD_COLLECTORS and not self._allow_live_hazards:
                raise RuntimeError(
                    f"{pair}: refusing default (live-mode) construction -- this collector's "
                    f"activate() spawns a real child process when unreplayed. Pass "
                    f"allow_live_hazards=True to ActivationEngine to override, only after "
                    f"reading the hazard note in README.md."
                )
            return cls()

        # replay mode: never leave a list-shaped replay parameter at its
        # None default -- explicit empty list (or real supplied data)
        # every time, which is what actually keeps activate() inert for
        # every CLI Wrapper collector (see module docstring).
        kwargs = self._replay_kwargs_for(cls, pair)
        return cls(**kwargs)

    def _replay_kwargs_for(self, cls: type, pair: Tuple[str, str]) -> Dict[str, Any]:
        supplied = self._replay_data.get(pair, [])
        kwargs: Dict[str, Any] = {}
        for name in inspect.signature(cls.__init__).parameters:
            if name == "self":
                continue
            if name.startswith("replay") and any(marker in name for marker in _LIST_REPLAY_PARAM_MARKERS):
                kwargs[name] = supplied
        return kwargs


def _print_wiring_result(result: WiringResult) -> None:
    print(f"registered ({len(result.registered)}): {result.registered}")
    print(f"activated  ({len(result.activated)}): {result.activated}")
    if result.skipped:
        print(f"skipped ({len(result.skipped)}):")
        for agent, technique, reason in result.skipped:
            print(f"  - {agent}.{technique}: {reason}")
    if result.construction_errors:
        print(f"construction_errors ({len(result.construction_errors)}):")
        for err in result.construction_errors:
            print(f"  - {err}")
