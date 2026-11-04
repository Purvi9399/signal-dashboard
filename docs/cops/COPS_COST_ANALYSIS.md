# COPS cost and performance analysis

This report audits the cost and performance of the latest live COPS evaluation (2026-11-02). It uses only results that were already recorded. **No new paid model calls were made, and no COP logic was changed.** Where a figure was not recorded, the report says so (§10) instead of filling the gap.

Label conventions:
- **Measured**: read directly from a results file.
- **Recomputed**: derived from measured numbers with a formula given in §9.
- **Replay-measured**: timed locally by replaying recorded model replies at no cost. Used only for local compute, tool calls and memory, never for model cost.
- **Estimate**: a projection with its assumptions stated. Every estimate is labelled.

The model is referred to as "the configured model". Its id is recorded in `docs/cops/evaluation/live_results.json` (`model`). Model ids are kept out of repository docs by project rule.

## 1. Sources and method

| Source | What it provides |
|---|---|
| `docs/cops/evaluation/live_results.json` | The 30 live cases (10 LLM COPs × normal, abnormal, missing). Per call: `purpose`, `input_tokens`, `output_tokens`, `latency_ms`, `cost_usd`, `simulated`, `tokens_measured`, `request_id`. Per case: `wall_ms`, labels, verdicts. `totals` block. |
| `docs/cops/evaluation/test_bench_results.json` | Fleet scenarios F1 and F2: the only other live calls (7 calls). |
| `docs/cops/evaluation/live_attempt_2026-11-02.json` | The first attempt: 30 calls refused with HTTP 400 (credit balance too low). |
| `collector/cops/runtime/llm.py` | `load_pricing` (reads `COPS_PRICING`), `AnthropicProvider.complete` (request shape, SDK retries off, `max_tokens` 16000, effort from `COPS_EFFORT`), `LLMGateway._cost` (cost formula, `cost_basis`). |
| `collector/cops/runtime/live_eval.py` | `LIVE_BUDGET` (per-run caps), `run_one` (one case = one `graph.run_case`). |
| `collector/cops/runtime/graph.py` | `_payload` (what each call sends), the plan → tools → assess loop. |
| `collector/cops/runtime/memory.py` | `POLICIES` (memory scope per COP), `MemoryGateway.read` (returns every `established` and `verified_pending` record, no cap). |
| Local replay (scratch scripts, not committed) | Replays the 30 cases with the recorded replies and runs the deterministic cases. Measures node timings, tool calls, memory reads and writes, payload sizes and store bytes. No model is called. |

Settings used by the live run (from the run command, recorded in `live_results.json`): `COPS_PRICING` set to $2.00 per million input tokens and $10.00 per million output tokens for the configured model; `COPS_EFFORT` not set; no prompt-cache configuration; dry-run mode.

## 2. Per-COP cost table (18 rows)

Columns: invocations counts every time the COP ran in the 30 live cases, including runs as an upstream of another COP. Model latency is the sum of `latency_ms` over the COP's calls. Local compute and memory are replay-measured. Compute cost uses an assumed $0.05 per vCPU-hour (estimate). "M1" is the configured model.

| # | COP | Type | Model / provider | Invocations in live eval | LLM API calls | Input tokens | Output tokens | Cached input tokens | API cost (USD) | Tool calls | Model latency (s) | Local compute (ms) | Memory reads / writes | Memory time (ms) | Compute cost (USD, est.) | Total attributable (USD) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | Runtime Monitoring | deterministic | none (rules) | 30 | 0 | 0 | 0 | n/a | 0.000000 | 60 | 0.0 | 44.3 | 30 / 3 | 9.3 | 6.2e-07 | 0.000001 |
| 2 | Workflow Orchestration | LLM | configured model M1 / Anthropic API (6 live, 0 simulated) | 3 | 6 | 23,767 | 6,848 | not recorded (0 expected) | 0.116014 | 16 | 55.5 | 6.9 | 3 / 1 | 1.1 | 9.6e-08 | 0.116014 |
| 3 | Context & Memory | LLM | configured model M1 / Anthropic API (6 live, 0 simulated) | 3 | 6 | 19,584 | 5,160 | not recorded (0 expected) | 0.090768 | 24 | 41.8 | 5.1 | 3 / 1 | 1.0 | 7.1e-08 | 0.090768 |
| 4 | Behavior Analytics | deterministic | none (rules) | 9 | 0 | 0 | 0 | n/a | 0.000000 | 18 | 0.0 | 12.3 | 9 / 2 | 2.2 | 1.7e-07 | 0.000000 |
| 5 | Anomaly Detection | LLM | configured model M1 / Anthropic API (6 live, 0 simulated) | 3 | 6 | 25,754 | 10,046 | not recorded (0 expected) | 0.151968 | 12 | 70.1 | 5.3 | 3 / 1 | 1.0 | 7.4e-08 | 0.151968 |
| 6 | Interaction Discovery | deterministic | none (rules) | 15 | 0 | 0 | 0 | n/a | 0.000000 | 30 | 0.0 | 16.8 | 15 / 2 | 2.5 | 2.3e-07 | 0.000000 |
| 7 | Agent Lifecycle Management | deterministic | none (rules) | 15 | 0 | 0 | 0 | n/a | 0.000000 | 30 | 0.0 | 12.5 | 15 / 0 | 1.9 | 1.7e-07 | 0.000000 |
| 8 | Policy Enforcement | deterministic | none (rules) | 12 | 0 | 0 | 0 | n/a | 0.000000 | 24 | 0.0 | 17.3 | 12 / 5 | 3.2 | 2.4e-07 | 0.000000 |
| 9 | Recovery & Resilience | deterministic | none (rules) | 6 | 0 | 0 | 0 | n/a | 0.000000 | 12 | 0.0 | 5.2 | 6 / 0 | 1.0 | 7.2e-08 | 0.000000 |
| 10 | Incident Response | LLM | configured model M1 / Anthropic API (6 live, 0 simulated) | 3 | 6 | 25,281 | 9,652 | not recorded (0 expected) | 0.147082 | 15 | 67.7 | 7.1 | 3 / 2 | 2.1 | 9.8e-08 | 0.147082 |
| 11 | Classification & Tagging | LLM | configured model M1 / Anthropic API (6 live, 0 simulated) | 3 | 6 | 21,448 | 6,981 | not recorded (0 expected) | 0.112706 | 15 | 56.0 | 4.7 | 3 / 1 | 0.9 | 6.6e-08 | 0.112706 |
| 12 | Resource Management | deterministic | none (rules) | 6 | 0 | 0 | 0 | n/a | 0.000000 | 12 | 0.0 | 5.9 | 6 / 0 | 0.7 | 8.2e-08 | 0.000000 |
| 13 | Risk Mitigation | LLM | configured model M1 / Anthropic API (6 live, 0 simulated) | 3 | 6 | 23,944 | 8,725 | not recorded (0 expected) | 0.135138 | 18 | 63.5 | 6.2 | 3 / 2 | 1.7 | 8.6e-08 | 0.135138 |
| 14 | Governance Intelligence | LLM | configured model M1 / Anthropic API (6 live, 0 simulated) | 3 | 6 | 19,830 | 7,358 | not recorded (0 expected) | 0.113240 | 15 | 57.8 | 26.8 | 3 / 2 | 2.0 | 3.7e-07 | 0.113240 |
| 15 | Workload Optimization | LLM | configured model M1 / Anthropic API (6 live, 0 simulated) | 3 | 6 | 21,344 | 8,612 | not recorded (0 expected) | 0.128808 | 18 | 62.3 | 8.0 | 3 / 2 | 1.9 | 1.1e-07 | 0.128808 |
| 16 | Audit & Traceability | deterministic | none (rules) | 3 | 0 | 0 | 0 | n/a | 0.000000 | 6 | 0.0 | 2.7 | 3 / 1 | 0.7 | 3.7e-08 | 0.000000 |
| 17 | Knowledge & Policy Management | LLM | configured model M1 / Anthropic API (6 live, 0 simulated) | 3 | 6 | 18,679 | 5,304 | not recorded (0 expected) | 0.090398 | 21 | 41.5 | 5.0 | 3 / 1 | 1.1 | 6.9e-08 | 0.090398 |
| 18 | Governance Architecture | LLM | configured model M1 / Anthropic API (6 live, 0 simulated) | 3 | 6 | 32,792 | 11,437 | not recorded (0 expected) | 0.179954 | 18 | 78.0 | 13.4 | 3 / 3 | 1.9 | 1.9e-07 | 0.179954 |
| | **Total** | 10 LLM + 8 rules | | **126** | **60** | **232,423** | **80,123** | – | **1.266076** | **364** | **594.4** | **205.3** | **126 / 29** | **36.1** | **2.9e-06** | **1.266079** |
Notes on the table:
- **Cached input tokens:** the run sent no `cache_control`, so no cache reads or writes are expected. But the usage record keeps only `input_tokens` and `output_tokens` (`llm.AnthropicProvider.complete`), so this cannot be confirmed from the logs. Shown as "not recorded (0 expected)".
- **Deterministic COPs have no model cost.** Their own cost is local compute only (about 1–2 ms of node time per run; 10–48 ms of case wall time in their own bench cases, replay-measured).
- **Memory cost** in storage terms is negligible: about 15 KB written per COP invocation, about 63 KB per case (replay-measured store bytes). Its real cost is the extra input tokens it adds to later prompts. That is modelled in §7.
- Total attributable = API cost + compute cost. Compute adds $0.000003 in total.

### 2.1 Model cost per investigation (LLM COPs)

Each LLM COP ran 3 investigations (normal, abnormal, missing), each one plan call and one assess call.

| COP | Plan: in / out tokens (3 calls) | Assess: in / out tokens (3 calls) | Share of COP cost from output | Cost per investigation: min / mean / max | Model latency per investigation: mean / max (s) |
|---|---|---|---|---|---|
| Governance Architecture | 5,889 / 2,566 | 26,903 / 8,871 | 64% | $0.0578 / $0.0600 / $0.0627 | 26.0 / 28.1 |
| Anomaly Detection | 8,455 / 3,092 | 17,299 / 6,954 | 66% | $0.0466 / $0.0507 / $0.0537 | 23.4 / 25.9 |
| Incident Response | 8,097 / 3,071 | 17,184 / 6,581 | 66% | $0.0406 / $0.0490 / $0.0561 | 22.6 / 25.6 |
| Risk Mitigation | 7,701 / 2,816 | 16,243 / 5,909 | 65% | $0.0299 / $0.0450 / $0.0546 | 21.2 / 27.9 |
| Workload Optimization | 6,711 / 2,495 | 14,633 / 6,117 | 67% | $0.0399 / $0.0429 / $0.0459 | 20.8 / 23.6 |
| Workflow Orchestration | 7,303 / 2,309 | 16,464 / 4,539 | 59% | $0.0277 / $0.0387 / $0.0494 | 18.5 / 25.7 |
| Governance Intelligence | 5,718 / 2,328 | 14,112 / 5,030 | 65% | $0.0242 / $0.0377 / $0.0448 | 19.3 / 24.2 |
| Classification & Tagging | 6,535 / 2,991 | 14,913 / 3,990 | 62% | $0.0315 / $0.0376 / $0.0494 | 18.7 / 24.1 |
| Context & Memory | 5,854 / 2,081 | 13,730 / 3,079 | 57% | $0.0231 / $0.0303 / $0.0356 | 13.9 / 16.2 |
| Knowledge & Policy Management | 5,818 / 1,857 | 12,861 / 3,447 | 59% | $0.0215 / $0.0301 / $0.0372 | 13.8 / 18.5 |
Observations (recomputed from the per-call records):
- **Output tokens are 63% of total cost** ($0.801 of $1.266), and 57–67% for every COP.
- **Assess calls cost 2.2× plan calls**: mean $0.0291 vs $0.0131 per call. Assess sends a mean of 5,478 input tokens against 2,269 for plan, and produces 1,817 output tokens against 854.
- **Governance Architecture is the most expensive COP** ($0.180, 14% of the total). Its assess calls average 8,968 input tokens because the catalogue and coverage tool results are passed in full.

## 3. Totals

| Measure | Value | Basis |
|---|---|---|
| LLM API calls | 60 (30 plan, 30 assess) | measured |
| Mocked / simulated calls counted | 0 (all 60 have `simulated: false`, `tokens_measured: true`) | measured |
| Retries | 0 (SDK retries are off in `AnthropicProvider`; 0 errors recorded) | measured |
| Input tokens | 232,423 | measured |
| Output tokens | 80,123 | measured |
| Cached tokens | not recorded; 0 expected (no `cache_control` sent) | see §4 |
| Model cost | **$1.266076** | recomputed = recorded |
| Deterministic compute cost | $0.000003 (205 ms local compute in total) | replay-measured × assumed $0.05/vCPU-h |
| Memory cost | 126 reads, 29 writes, 36 ms; storage negligible (about 15 KB per COP invocation) | replay-measured |
| **Total attributable cost of the run** | **$1.266** | |
| COP invocations | 126 (30 LLM investigations, 96 deterministic runs as upstreams) | measured |
| Average cost per COP invocation | $0.0100 (all 126); $0.0422 per LLM investigation | recomputed |
| Average cost per scenario case | $0.0422 (30 cases) | recomputed |
| Tool calls | 364 | replay-measured |

### 3.1 Cost per scenario type

| Scenario | Cases | Calls | Input / output tokens | Cost | Cost per case | Model latency | Sum of case wall time |
|---|---|---|---|---|---|---|---|
| normal | 10 | 20 | 76,224 / 26,137 | $0.4138 | $0.0414 | 196.2 s | 196.7 s |
| abnormal | 10 | 20 | 79,554 / 33,035 | $0.4895 | $0.0489 | 239.5 s | 240.0 s |
| missing evidence | 10 | 20 | 76,645 / 20,951 | $0.3628 | $0.0363 | 158.7 s | 159.2 s |
| **all** | 30 | 60 | 232,423 / 80,123 | **$1.2661** | $0.0422 | 594.4 s | 595.9 s |

Input is almost the same across scenarios. The difference comes from output: abnormal cases produce 26% more output than normal, and missing-evidence cases 20% less (consistent with shorter conclusions when evidence is absent; not separately measured).

### 3.2 Runtime

| Measure | Value | Basis |
|---|---|---|
| Sum of COP execution time (all 30 cases) | 594.6 s: 594.4 s model latency + 0.2 s local compute | measured + replay-measured |
| Sum of case wall time (`wall_ms`) | 595.9 s | measured |
| Share of case time spent waiting on the model | 99.7% | recomputed |
| Wall-clock duration of the whole run | **not recorded.** The runner ran cases one after another, so it was at least 595.9 s (about 10 min) | lower bound only |
| Per-call latency | plan mean 7.1 s (4.8–9.8 s); assess mean 12.7 s (3.1–21.3 s) | measured |
| Per-investigation latency | mean 19.8 s; slowest COP Governance Architecture, 26.0 s mean | measured |
| Deterministic COP run | about 1–2 ms of node time; 10–48 ms per bench case | replay-measured |

The two times differ by design. The sum of COP times counts every COP's model wait. The wall time is per case. They are almost equal here because each case runs one LLM COP and its upstreams run in milliseconds.

## 4. Billing verification

**Status: the figures are calculated API cost (measured tokens × configured price). They are not provider billing.** `LLMGateway._cost` labels each call `cost_basis: "configured_price_estimate"`. This session has no Admin API key or console access, so the invoice could not be checked.

### 4.1 Prices

| Item | Price used in the run (`COPS_PRICING`) | Provider's published price for the configured model | Used in this run? |
|---|---|---|---|
| Input tokens | $2.00 / MTok | $2.00 / MTok | yes |
| Output tokens | $10.00 / MTok | $10.00 / MTok | yes |
| Cache read | not configured | $0.20 / MTok (0.1× input) | no (no `cache_control` sent) |
| Cache write, 5-minute TTL | not configured | $2.50 / MTok (1.25× input) | no |
| Cache write, 1-hour TTL | not configured | $4.00 / MTok (2× input) | no |
| Batch API | not used | 50% of standard (to be confirmed for this model) | no |

The published prices are taken from the provider's model and caching reference as of 2026-11-03. Prices change; confirm against the console before budgeting.

### 4.2 Reconciliation

| Check | Result |
|---|---|
| Σ input tokens × $2/M | 232,423 × 0.000002 = $0.464846 |
| Σ output tokens × $10/M | 80,123 × 0.00001 = $0.801230 |
| Recomputed total | **$1.266076** |
| Sum of the 60 recorded `cost_usd` values | $1.266076 |
| `totals.cost_usd` in `live_results.json` | $1.266076 |
| Calls where recorded cost ≠ recomputed cost | 0 of 60 |
| Fleet F1 (3 calls, 16,316 / 4,727 tokens) | $0.079902, recomputes exactly |
| Fleet F2 (4 calls, 18,850 / 7,533 tokens) | $0.113030, recomputes exactly |
| First attempt (`live_attempt_2026-11-02.json`) | 30 calls refused with HTTP 400; 0 tokens; $0 |

**What could still differ from the invoice:**
- **Thinking tokens.** The request set neither thinking nor effort. Per the provider's model notes, this model uses adaptive thinking by default, and thinking is billed as output. The recorded `output_tokens` comes from the API's usage block, so it should already include thinking. But the record has no thinking split, so this is not verified.
- **Cache fields.** Not recorded. None were expected.
- **Request ids.** Every live call recorded a `request_id`, so each one can be matched against the console's usage export.

### 4.3 Total spend against the $5 budget

| Item | Cost | Basis |
|---|---|---|
| Live evaluation (60 calls) | $1.266 | recomputed = recorded |
| Fleet scenarios F1 + F2 (7 calls) | $0.193 | recomputed = recorded |
| Smoke and fix-check runs before the evaluation | about $0.43 | **session tally only; not in any results file** |
| **Total** | **about $1.89 of $5.00** | |

## 5. One run of all 18 COPs (estimate)

**No single run of all 18 COPs together has happened.** Each live case ran one LLM COP and its deterministic upstreams. This estimate assembles one full run from the measured per-investigation costs.

| | Low | Expected | High |
|---|---|---|---|
| 8 deterministic COPs | $0 | $0 | $0 |
| 10 LLM COPs, one investigation each | $0.3429 (sum of per-COP minima) | $0.4220 (sum of means) | $0.4895 (sum of maxima) × 1.10 = **$0.5384** |
| Model time, run in sequence | about 148 s | about 198 s | about 240 s |
| Local compute | < 0.1 s | < 0.1 s | < 0.1 s |

The 1.10 factor in the high case allows for larger upstream payloads, because in a full run every LLM COP sees all its upstream findings rather than only the ones its own case needed. This factor is an assumption. Model time is the sum of each COP's fastest / mean / slowest measured investigation (148 s / 198 s / 240 s).

## 6. 20-user simulation budget (estimate)

**These figures are projections, not measurements.** Each session is one run of COPS governance over one coding-agent session. Each cell shows low / expected / high.

### 6.1 COPS governance cost

| Configuration (COPS governance only) | 20 × 1 (20 sessions) | 20 × 5 (100 sessions) | 20 × 10 (200 sessions) |
|---|---|---|---|
| Deterministic-only baseline (18 rule readings, no model) | $0.00 / $0.00 / $0.00 | $0.00 / $0.00 / $0.00 | $0.00 / $0.00 / $0.00 |
| Hybrid, all 18 COPs every session (10 model investigations) | $6.86 / $8.44 / $10.77 | $34.29 / $42.20 / $53.84 | $68.57 / $84.41 / $107.68 |
| Hybrid, conditional activation (1 / 3 / 6 model investigations per session) | $0.84 / $2.53 / $5.06 | $4.22 / $12.66 / $25.32 | $8.44 / $25.32 / $50.64 |
| Hybrid, all 18, with memory retrieval as coded today (no cap) | $7.07 / $8.97 / $11.93 | $39.93 / $56.30 / $84.72 | $91.29 / $141.20 / $232.07 |
| Hybrid, all 18, with memory capped at 5 records per COP | $7.07 / $8.97 / $11.91 | $38.55 / $48.53 / $63.68 | $78.77 / $98.58 / $129.58 |
| Hybrid, conditional, with memory capped at 5 | $0.87 / $2.69 / $5.75 | $4.65 / $14.56 / $31.23 | $9.46 / $29.57 / $63.78 |

**Assumptions:**
- **Per-session cost** comes from §5: $0.3429 / $0.4220 / $0.5384.
- **Conditional activation** runs 1 / 3 / 6 model investigations per session, at the measured mean of $0.0422 each. The other COPs run as rules only.
- **The memory model:**
  - all 20 users are one tenant;
  - the 7 tenant-scoped LLM COPs (`memory.POLICIES`: IR, CT, RiskM, GI, WLO, KPM, GA) see every record the tenant has written;
  - the 3 agent-scoped ones (WO, AD, CM) see only that user's records;
  - a share p = 0.1 / 0.2 / 0.3 of investigations leaves a record that is then retrieved;
  - each record adds r = 400 / 500 / 730 tokens to both calls, at $2/M input.

  The record size comes from measured payloads: 554 to 2,306 characters, mean 1,608.
- **"As coded today"** means no cap. `MemoryGateway.read` returns every `established` and `verified_pending` record, and `graph._payload` sends them all. Cost per session therefore grows with the number of sessions before it.
- **"Capped at 5"** is a proposed control, not current behaviour.
- **Not included:** the price of output caused by longer memory context, retries, and rejection loops.

### 6.2 Coding agents being monitored (kept separate)

| Coding-agent cost (assumed, not measured) | 20 × 1 | 20 × 5 | 20 × 10 |
|---|---|---|---|
| $0.25 / $1.00 / $4.00 per session | $5.00 / $20.00 / $80.00 | $25.00 / $100.00 / $400.00 | $50.00 / $200.00 / $800.00 |

**No coding-agent spend was measured in this evaluation.** These per-session figures are placeholders, to be replaced with the agent team's real cost per session. They are listed so that COPS governance cost can be compared with what it governs. Under the expected figures, COPS adds about 42% to coding-agent cost when all 18 COPs run every session, and about 13% with conditional activation.

### 6.3 Sensitivity to the number of model calls

Every live investigation used exactly 2 calls (plan, assess). The loop allows more: `LIVE_BUDGET` caps it at 4. Fleet F1's Anomaly Detection used 3. Each extra call is costed as one more assess call ($0.0291 mean).

| Model calls per investigation | Extra cost per session, all 18 | 200 sessions, all 18 | 200 sessions, conditional (3) |
|---|---|---|---|
| 2 (measured) | +$0.000 | $84.41 | $25.32 |
| 3 | +$0.291 | $142.66 | $42.80 |
| 4 (cap) | +$0.583 | $200.92 | $60.28 |

### 6.4 Runtime and overhead at scale

| | Per session | 20 sessions | 100 sessions | 200 sessions |
|---|---|---|---|---|
| All 18, run in sequence | about 198 s | 1.1 h | 5.5 h | 11 h |
| Conditional (3 investigations) | about 59 s | 20 min | 1.6 h | 3.3 h |
| Deterministic only | < 0.1 s | < 2 s | < 10 s | < 20 s |
| Memory store growth (about 63 KB per full case) | | about 1.3 MB | about 6.3 MB | about 13 MB |

These are estimates. Sessions are independent, so running them in parallel cuts wall-clock time. In that case the provider's rate limits set the ceiling, not cost. Memory storage stays small; its cost lies in prompt tokens (§6.1).

### 6.5 Budget summary

| | 20 × 1 | 20 × 5 | 20 × 10 |
|---|---|---|---|
| **Low** (conditional, 1 investigation, memory capped) | $0.87 | $4.65 | $9.46 |
| **Expected** (all 18, memory capped at 5) | $8.97 | $48.53 | $98.58 |
| **High** (all 18, memory uncapped, 3 calls per investigation) | $17.76 | $113.85 | $290.33 |

How the high row is built: the uncapped-memory high figure plus 20 × N × $0.291 for a third call per investigation. For 200 sessions that is $232.07 + $58.26. These cover COPS governance only; coding-agent cost (§6.2) is extra.

## 7. Optimisation

### 7.1 Where the tokens and time go

- **Highest cost:** Governance Architecture ($0.180), Anomaly Detection ($0.152), Incident Response ($0.147).
- **Slowest:** Governance Architecture (26.0 s per investigation), Anomaly Detection (23.4 s), Incident Response (22.6 s).
- **Lowest cost:** Knowledge & Policy Management ($0.090), Context & Memory ($0.091).
- **Output is the main cost driver** (63%). Thinking is probably part of it but was not split out (§4.2).
- **Repeated context:** assess re-sends what plan already saw. Measured: 68,081 of the 164,342 assess input tokens (41%) repeat the plan payload. The two calls differ from the very first payload key (`purpose`), and use different response schemas. So only the system prompt, about 2,100–2,500 characters per COP, is a shared prefix. That is close to the minimum cacheable length.
- **Large tool results:** Governance Architecture's assess payload is about 15,000 characters, mostly the catalogue and coverage tool results.

### 7.2 Levers, ranked by expected saving

| # | Lever | Expected saving | Risk to quality | Change needed |
|---|---|---|---|---|
| 1 | **Cap memory in the payload** (for example the 5 most relevant records per COP) | Grows with scale: 30% of the 200-session expected cost ($141.20 → $98.58) | Low if ranking keeps contradicting and `verified_pending` records | `MemoryGateway.read` / `graph._payload` |
| 2 | **Conditional activation** (model only when the rule baseline is not quiet or a signal fires) | 70% at 3 of 10 investigations | **High as specified today**: see §7.3 | runner / graph |
| 3 | **Smaller model for simpler COPs**: Classification & Tagging (NONE cases only), Context & Memory, Knowledge & Policy Management, Governance Intelligence, Governance Architecture | Those 5 are 46% of cost; moving them cuts about 43% of the total | Medium: more verifier rejections are possible. Needs a live re-run to measure, and that re-run needs approval | `COPS_MODEL` per COP |
| 4 | **Trim large tool results** (Governance Architecture's `_result_view`) | Part of Governance Architecture's 14% share; not measured | Low if the cited fields are kept | `graph._result_view` |
| 5 | **Set effort explicitly** (`COPS_EFFORT`) | Unknown until measured; output is 63% of cost | Medium: weaker reasoning on abnormal cases | config only |
| 6 | **Prompt caching** after reordering the payload so the shared parts come first and `purpose` and `budget_remaining` come last | about 5–13% of model cost | None | `graph._payload` order, `cache_control` |
| 7 | **Batch API** for offline simulation runs | 50% of model cost | None, results arrive later. Not for live governance | runner |

**Not recommended:**
- dropping the assess call;
- merging plan and assess into one call;
- skipping verifiers;
- skipping missing-evidence cases.

Each of these saves cost by removing the evidence gate or verifier independence that the evaluation exists to test.

### 7.3 Conditional activation was checked against the 30 live cases

The trigger tested was: the rule baseline is not quiet, OR any attention metric is set. It would have skipped 10 of the 30 live cases:

- **The other 9 normal cases.** Their ground truth is quiet, so skipping them loses no finding.
- **Governance Architecture normal.** This was the run's one false positive (the model read GAP), so skipping it would have helped.
- **Workload Optimization abnormal.** **Skipping it would miss a true finding.** The rule baseline reads EFFICIENT, but the truth is REDUNDANT, and the model found it.

So conditional activation must not be adopted as specified. It first needs either a fix to the Workload Optimization rule or triggers based on tool signals. It can be re-checked on the test-bench replay at no cost.

## 8. Recommended cost controls

1. **Keep the hard spend caps on.** `COPS_LIVE_MAX_TOTAL_USD` and `LIVE_BUDGET` stopped the run within budget. Keep them for every live run and simulation.
2. **Cap memory in the payload before any multi-session run.** Without a cap, cost per session grows with every session before it (§6.1).
3. **Record the missing measurements** (§10) before the 20-user run, so its bill can be checked.
4. **Use the Batch API for the 20-user simulation**, if confirmed for this model. It halves model cost and results are not needed in real time.
5. **Run conditional activation only after the Workload Optimization fix**, and only after validating it on the test bench.
6. **Re-evaluate a smaller model for the 5 simpler COPs** in a separate approved live run, comparing rejection rates and agreement with the current results.
7. **Alert at 50% and 80% of the budget per run**, and stop at 100% (the current behaviour).

## 9. Formulas

- Call cost = `input_tokens` × input price + `output_tokens` × output price (`llm.LLMGateway._cost`). Here: in × 2e-6 + out × 1e-5.
- COP cost = Σ call cost over the COP's calls.
- Cost per investigation = COP cost / 3.
- Cost per scenario case = Σ cost over the case's calls (2 per case).
- Cost per COP invocation = total cost / 126.
- Compute cost = local ms × $0.05 / 3,600,000 (assumed vCPU price).
- Single full run (low / expected / high) = Σ over 10 LLM COPs of min / mean / max investigation cost; high × 1.10.
- Conditional session cost = k × $0.042203, k = 1 / 3 / 6.
- Memory extra cost = 2 calls × r tokens × $2e-6 × Σ_sessions [7 × min(p × i, cap) + 3 × min(p × j, cap)]. Here i is the tenant session index, j is the user's own session index, and the conditional case scales by k / 10.
- Extra-call sensitivity = (calls − 2) × $0.029128 × investigations per session.

## 10. Missing measurements

| Missing | Effect on this report | How to record it |
|---|---|---|
| Per-call and per-case timestamps | The run's wall-clock duration is only a lower bound | add `started_at` / `ended_at` in `live_eval.run_one` |
| Per-node timings in the live run | Local compute and memory times come from replay | keep node timings in the live record |
| Cache usage fields | Cached tokens cannot be confirmed as 0 | record `cache_read_input_tokens` and `cache_creation_input_tokens` |
| Thinking-token split | The share of output spent on thinking is unknown | record the usage output details |
| Tool results in the live record | Tool-call counts come from replay | store tool results, or at least their counts and sizes |
| Fleet model replies | The fleet runs cannot be replayed | store the replies as in `live_results.json` |
| Smoke and fix-check run costs | The $0.43 comes from the session tally, not a file | write every live run's totals to a results file |
| Provider billing | The cost is calculated, not invoiced | match the request ids against the console usage export |
| Coding-agent spend | The §6.2 figures are placeholders | measure or obtain the agents' cost per session |
| Infrastructure pricing | Compute cost uses an assumed $0.05/vCPU-hour | use the deployment's real price |
| A full 18-COP run | §5 is assembled, not observed | one approved run of `build_case_graph` over all 18 |
