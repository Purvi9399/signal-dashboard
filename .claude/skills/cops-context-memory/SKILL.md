---
name: cops-context-memory
description: Maintain or extend the Context & Memory COP package (collector/cops/packages/context_memory) in signal-dashboard; use when changing memory retrieval, request fields, scope or tenant isolation, provenance checks, relevance, duplicates, the retrieval bundle, how COPs read memory (graph.retrieve_memory, tools._memory_lookup), its labels, harness or ground truth.
---

# Context & Memory COP package

These are maintenance instructions for Claude Code. They are not the runtime's source of truth. The runtime reads
`collector/cops/packages/context_memory/METRICS.yaml`, validated by `cop_package.validate`.

This is COP 12 in the build order. The workbook and `docs/cops/cop_catalogue.json` number it 4. Never infer build
order from that numbering.

## What this COP is

A dedicated memory retrieval and routing COP (purvi, 2026-11-28). It answers "What previously recorded information
is relevant to the requesting COP's current question, and can that information be retrieved reliably with its
provenance intact?" It detects nothing: no context loss, contradiction, drift, anomaly, violation, incident or
risk. The previous contract is kept for comparison (`METRICS.yaml previous_contract`,
`rules._context_memory_previous_contract`, never called; `DECISIONS.md`).

## Where things are

- **Contract:** `METRICS.yaml`: request fields, provenance required and expected fields, status support, event
  identity keys, standings, questions CM-1 to CM-4, `retrieval_status` to label, `label_order`, severity,
  confidence, responses, `model_role: none`, `operations` (all deterministic), `unavailable_operations`, the
  15-metric mapping (none read) and `previous_contract`.
- **Explanations:** `SPEC.md`, `INTERPRETATION.md`, `MEMORY.md`, `CONNECTIONS.md`, `DECISIONS.md` (D-CM1 to D-CM20,
  P-CM1, P-CM2, U-CM1 to U-CM9).
- **Ground truth:** `GROUND_TRUTH.yaml` (35 scenarios, fixture records in its header). The harness is
  `harness.py`; `harness.RECORDS` and `harness.seed` build the fixtures.
- **Retrieval:** `collector/cops/runtime/cop_package.py`, Context & Memory section: `cm_request`, `cm_scope`,
  `cm_provenance`, `cm_relevance`, `retrieve_memory`, `_cm_finish`, `runtime_request`, `bundle_view`,
  `memory_buckets`, `assess_context_memory`, `decide_context_memory`.
- **Gateway:** `memory.MemoryGateway.read(include_excluded=...)` and `MemoryGateway.log`.
- **Routing:** `graph.retrieve_memory` and `tools._memory_lookup` call `retrieve_memory` for every COP.
- **Rule and tool:** `rules.context_memory`, `tools.t_assess_context_memory_questions`
  (`DETERMINISTIC_TOOLS["context_memory"]`).

## Rules that must hold

1. Reuse MemoryGateway and its store. No second memory database, store or persistence pipeline.
2. Authorization is the requesting COP's own `memory.POLICIES`; the gateway enforces tenant, type, agent scope,
   expiry and rejection. Another tenant's record is never returned, listed or counted.
3. Retrieval writes no memory record and changes no stored record. Every retrieval writes one access-log row.
4. A record's `validation_status` is returned unchanged. Standing comes only from status and provenance; a payload
   field never sets either. Missing provenance is named, never filled in.
5. Only ESTABLISHED records of another COP may corroborate. A requester's own record is OWN_EARLIER_OUTPUT.
6. Missing memory is never evidence of absence. Storage failure, denial and invalid requests are MEMORY_UNAVAILABLE,
   never NO_RELEVANT_MEMORY.
7. Relevance is deterministic. Do not add model or semantic retrieval without an approved contract (U-CM4).
8. Never produce CONTEXT_OK, STALE, CONTRADICTORY, CONTEXT_LOSS or PRECEDENT_FOUND. Do not add an adapter that would
   fabricate them for Behavior Analytics (D-CM6, P-CM1).
9. Action NONE, severity NONE. Nothing is enforced.
10. Never edit GROUND_TRUTH.yaml to match an output, and never weaken another COP's accepted ground truth.

## Checks to run after a change

```
cd collector && python3 -m cops.packages.context_memory.harness
python3 -m pytest tests/test_cop_package_context_memory.py tests/test_runtime_memory.py -q
```

A change to retrieval reaches every COP's memory reads, so then run every accepted COP harness, the full suite, the
evaluation and the bench, sequentially. After the evaluation and bench, restore `docs/cops/evaluation/RESULTS.md`,
`results.json` and `test_bench_results.json` with git checkout.

## Open decisions

U-CM1 (workbook questions), U-CM2 (owner of context-loss detection), U-CM3 (memory Q metrics), U-CM4 (semantic
retrieval), U-CM5 (LLM_COPS allocation), U-CM6 (confidence), U-CM7 (provenance fields and event identity keys),
U-CM8 (retrieval limit), U-CM9 (own precedent in VS-01). Proposals P-CM1 (BA-4 migration) and P-CM2 (workbook
recipients).
