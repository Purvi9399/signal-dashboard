# COPS Shared Contracts — Requirements Specification

Status: Active for implementation.  
Language: ASD-STE (Standard Technical English).  
Branch: `cops/v1-contracts` (from `Dev` / `main`).

## 1. Purpose

The system shall give every COP one input type and one output type.

The metric layer shall calculate values outside COPS.
A COP shall interpret a metric or a list of decisions.
A COP shall not query a full event table.

## 2. Scope

### 2.1 In scope

- Shared types in `collector_framework/cops/contracts.py`
- Public re-exports in `collector_framework/cops/__init__.py`
- Unit, integration, functional, and regression tests
- Offline benchmark of object construction
- McCabe cyclomatic complexity ≤ 5 for new Python callables

### 2.2 Out of scope

- Runtime Monitoring, Interaction Discovery, Policy Enforcement,
  Anomaly Detection, and Incident Response modules (colleague)
- Classification module (later slice)
- Dashboard UI
- New database
- LLM analysis of raw telemetry

## 3. Definitions

| Term | Meaning |
|---|---|
| MetricEvent | One deterministic metric row. Evidence is IDs only. |
| COPDecision | One COP finding and action. |
| MetricState | `NORMAL`, `WARNING`, `EXCEPTION`, or `UNKNOWN`. |
| ActionType | Allowed V1 action. |
| CopType | Canonical COP name string. |
| Targeted context | Small query after a non-normal metric. Not `SELECT *`. |

## 4. Functional requirements

| ID | Requirement |
|---|---|
| FR1 | `MetricEvent` shall include `metric_id`, `metric_name`, `session_id`, `value`, `threshold`, and `state`. |
| FR2 | `MetricEvent` may include `trace_id`, `features`, `evidence_refs`, `metric_source`, and `timestamp`. |
| FR3 | `evidence_refs` shall be a list of ID strings. It shall not hold raw event bodies. |
| FR4 | `COPDecision` shall include `cop`, `session_id`, `interpretation`, `confidence`, `action`, and `action_reason`. |
| FR5 | `COPDecision.metric_id` shall be optional. Incident Response has no metric of its own. |
| FR6 | `COPDecision.cop` and `downstream_cops` shall use `CopType`. |
| FR7 | `CopType` shall define exactly 18 COP names. Values shall not change without a regression test change. |
| FR8 | `ActionType` shall include `NONE`, `WATCH`, `ALERT`, `INSTRUCT`, `REQUEST_CONTEXT`, `REQUEST_APPROVAL`, `ESCALATE`, `BLOCK`, `PAUSE`, and `PASS_TO_COP`. |
| FR9 | `MetricState` shall include `NORMAL`, `WARNING`, `EXCEPTION`, and `UNKNOWN`. |
| FR10 | `COLLEAGUE_COPS` shall list the five colleague COPS. `OWNED_COPS` shall list Classification only. The two sets shall not overlap. |
| FR11 | Incident Response input shall be a list of `COPDecision` objects. It shall not be a `MetricEvent`. |
| FR12 | Package `cops` shall export `MetricEvent`, `COPDecision`, `CopType`, `ActionType`, and `MetricState`. |
| FR13 | `confidence` shall be valid only when it is between 0.0 and 1.0 inclusive. |
| FR14 | Contracts shall live only in `collector_framework/cops/`. Do not copy them into the dashboard repo. |

## 5. Non-functional requirements

| ID | Requirement |
|---|---|
| NFR1 | New and changed Python callables shall have McCabe complexity ≤ 5. |
| NFR2 | Offline tests shall not need network access. |
| NFR3 | The module shall use Python 3 stdlib only. |
| NFR4 | Spec, comments, and operator text shall use ASD-STE. |
| NFR5 | Independent benchmarks shall run offline for N=20 and N=100. Do not store result files in git. |

## 6. Interface

### 6.1 MetricEvent

Required: `metric_id`, `metric_name`, `session_id`, `value`, `threshold`, `state`.  
Optional: `trace_id`, `features`, `evidence_refs`, `metric_source`, `timestamp`.

### 6.2 COPDecision

Required: `cop`, `session_id`, `interpretation`, `confidence`, `action`, `action_reason`.  
Optional: `metric_id`, `context_used`, `downstream_cops`, `evaluation_ms`, `failed`, `error`.

### 6.3 CopType values

WorkflowOrchestration, RuntimeMonitoring, AnomalyDetection, ContextAndMemory,
InteractionDiscovery, IncidentResponse, Classification, PolicyEnforcement,
RiskMitigation, ResourceManagement, GovernanceIntelligence, BehaviorAnalytics,
RecoveryAndResilience, WorkloadOptimization, AuditAndTraceability,
KnowledgeAndPolicy, AgentLifecycle, GovernanceArchitecture.

## 7. Environment

No new environment variables.

## 8. Acceptance criteria

1. Unit tests pass.
2. Integration tests pass.
3. Functional tests pass.
4. Regression tests pass. CopType and ActionType values match the frozen lists.
5. `radon cc` shows CC ≤ 5 on new Python callables.
6. `python -m cops.benchmarks.bench_contracts` runs for N=20 and N=100. Do not commit the output file.
7. ISO quality lens review in §10 is complete.

## 9. V-cycle map

| V left | Artifact | V right | Proof |
|---|---|---|---|
| Requirements | This document | Acceptance | §8 checklist |
| Architecture | Shared contracts only; COP modules later | Functional test | Public import of `cops` |
| Detailed design | `MetricEvent` + `COPDecision` + enums | Integration tests | Incident consumes decisions |
| Implementation | `contracts.py` | Unit + regression | Offline green |

Modules:

- `cops/contracts.py` — types and ownership tuples
- `cops/__init__.py` — public exports

## 10. ISO/IEC 25010 quality lenses

| Lens | Assessment |
|---|---|
| Functional suitability | Meets FR1–FR14 for shared input and output types. |
| Performance efficiency | Offline construction benchmark via `cops/benchmarks/bench_contracts.py`. |
| Compatibility | Stdlib dataclasses; colleague COPS import the same module. |
| Usability | This spec and package docstring state the metric-first rule. |
| Reliability | Confidence and evidence-ref helpers reject invalid values. |
| Security | Evidence is IDs only. No raw payloads in the contract. |
| Maintainability | One module. McCabe ≤ 5. Layered tests. |
| Portability | Python 3 stdlib. No OS-specific code. |

## 11. Risks

| Risk | Mitigation |
|---|---|
| Two copies of types | Keep one module. FR14. |
| CopType string drift | Regression test with frozen values. |
| Incident reads metrics | FR5 and FR11. Integration test. |

## 12. Implementation plan

### Phase A — Spec lock

This document.

### Phase B — Red (TDD)

Add tests under `cops/tests/`:

1. `unit/` — field defaults, ownership split, confidence helper
2. `integration/` — Incident input is a list of COPDecision
3. `functional/` — public package import
4. `regression/` — frozen CopType and ActionType values

Run tests. New helpers shall fail until Phase C.

### Phase C — Green

Keep existing dataclasses. Add small helpers for FR3 and FR13. Re-run tests.

### Phase D — Benchmark and complexity

Run `python -m cops.benchmarks.bench_contracts`. Keep the printed times with the review. Do not commit a result file. Run `radon cc`.

### File touch list

| File | Action |
|---|---|
| `cops/SPEC.md` | Spec + plan (this file) |
| `cops/contracts.py` | Types + helpers |
| `cops/__init__.py` | Public exports |
| `cops/tests/{unit,integration,functional,regression}/` | TDD |
| `cops/benchmarks/bench_contracts.py` | Benchmark |
