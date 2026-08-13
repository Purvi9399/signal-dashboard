# Project Signal — Collector Framework Architecture

This document describes what was built for the Deployment phase's "Build
the package" step: a chain of independently-testable components that
find, decide, and activate telemetry collection for AI coding agents, then
keep itself updated, all packaged into one deployable executable.

## High-level pipeline

```
                    ┌─────────────────────────────────────────────────────┐
                    │                  core/interfaces.py                  │
                    │   Collector ABC: detect() -> activate() -> capture() │
                    │   -> deactivate()          RawEvent (raw, unmodified)│
                    └─────────────────────────────────────────────────────┘
                                          ▲
                                          │ implements
                    ┌─────────────────────────────────────────────────────┐
                    │              collectors/<agent>/*.py                 │
                    │   28 real collectors: 5 agents x up to 6 techniques  │
                    │   (Hooks, OTel, CLI Wrapper, MCP Proxy, FS Watcher,  │
                    │    Local Hooks) -- only where real evidence exists   │
                    └─────────────────────────────────────────────────────┘
                                          ▲
                                          │ detect()-only (never activate())
                    ┌─────────────────────────────────────────────────────┐
                    │            discovery/discovery_engine.py             │
   FINDS             │  "What's installed? Which techniques are viable?"   │
                    │  -> DiscoveryReport { AgentDiscovery [TechniqueResult]}
                    └─────────────────────────────────────────────────────┘
                                          │ DiscoveryReport
                                          ▼
                    ┌─────────────────────────────────────────────────────┐
                    │              policy/policy_engine.py                 │
   DECIDES           │  "What's approved to run, and why?"                 │
                    │  policy config (JSON, default-deny) + DiscoveryReport│
                    │  -> PolicyDecisionReport { TechniqueDecision }       │
                    └─────────────────────────────────────────────────────┘
                                          │ PolicyDecisionReport.approved()
                                          ▼
                    ┌─────────────────────────────────────────────────────┐
                    │            activation/activation_engine.py           │
   ACTIVATES         │  Constructs + registers + activates approved pairs  │
                    │  into a real CollectorRegistry (replay-safe or live) │
                    │  -> (CollectorRegistry, WiringResult)                │
                    └─────────────────────────────────────────────────────┘
                                          │ registers into
                                          ▼
                    ┌─────────────────────────────────────────────────────┐
                    │                core/registry.py                     │
                    │  CollectorRegistry: run_detection, activate_detected,│
                    │  merged_capture (round-robin), deactivate_all --     │
                    │  isolates per-collector failures everywhere          │
                    └─────────────────────────────────────────────────────┘

    ─────────────────────────  orthogonal to the above  ─────────────────────

                    ┌─────────────────────────────────────────────────────┐
                    │              update/update_engine.py                 │
   KEEPS CURRENT     │  snapshot (hash) -> diff -> apply (w/ backup)       │
                    │  -> verify (real DiscoveryEngine scan) -> rollback   │
                    │  Manages the package's own files on disk, unrelated │
                    │  to what's currently activated.                     │
                    └─────────────────────────────────────────────────────┘

    ───────────────────────────  packaging  ──────────────────────────────

                    ┌─────────────────────────────────────────────────────┐
                    │        package/cli.py  ("signal-agent" CLI)          │
                    │  scan | decide | run | version | update ...          │
                    │  thin argparse wrapper -- zero new logic             │
                    └─────────────────────────────────────────────────────┘
                                          │ frozen by
                                          ▼
                    ┌─────────────────────────────────────────────────────┐
                    │      package/build_exe.py  (PyInstaller)             │
                    │  -> dist/signal-agent (macOS, built locally)         │
                    │  -> .github/workflows/build-exe.yml (CI matrix:      │
                    │     windows-latest + macos-latest, builds + smoke-   │
                    │     tests both, not yet run -- repo not pushed)      │
                    └─────────────────────────────────────────────────────┘
```

## The five architectural principles enforced throughout

1. **Strict single-responsibility boundary at every stage.**
   Discovery finds and reports — never activates. Policy decides and
   reports — never discovers or activates. Activation only wires up what
   Policy already approved — it has no policy logic of its own and cannot
   override a denial. Update only manages files on disk — it has no
   opinion about what's currently running. Each stage consumes only the
   previous stage's *output data structure*, never its internals.

2. **Nothing is ever fabricated.**
   If an agent isn't installed, Discovery reports "not found," not a
   simulated positive. If no evidence exists that a technique works for
   an agent (e.g. Cursor has no OTel signal), it's left out rather than
   stubbed. Every component was tested for real against this actual
   machine, not against synthetic examples.

3. **Every decision is traceable, never a bare boolean.**
   `TechniqueResult` (viable + why), `TechniqueDecision` (approved +
   `decided_by` + reason), `WiringResult` (registered/activated/
   skipped/errored, each with a reason), `UpdateDiff`/`ApplyResult`
   (exactly which files, never silently dropped). This is a governance
   product — decisions have to be auditable, not just enforced.

4. **Safety is structural, not just documented.**
   `detect()` is contractually side-effect-free (enforced by convention +
   verified by reading every collector's implementation), which is what
   makes it safe for Discovery Engine to call it on all 28 collectors
   speculatively. Known live-process-spawn hazards
   (`LIVE_MODE_HAZARD_COLLECTORS`, defined once in `discovery_engine.py`,
   reused by `activation_engine.py`) are blocked by default and require
   an explicit `allow_live_hazards=True` override. Update Engine never
   auto-deletes files and always backs up before overwriting.

5. **Failure in one part never takes down the whole system.**
   `CollectorRegistry` wraps every lifecycle call (`detect`, `activate`,
   `capture`, `deactivate`) per-collector in try/except, recording
   failures without stopping other collectors. `DiscoveryEngine.scan()`
   does the same per-agent. This pattern repeats at every layer.

## Data flow summary (types, not just boxes)

```
DiscoveryReport
  └── agents: [AgentDiscovery]
        ├── agent, installed, confidence, install_paths
        └── technique_results: [TechniqueResult]
              ├── technique, viable, collector_id
              └── note / error

        ↓ consumed by PolicyEngine.evaluate()

PolicyDecisionReport
  └── decisions: [TechniqueDecision]
        ├── agent, technique, viable, approved
        └── reason, decided_by ("agent_rule" | "technique_rule" | "default" | "not_viable")

        ↓ .approved() consumed by ActivationEngine.wire()

(CollectorRegistry, WiringResult)
  └── WiringResult: registered, activated, skipped, construction_errors

        ↓ registry.merged_capture() → Iterator[RawEvent]
```

## Repository layout

```
collector_framework/
├── core/                # Collector ABC, RawEvent, CollectorRegistry
├── collectors/<agent>/  # 28 real per-agent/technique implementations
├── discovery/           # Discovery Engine
├── policy/              # Policy Engine + sample_policy.json
├── activation/          # Activation Engine
├── update/              # Update Engine
├── package/             # signal-agent CLI + PyInstaller build script
├── demos/               # runnable, real-output demo scripts per component
├── dist/                # frozen executable (macOS build present)
└── README.md            # full component-by-component documentation

.github/workflows/
└── build-exe.yml        # CI matrix build: windows-latest + macos-latest
```

## What's deliberately NOT in this architecture (out of scope, by design)

- **Normalization** (mapping Claude's `hook_event_name` and Cursor's
  `event_type` onto one canonical schema) — a separate downstream stage
  that consumes `RawEvent`, not built here.
- **Redaction / PII scrubbing** — explicitly why MCP Proxy is denied by
  default in the sample policy; this is future work, not silently assumed
  to already exist.
- **Domain-based access control** (who on the team sees which events) — a
  separate database/access-layer concern, unrelated to whether a
  technique is allowed to *run* on a machine.
- **Scheduling / continuous execution** — Discovery/Update Engine's
  `scan()`/`snapshot()` are designed to be *called* periodically by a
  Continuous Ops layer; neither self-schedules today.
