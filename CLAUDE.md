# Project Signal

Signal is an **agent governance tool** that governs the behaviour of **coding agents** running
across 5 coding tools (claude-code, cursor, codex, copilot, antigravity). Telemetry is captured by
**collectors**, normalized into **observables** (the DB tables), used to compute **signals/metrics**,
which feed **COPs** that interpret each metric in context and decide **actions**.

## Project bible — read first

The authoritative vision, architecture, dependency chain, evolution model (DPI), UI design, and
build plan are captured **strictly from the team transcripts** in the document below. Treat it as
the source of truth for *how the project should behave and where we are heading*. If a decision
contradicts it, update that document.

@docs/PROJECT_SIGNAL_VISION.md

## Decision log — check before non-trivial or hard-to-reverse changes

Important, cross-cutting, or hard-to-reverse decisions are recorded in the decision log below.
Consult it before changing the DB schema, upload mode, capture/curation logic, or anything a past
session already settled. Add a new entry when you make such a decision.

@docs/DECISIONS.md

## The one rule that constrains everything

The pipeline is a strict one-directional chain — each stage derives only from the one below it:

```
collectors → collections (raw data) → observables → signals/metrics → COPs → actions
```

**Only a behavioural question may trigger new data collection.** Observables/signals/metrics can
only be built from what the stage below already supports. See the vision doc, §2–§3.

## Current focus

Closing the **metrics data-availability gap** (not a functionality gap): generating **scenario/task
prompts run by real coding-agent sessions** so agents exhibit the behaviours our metrics measure,
producing the telemetry → observables → so gap metrics compute and land in the database. See the
vision doc, §4 and §13.
