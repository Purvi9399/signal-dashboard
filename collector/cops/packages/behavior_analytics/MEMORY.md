# Behavior Analytics: memory

`memory.POLICIES["behavior_analytics"]`, unchanged: writes behaviour_baseline; reads behaviour_baseline,
context_integrity_finding and workflow_failure; agent scope. Every read and write goes through the memory gateway.

## Write

The shared step (`graph.validate_memory_update`) writes a behaviour_baseline record for a verified substantive
reading that is not quiet. STABLE is quiet and is not written, as before (U-BA5). The payload (`memory_payload`)
holds the four answers, the time scope, the recounted events (raw and distinct counts) and the pattern metric
states. A record starts VERIFIED or UNVALIDATED; only an independent reviewer's feedback makes it VALIDATED.

## Read

`tools.assess_behaviour_questions` reads the established records of the three types for this agent
(`_memory_lookup`). BA-3 lists them as precedents with `counted: false`.

A precedent never:
- changes an answer, the label, the severity, the confidence or the action;
- counts as an earlier occurrence in BA-3. It is this COP's own earlier classification, and history must not
  validate its own classifications (D-BA10).

Tested: a validated LOOPING precedent for a session outside the estate leaves an isolated anomaly ISOLATED and a
stable session STABLE, identical with and without memory.
