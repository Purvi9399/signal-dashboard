# Audit and Traceability COP — Requirements Specification

Status: Active for implementation.  
Language: ASD-STE (Standard Technical English).  
Branch: `cops/audit` (from `main`).

## 1. Purpose

Audit shall decide whether an important action can be attributed and
reconstructed.

Audit shall not judge collector liveness. That is Runtime Monitoring.

## 2. Scope

### 2.1 In scope

- `cops/audit.py`
- Optional one-row raw event lookup by ID
- Unit, integration, functional, and regression tests
- Offline benchmark of `evaluate`
- McCabe cyclomatic complexity ≤ 5 for new Python callables

### 2.2 Out of scope

- Runtime Monitoring, Interaction Discovery, Policy Enforcement,
  Anomaly Detection, Incident Response (colleague)
- `SELECT *` from events
- LLM analysis
- Dashboard UI
- BLOCK, PAUSE, or policy enforcement

## 3. Definitions

| Term | Meaning |
|---|---|
| `attributable` | Pointer and actor attribution are present. Collectors do not conflict. |
| `untraceable` | No `raw_ref`, or `unattributed` is true. |
| `disputed` | `collectors_agree` is false. |
| `incomplete` | Required feature keys are missing. Do not claim certainty. |

## 4. Functional requirements

| ID | Requirement |
|---|---|
| FR1 | Audit shall consume one `MetricEvent`. |
| FR2 | Audit shall read `raw_ref`, `attribution`, `unattributed`, and `collectors_agree` from `metric.features`. It shall not recompute them. |
| FR3 | Status shall be `incomplete` when a required feature key is absent. |
| FR4 | Status shall be `disputed` when `collectors_agree` is false. |
| FR5 | Status shall be `untraceable` when `unattributed` is true or `raw_ref` is empty. |
| FR6 | Else status shall be `attributable`. |
| FR7 | Status order shall be incomplete, then disputed, then untraceable, then attributable. |
| FR8 | Successful `attributable` and `incomplete` evaluations shall use action `NONE`. |
| FR9 | Successful `untraceable` and `disputed` evaluations shall use action `ALERT`. |
| FR10 | `COPDecision.cop` shall be `CopType.AUDIT_AND_TRACEABILITY`. |
| FR11 | `untraceable` and `disputed` shall set `downstream_cops` to Governance Architecture. Else empty. |
| FR12 | If `evidence_refs` has an ID, Audit may request that one raw event from `ContextProvider.get_raw_event`. It shall not query a full table. |
| FR13 | The lookup shall not change status. Status comes from features only. |
| FR14 | On evaluator failure, `failed` shall be true and action shall be `ESCALATE`. |
| FR15 | If `state` is `UNKNOWN`, confidence shall be 0.30 and action shall be `NONE`. Do not claim certainty. |
| FR16 | Confidence shall be 0.95 when required keys are present, else 0.60, unless FR15 applies. |

Required feature keys: `raw_ref`, `attribution`, `unattributed`, `collectors_agree`.

## 5. Non-functional requirements

| ID | Requirement |
|---|---|
| NFR1 | New Python callables shall have McCabe complexity ≤ 5. |
| NFR2 | Offline tests shall not need network. |
| NFR3 | Python 3 stdlib only. |
| NFR4 | Spec and comments shall use ASD-STE. |
| NFR5 | Benchmark `evaluate` for N=20 and N=100. Do not store result files in git. |

## 6. Interface

Input: `MetricEvent` with curated attribution features.  
Output: `COPDecision` with `context_used["status"]`.  
Optional: `context_used["raw_event"]` from one ID lookup.

## 7. Environment

No new environment variables.

## 8. Acceptance criteria

1. Unit tests pass.
2. Integration tests pass.
3. Functional tests pass.
4. Regression tests freeze status names.
5. `radon cc` ≤ 5 on new callables.
6. `python -m cops.benchmarks.bench_audit` runs. Do not commit output.
7. ISO review in §10 is complete.

## 9. V-cycle map

| V left | Artifact | V right | Proof |
|---|---|---|---|
| Requirements | This document | Acceptance | §8 |
| Architecture | Reconstruct vs live health | Functional test | Public `evaluate` |
| Detailed design | Feature → status rules | Integration | Incident-input + one ID lookup |
| Implementation | `audit.py` | Unit + regression | Offline green |

## 10. ISO/IEC 25010 quality lenses

| Lens | Assessment |
|---|---|
| Functional suitability | Meets FR1–FR16. Attribute and reconstruct only. |
| Performance efficiency | Offline `evaluate` benchmark. |
| Compatibility | Reuses curate `raw_ref`, `attribution`, `collectors_agree`. |
| Usability | ALERT on gap. NONE when attributable or incomplete. |
| Reliability | UNKNOWN and incomplete do not claim certainty. Failure → ESCALATE. |
| Security | One event ID lookup. No full table. Evidence IDs only. |
| Maintainability | Small helpers. McCabe ≤ 5. |
| Portability | Stdlib. |

## 11. Risks

| Risk | Mitigation |
|---|---|
| Overlap with Runtime Monitoring | Audit does not use heartbeat or latency. |
| Lookup changes verdict | FR13. |
| False ALERT on missing keys | Incomplete → NONE. |

## 12. Implementation plan

### Phase A — Spec lock

This document.

### Phase B — Red

Classification-style tests for Audit.

### Phase C — Green

`cops/context.py` (one method) and `cops/audit.py`.

### Phase D

Benchmark and `radon cc`.

### File touch list

| File | Action |
|---|---|
| `cops/AUDIT_SPEC.md` | Spec + plan |
| `cops/context.py` | One-ID lookup |
| `cops/audit.py` | COP |
| `cops/__init__.py` | Export |
| `cops/tests/*/test_audit*.py` | TDD |
| `cops/benchmarks/bench_audit.py` | Benchmark |
