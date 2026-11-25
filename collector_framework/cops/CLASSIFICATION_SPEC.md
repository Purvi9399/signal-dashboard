# Classification COP — Requirements Specification

Status: Active for implementation.  
Language: ASD-STE (Standard Technical English).  
Branch: `cops/classification` (from `main`).

## 1. Purpose

Classification shall name the kind of situation and which entities
stay under watch.

Classification shall not enforce. V1 action shall be `NONE` (tags only).

## 2. Scope

### 2.1 In scope

- `cops/classification.py`
- Rule tags from existing curated metrics
- Unit, integration, functional, and regression tests
- Offline benchmark of `evaluate`
- McCabe cyclomatic complexity ≤ 5 for new Python callables

### 2.2 Out of scope

- Runtime Monitoring, Interaction Discovery, Policy Enforcement,
  Anomaly Detection, Incident Response (colleague)
- Targeted Redis or SQL context (binding table: none yet)
- LLM classification
- Dashboard UI
- `WATCH` / `ALERT` / `BLOCK` from this COP

## 3. Definitions

| Term | Meaning |
|---|---|
| Tag | A short label on a session or event. |
| `sensitive` | `sensitivity_tier` ≥ 3 (curate secret/credential paths). |
| `config` | `is_config_file` is true. |
| `high-risk` | `risk_markers` is a non-empty list or string. |
| `watch` | At least one of sensitive, config, high-risk. |
| `routine` | None of those tags. |

## 4. Functional requirements

| ID | Requirement |
|---|---|
| FR1 | Classification shall consume one `MetricEvent`. It shall not query a full event table. |
| FR2 | Classification shall read tags from `metric.features`. It shall not recalculate `sensitivity_tier`, `risk_markers`, `tool_category`, or `is_config_file`. |
| FR3 | If `sensitivity_tier` ≥ 3, the result shall include tag `sensitive`. |
| FR4 | If `is_config_file` is true, the result shall include tag `config`. |
| FR5 | If `risk_markers` is non-empty, the result shall include tag `high-risk`. |
| FR6 | If any of FR3–FR5 apply, the result shall include tag `watch`. Else it shall include tag `routine`. |
| FR7 | Optional string features `groups`, `trigger_class`, and `solution_class` shall add tags of the form `key:value` when present. |
| FR8 | `COPDecision.action` shall be `NONE` on a successful evaluation. |
| FR9 | `COPDecision.cop` shall be `CopType.CLASSIFICATION`. |
| FR10 | If tag `watch` is present, `downstream_cops` shall include `GovernanceIntelligence`. Else `downstream_cops` shall be empty. |
| FR11 | `context_used` shall include `tags` and, when present, `tool_category`. |
| FR12 | Classification shall not call a context provider. |
| FR13 | On evaluator failure, `failed` shall be true and `action` shall be `ESCALATE`. |
| FR14 | Confidence shall be 0.95 when required feature keys are present, 0.60 when some are missing, and 0.30 when `state` is `UNKNOWN`. |

Required feature keys: `sensitivity_tier`, `is_config_file`, `risk_markers`.

## 5. Non-functional requirements

| ID | Requirement |
|---|---|
| NFR1 | New Python callables shall have McCabe complexity ≤ 5. |
| NFR2 | Offline tests shall not need network access. |
| NFR3 | Python 3 stdlib only. |
| NFR4 | Spec and comments shall use ASD-STE. |
| NFR5 | Benchmark `evaluate` for N=20 and N=100. Do not store result files in git. |

## 6. Interface

Input: `MetricEvent` with `features` from curate / Insights.

Output: `COPDecision` with tags in `context_used["tags"]` and
`interpretation`.

## 7. Environment

No new environment variables.

## 8. Acceptance criteria

1. Unit tests pass.
2. Integration tests pass.
3. Functional tests pass.
4. Regression tests freeze tag names.
5. `radon cc` ≤ 5 on new callables.
6. `python -m cops.benchmarks.bench_classification` runs. Do not commit output.
7. ISO review in §10 is complete.

## 9. V-cycle map

| V left | Artifact | V right | Proof |
|---|---|---|---|
| Requirements | This document | Acceptance | §8 |
| Architecture | Tags only; no enforcement | Functional test | Public `evaluate` |
| Detailed design | Feature → tag rules | Integration | Decision is incident-input |
| Implementation | `classification.py` | Unit + regression | Offline green |

## 10. ISO/IEC 25010 quality lenses

| Lens | Assessment |
|---|---|
| Functional suitability | Meets FR1–FR14. Tags only. |
| Performance efficiency | Offline `evaluate` benchmark. |
| Compatibility | Reuses curate fields. Same `COPDecision`. |
| Usability | Binding: action NONE. Downstream Governance Intelligence. |
| Reliability | Evaluator failure → ESCALATE. UNKNOWN lowers confidence. |
| Security | No raw payloads. Features only. |
| Maintainability | Small helpers. McCabe ≤ 5. |
| Portability | Stdlib. |

## 11. Risks

| Risk | Mitigation |
|---|---|
| Treat unusual as prohibited | Action is NONE. |
| Recalculate metrics | FR2. Read features only. |
| Context queries | FR12. None in V1. |

## 12. Implementation plan

### Phase A — Spec lock

This document.

### Phase B — Red

Tests under `cops/tests/` for Classification.

### Phase C — Green

`cops/classification.py`. No colleague COP files.

### Phase D

Benchmark and `radon cc`.

### File touch list

| File | Action |
|---|---|
| `cops/CLASSIFICATION_SPEC.md` | Spec + plan |
| `cops/classification.py` | COP |
| `cops/__init__.py` | Export COP |
| `cops/tests/*/test_classification*.py` | TDD |
| `cops/benchmarks/bench_classification.py` | Benchmark |
