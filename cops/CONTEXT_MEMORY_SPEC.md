# Context and Memory COP — Requirements Specification

Status: Active for implementation.  
Language: ASD-STE (Standard Technical English).  
Branch: `cops/context-and-memory` (from `main`).

## 1. Purpose

Context and Memory shall decide whether the current session context is
internally consistent, still relevant, and supported by useful precedent.

It shall not judge collector liveness. That is Runtime Monitoring.  
It shall not name situation tags. That is Classification.  
It shall not reconstruct attribution. That is Audit.

## 2. Scope

### 2.1 In scope

- `cops/context_memory.py`
- One session-context lookup on `ContextProvider`
- Unit, integration, functional, and regression tests
- Offline benchmark of `evaluate`
- McCabe cyclomatic complexity ≤ 5 for new Python callables

### 2.2 Out of scope

- Runtime Monitoring, Interaction Discovery, Policy Enforcement,
  Anomaly Detection, Incident Response (colleague)
- Compute of compaction or precedent metrics (metric layer)
- `SELECT *` from events or full chat history
- LLM summary of memory
- Dashboard UI
- ALERT, BLOCK, PAUSE, or policy enforcement

## 3. Definitions

| Term | Meaning |
|---|---|
| `incomplete` | Required feature keys are missing. The metric layer does not yet emit them. |
| `context_loss` | `context_loss_after_compaction` is true. |
| `contradictory` | `context_consistency` is false. |
| `stale` | `session_reset` is true. |
| `precedent_found` | `precedent_match` is true. |
| `context_ok` | Required keys are present and none of the problem flags apply. |

## 4. Functional requirements

| ID | Requirement |
|---|---|
| FR1 | Context and Memory shall consume one `MetricEvent`. |
| FR2 | It shall read `compaction_count`, `context_loss_after_compaction`, `session_reset`, `precedent_match`, and `context_consistency` from `metric.features`. It shall not recompute them. |
| FR3 | Status shall be `incomplete` when a required feature key is absent. |
| FR4 | Status shall be `context_loss` when `context_loss_after_compaction` is true. |
| FR5 | Status shall be `contradictory` when `context_consistency` is false. |
| FR6 | Status shall be `stale` when `session_reset` is true. |
| FR7 | Status shall be `precedent_found` when `precedent_match` is true. |
| FR8 | Else status shall be `context_ok`. |
| FR9 | Status order shall be incomplete, context_loss, contradictory, stale, precedent_found, context_ok. |
| FR10 | Successful `incomplete`, `precedent_found`, and `context_ok` evaluations shall use action `NONE`. |
| FR11 | Successful `context_loss`, `contradictory`, and `stale` evaluations shall use action `WATCH`. |
| FR12 | If metric `state` is `UNKNOWN` or `NOT_APPLICABLE`, action shall be `NONE`. Do not claim certainty. |
| FR13 | `COPDecision.cop` shall be `CopType.CONTEXT_AND_MEMORY`. |
| FR14 | `WATCH` shall set `downstream_cops` to Behavior Analytics. Else empty. |
| FR15 | The COP may request one session snapshot from `ContextProvider.get_session_context(session_id)`. It shall not query a full table. |
| FR16 | The lookup shall not change status. Status comes from features only. |
| FR17 | On evaluator failure, `failed` shall be true and action shall be `ESCALATE`. |
| FR18 | Confidence shall be omitted (`None`) unless `features["confidence"]` is a number in 0.0 to 1.0. Do not invent a score. |

Required feature keys: `compaction_count`, `context_loss_after_compaction`, `session_reset`, `precedent_match`, `context_consistency`.

## 5. Non-functional requirements

| ID | Requirement |
|---|---|
| NFR1 | New Python callables shall have McCabe complexity ≤ 5. |
| NFR2 | Offline tests shall not need network. |
| NFR3 | Python 3 stdlib only. |
| NFR4 | Spec and comments shall use ASD-STE. |
| NFR5 | Benchmark `evaluate` for N=20 and N=100. Do not store result files in git. |
| NFR6 | Two or three comment lines above each new function. |

## 6. Interface

Input: `MetricEvent` with context-memory features when the metric layer supplies them.  
Output: `COPDecision` with `context_used["status"]`.  
Optional: `context_used["session"]` from one session lookup.

## 7. Environment

No new environment variables.

## 8. Acceptance criteria

1. Unit tests pass.
2. Integration tests pass.
3. Functional tests pass.
4. Regression tests freeze status names.
5. Classification and Audit tests still pass.
6. `radon cc` ≤ 5 on new callables.
7. `python -m cops.benchmarks.bench_context_memory` runs. Do not commit output.
8. ISO review in §10 is complete.

## 9. V-cycle map

| V left | Artifact | V right | Proof |
|---|---|---|---|
| Requirements | This document | Acceptance | §8 |
| Architecture | Context quality, not liveness | Functional test | Public `evaluate` |
| Detailed design | Feature → status rules | Integration | Incident-input + one session lookup |
| Implementation | `context_memory.py` | Unit + regression | Offline green |

## 10. ISO/IEC 25010 quality lenses

| Lens | Assessment |
|---|---|
| Functional suitability | Meets FR1–FR18. Context quality only. |
| Performance efficiency | Offline `evaluate` benchmark. |
| Compatibility | Same `COPDecision`. Extends `ContextProvider` without changing Audit lookup. |
| Usability | WATCH on loss, contradiction, or reset. NONE when OK, incomplete, or precedent. |
| Reliability | Missing keys and UNKNOWN do not claim `context_ok`. Failure → ESCALATE. |
| Security | One session ID lookup. No full history. |
| Maintainability | Small helpers. McCabe ≤ 5. |
| Portability | Stdlib. |

## 11. Risks

| Risk | Mitigation |
|---|---|
| Invent compaction metrics | FR2. Read features only. |
| Invent confidence | FR18. |
| Lookup changes verdict | FR16. |
| Overlap with Runtime Monitoring | No heartbeat or coverage fields. |

## 12. Implementation plan

### Phase A — Spec lock

This document.

### Phase B — Red

Classification-style tests for Context and Memory.

### Phase C — Green

`cops/context.py` session lookup and `cops/context_memory.py`.

### Phase D

Benchmark and `radon cc`.

### File touch list

| File | Action |
|---|---|
| `cops/CONTEXT_MEMORY_SPEC.md` | Spec + plan |
| `cops/context.py` | Session lookup |
| `cops/context_memory.py` | COP |
| `cops/contracts.py` | Add to `OWNED_COPS` |
| `cops/__init__.py` | Export |
| `cops/tests/*/test_context_memory*.py` | TDD |
| `cops/benchmarks/bench_context_memory.py` | Benchmark |
