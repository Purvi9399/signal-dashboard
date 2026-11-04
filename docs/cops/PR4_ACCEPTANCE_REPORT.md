# PR #4 technical acceptance report

**PR:** [Purvi9399/signal-dashboard#4](https://github.com/Purvi9399/signal-dashboard/pull/4), branch `feat/cops-langgraph-runtime`.
**Reviewed head:** `8a45418`. **Base:** `main` at `6a5c942`, which is unchanged.
**Prepared:** 2026-10-29. Everything here was run in the cloud sandbox:
- No paid model was called. Every model reply is a scripted fixture and is labelled simulated.
- Nothing was merged, and nothing was pushed after `8a45418`.
- Malhar's repository was not touched.

The fixes described below are local commits on the PR branch. They wait for your review before any push.

## Summary

| | Before (head `8a45418`) | After the fixes |
|---|---|---|
| pytest | 156 passed, 1 skipped (89 test functions) | **196 passed, 1 skipped** (123 test functions) |
| Evaluation scenarios | 24 of 24 passed | **25 of 25 passed** (E2E-full added) |
| Simulated LLM calls / tool calls | 93 / 257 | 114 / 314 |
| LLM COP runs: accepted / rejected / inconclusive | 33 / 5 / 4 of 42 | 39 / 8 / 5 of 52 |
| Quiet label reached while a required artifact was missing (all-18 audit, no artifacts) | 8 COPs | **0 COPs** |
| AU-01 can fail on the graph path | no | **yes**, on the original proposal |
| Runtime branch coverage: `graph.py` / `verifiers.py` / all runtime | 93% / 85% / 87% | **99% / 94% / 89%** |

These are the five discrepancies the review found, and how each was resolved:

1. **Missing evidence.** A new `runtime/requirements.py` declares, for each COP, which artifacts are REQUIRED, OPTIONAL or conditionally NOT_APPLICABLE. A REQUIRED artifact that is absent now withholds the quiet label. Deterministic rules return `MISSING_DEPENDENCY`, and DS-01 rejects an LLM's quiet label.
2. **AU-01.** It now runs on the original proposal before any substitution. The original, its check, the corrected proposal and the final authority outcome are recorded separately. Negative tests show it failing.
3. **All-18 scenario.** The description now lists what was supplied and what was not. Expectations now test findings. A new E2E-full scenario supplies every artifact.
4. **Untested error paths.** 35 new test cases cover them. Section 4.4 lists what is still uncovered.
5. **Ambiguous labels.** Result tables and reports now use the COP spec key, for example `runtime_monitoring`, instead of initials.

---

## Decisions requiring Malhar's approval

These are the seven items under "Needs agreement before porting to project-signal" in `docs/cops/RUNTIME.md`. Each has the technical choice the PR made, my recommendation and what it means for integration. Malhar's code could not be read from this session, so every comparison with his branches is unverified.

### 1. Contracts

- **What the PR chose.** `collector/cops/runtime/contracts.py` defines `Claim`, `Finding`, `VerifierResult`, `ProposedAction`, `MemoryRecord` and `LLMInvocation` as plain JSON dataclasses. They follow this repository's `cops/models.py`, and `decision_id` uses the same uuid5 formula as `COPDecision`. No mapping onto Malhar's `collector_framework/cops/contracts.py` exists.
- **Recommendation.** Keep these as the runtime's internal shape. Add one adapter module in the integration PR that converts to and from Malhar's public contracts, so his interfaces stay authoritative.
- **Alternatives.** Replace his contracts, which breaks his callers. Or rewrite the runtime onto his types without being able to read them.
- **Integration consequences.**
  - The new statuses may have no equivalent in his enums and must be mapped explicitly, not dropped: `INCONCLUSIVE`, `REJECTED_BY_VERIFIER`, `MISSING_DEPENDENCY`, `FAILED` and the seven execution statuses.
  - This review added fields that the adapter must carry: `evidence_requirements` and `gated_label` on findings; `original_proposal`, `original_verification`, `corrected_proposal` and `final_authority` on proposed actions; `subject` on verifier results.
  - If his `decision_id` formula differs, runtime and legacy rows stop upserting into the same row.
- **Affected COPs.** All 18.

### 2. Verifier ids

- **What the PR chose.** A new versioned registry, `verifiers.CHECKS` with `REGISTRY_VERSION = "2026-10-29.1"`, uses the ids EV/ID/MC/SC/DS/PE/AU/EX/MU/RP, each with a category and a declared effect. VR01–VR10 (PR #9), V01–V16 (Malhar's audited branch) and a possible seventeenth check could not be read, so VR06, VR07 and KP-1 were not reviewed.
- **Recommendation.**
  - Keep the category registry as canonical, and publish a mapping table from VRxx and Vxx to the new ids.
  - Review VR06, VR07 and KP-1 against the B8 test: can the check fail, and does it compute anything independently of the rule? Anything that only re-runs the rule becomes DIAGNOSTIC, like RP-01.
  - This review found the same defect in AU-01 and fixed it (see Discrepancy 2). Apply the same test to every legacy check.
- **Alternatives.** Keep Malhar's numbering and slot the new checks in. Running two registries side by side is not recommended.
- **Integration consequences.**
  - Stored `verifier_results`, and any dashboard that filters by old ids, need a migration or an alias table.
  - AU-01 now appears twice per action, with `subject` set to `original_proposal` or `final_action`. Any consumer must read `subject`.
- **Affected COPs.** All 18. KP-1 belongs to Knowledge & Policy Management.

### 3. Edges

- **What the PR chose.** `specs.CONSUMES` is the single source, and `downstream_cops` is derived from it. Against the old hand-written lists, `specs.edge_report()` reports 16 differences.
  - **14 edges consumed but never declared:**
    - Runtime Monitoring → Agent Lifecycle Management, Anomaly Detection, Behavior Analytics, Context & Memory, Interaction Discovery, Policy Enforcement, Resource Management, Workflow Orchestration and Workload Optimization
    - Interaction Discovery → Agent Lifecycle Management and Incident Response
    - Incident Response → Governance Intelligence and Risk Mitigation
    - Policy Enforcement → Knowledge & Policy Management
  - **2 edges declared but not consumed**, removed to break cycles: Risk Mitigation → Incident Response, and Workload Optimization → Resource Management.
- **Recommendation.** Accept the 14 as declarations, because they are data flows the rules actually read. Keep the current direction for the two cycles within a case. Carry the reverse influence across cases through memory: Incident Response already reads `incident_outcome`, and Risk Mitigation writes `mitigation_outcome`.
- **Alternatives.** Allow iterative cycles within a case, which reopens double counting. Or reverse each cycle.
- **Integration consequences.**
  - If Malhar's graph schedules in a different order, the consumption log marks inputs `absent` and downstream COPs decide without them.
  - Spec files on both sides must use the same `CONSUMES` table.
- **Affected COPs.** 14: Runtime Monitoring, Interaction Discovery, Agent Lifecycle Management, Anomaly Detection, Behavior Analytics, Context & Memory, Policy Enforcement, Resource Management, Workflow Orchestration, Workload Optimization, Incident Response, Governance Intelligence, Risk Mitigation and Knowledge & Policy Management.

### 4. The quiet-label rule (DS-01) and required evidence

- **What the PR chose.** A quiet label is refused in three cases. This review added the third.
  1. Runtime Monitoring says INSUFFICIENT.
  2. The COP's own metrics are UNKNOWN or MISSING under telemetry that is not SUFFICIENT.
  3. An artifact that `runtime/requirements.py` marks REQUIRED is unavailable.

  Which artifacts are REQUIRED, OPTIONAL or conditionally NOT_APPLICABLE is the semantic choice that needs agreement. The table is under Discrepancy 1.
- **Recommendation.** Keep all three conditions. They implement the brief's rule "do not interpret missing data as normal activity". Agree the requirements table line by line. The most debatable line is Resource Management needing a quota for NORMAL, because its question is "within entitlement and acceptable operating bounds".
- **Alternatives.**
  - Downgrade instead of refusing: keep the quiet label but cap its confidence and add a qualifier. This gives fewer INCONCLUSIVE results, but "normal" appears on blind spots.
  - Gate only on INSUFFICIENT telemetry. This is laxer.
- **Integration consequences.**
  - Deployments without the authoritative artifacts produce many INCONCLUSIVE or MISSING_DEPENDENCY findings. Before this change, the 20-user simulation would have shown NORMAL in those places.
  - In this repository, Resource Management now reads INCONCLUSIVE in C01, C03, C05 and E2E, because those scenarios supply no quota.
- **Affected COPs.** 12 through requirements: Workflow Orchestration, Context & Memory, Agent Lifecycle Management, Policy Enforcement, Recovery & Resilience, Classification & Tagging, Resource Management, Risk Mitigation, Governance Intelligence, Workload Optimization, Knowledge & Policy Management, and Incident Response (optional only). Every COP with a quiet label is affected through the telemetry conditions.

### 5. Activation (`specs.ACTIVE`)

- **What the PR chose.** `specs.ACTIVE` still lists four COPs for the legacy `COPRunner`: Runtime Monitoring, Anomaly Detection, Incident Response and Resource Management. The runtime runs all 18 regardless.
- **Recommendation.** Leave the flags unchanged here. In the integration PR, choose one canonical path, the legacy `COPRunner` and `harness.COPAgent` or the runtime, and retire or narrow the other.
- **Alternatives.** Mark all 18 active. The legacy runner has rule interpreters for only 8, so the other 10 would fail or produce nothing.
- **Integration consequences.** Both paths share the `decision_id` formula. If both write `cop_decisions` for the same session and COP, the last writer wins, so a legacy write can overwrite a verified runtime decision.
- **Affected COPs.** The four active ones directly, and all 18 through the shared decision row.

### 6. Decision schema

- **What the PR chose.** `collector/cops_runtime_schema.sql` adds 13 columns to `cop_decisions`:
  - `case_id`, `tenant_id`, `contract_version`, `cop_mode`, `decision_status`, `verified`
  - `finding`, `verifier_results`, `proposed_action`, `execution_status`
  - `llm_calls`, `tool_calls`, `node_timings`

  It also adds four tables: `cop_llm_invocations`, `cop_tool_calls`, `cop_memory_records` and `cop_memory_access_log`. The migration has not been applied anywhere, and RLS is not written (the file notes this at line 87). The fields added by this review live inside the existing `finding` and `proposed_action` jsonb columns, so no schema change was needed.
- **Recommendation.** Malhar reviews the migration. Apply it first to a non-production Supabase project, and write tenant RLS for the four new tables before any runtime write. Production comes last and needs your explicit approval.
- **Alternatives.** Use a single `runtime jsonb` column, or a separate `cop_runtime_decisions` table.
- **Integration consequences.**
  - If columns are missing, `SupabaseRuntimeWriter` refuses unknown columns. It fails closed, so runtime decisions are not persisted.
  - Without RLS, a direct database read can cross tenants and bypass the memory gateway's isolation, which is the only isolation tested.
- **Affected COPs.** All 18.

### 7. Authoritative sources

- **What the PR chose.** Ten artifacts exist only as synthetic fixtures (`runtime/synthetic.py`, `evaluation.py`):
  - policy store, entitlements, taxonomy, risk register
  - task plans, context snapshots, recovery records
  - agent registry, quotas, task classes

  Without them, tools return typed missing dependencies. After this review, the COPs that need them also withhold their quiet label.
- **Recommendation.** Name an owner and a source for each artifact, starting with the policy store and entitlements. Do not carry over the synthetic risk thresholds (EMERGING 2, MATERIAL 6, `synthetic.py:107`).
- **Alternatives.** Ship with typed missing dependencies until each source exists.
- **Integration consequences.**
  - A synthetic policy or threshold that reaches production would make COPs decide on invented ground.
  - Each real source needs an adapter that matches what the tool reads. `requirements.AVAILABLE_IF` defines what counts as present.
- **Affected COPs.** The 12 listed in the requirements table under Discrepancy 1.

---

## The five discrepancies: before and after

### Discrepancy 1: quiet labels despite missing required evidence

**How it was found.** All 18 COPs were run through one case graph on a normal session with **no artifacts at all**, using the generic scripted reply. The script is `audit_missing.py`; its output is reproduced verbatim below. **Before** (head `8a45418`):

```
workflow_orchestration       llm           NORMAL          SUBSTANTIVE  verified=True   missing=['task_plan']  <-- QUIET WITH MISSING
agent_lifecycle_management   deterministic UNCHANGED       SUBSTANTIVE  verified=True   missing=['agent_registry']  <-- QUIET WITH MISSING
policy_enforcement           deterministic ALLOWED         SUBSTANTIVE  verified=True   missing=['entitlements', 'policy_store']  <-- QUIET WITH MISSING
classification_tagging       llm           UNCLASSIFIED    SUBSTANTIVE  verified=True   missing=['taxonomy']  <-- QUIET WITH MISSING
resource_management          deterministic NORMAL          SUBSTANTIVE  verified=True   missing=['budget_or_quota']  <-- QUIET WITH MISSING
risk_mitigation              llm           LOW             SUBSTANTIVE  verified=True   missing=['risk_register']  <-- QUIET WITH MISSING
workload_optimization        llm           EFFICIENT       SUBSTANTIVE  verified=True   missing=['task_classes']  <-- QUIET WITH MISSING
```

Eight COPs reached a verified quiet label. Seven are listed above. The eighth is Policy Enforcement's dependence on entitlements, counted separately because it applies only when an MCP server was used. Three other COPs were already safe: Context & Memory, Governance Intelligence and Knowledge & Policy Management went INCONCLUSIVE, and Recovery & Resilience returned MISSING_DEPENDENCY.

**After** (same audit):

```
workflow_orchestration       llm           INCONCLUSIVE    REJECTED_BY_VERIFIER   missing=['task_plan']
context_memory               llm           INCONCLUSIVE    INCONCLUSIVE           missing=['context_snapshots']
agent_lifecycle_management   deterministic INCONCLUSIVE    MISSING_DEPENDENCY     missing=['agent_registry']
policy_enforcement           deterministic INCONCLUSIVE    MISSING_DEPENDENCY     missing=['entitlements', 'policy_store']
recovery_resilience          deterministic INCONCLUSIVE    MISSING_DEPENDENCY     missing=['recovery_records']
classification_tagging       llm           INCONCLUSIVE    REJECTED_BY_VERIFIER   missing=['taxonomy']
resource_management          deterministic INCONCLUSIVE    MISSING_DEPENDENCY     missing=['budget_or_quota']
risk_mitigation              llm           INCONCLUSIVE    REJECTED_BY_VERIFIER   missing=['risk_register']
governance_intelligence      llm           INCONCLUSIVE    INCONCLUSIVE           missing=['decision_store']
workload_optimization        llm           INCONCLUSIVE    REJECTED_BY_VERIFIER   missing=['task_classes']
knowledge_policy_management  llm           INCONCLUSIVE    INCONCLUSIVE           missing=['policy_store']
```

Runtime Monitoring, Behavior Analytics, Anomaly Detection, Interaction Discovery, Incident Response, Audit & Traceability and Governance Architecture still reach their quiet label. They need no external artifact, because their evidence is session telemetry.

**Evidence classification**, from `runtime/requirements.py` `REQUIREMENTS`. A REQUIRED artifact gates only the quiet label unless the table says otherwise. An attention label must cite observed evidence, and SC-02 and DS-01 check that.

| COP | Artifact | Kind | What it backs |
|---|---|---|---|
| Workflow Orchestration | `task_plan` | REQUIRED | NORMAL: progress against the declared objective |
| Context & Memory | `context_snapshots` | REQUIRED | CONTEXT_OK: continuity across compaction |
| Agent Lifecycle Management | `agent_registry` | REQUIRED | UNCHANGED: comparison with the approved configuration |
| Policy Enforcement | `policy_store` | REQUIRED | ALLOWED: rule evaluation against an approved version |
| Policy Enforcement | `entitlements` | REQUIRED if an MCP server was used, else NOT_APPLICABLE | ALLOWED: MCP servers used are entitled |
| Recovery & Resilience | `recovery_records` | REQUIRED if something failed or was reverted, else NOT_APPLICABLE | RECOVERED |
| Incident Response | `policy_store` | OPTIONAL | approval requirements of a proposed response |
| Classification & Tagging | `taxonomy` | REQUIRED, **gates every label** | approved family definitions |
| Resource Management | `budget_or_quota` | REQUIRED | NORMAL: consumption within entitlement |
| Risk Mitigation | `risk_register` | REQUIRED | LOW: impact and thresholds |
| Governance Intelligence | `decision_store` | REQUIRED | NO_PATTERN: trends across sessions |
| Workload Optimization | `task_classes` | REQUIRED | EFFICIENT: like-for-like comparison |
| Knowledge & Policy Management | `policy_store` | REQUIRED | CURRENT: approved policy versions |
| Runtime Monitoring, Behavior Analytics, Anomaly Detection, Interaction Discovery, Audit & Traceability, Governance Architecture | – | none | session telemetry and own memory |

The "What it backs" text is taken from each tool's own `required_for` message in `tools.py`, not invented.

**Code changes:**
- `runtime/requirements.py` is new. It holds `REQUIREMENTS`, `AVAILABLE_IF`, `assess_requirements()` and `required_unavailable()`.
- `graph._gate_required()` is applied in `computational_baseline`. It covers every deterministic decision and every LLM COP's baseline.
- A DS-01 condition was added in `verifiers.verify_finding`, with the failure detail "required evidence unavailable: …".
- `evidence_requirements` and `gated_label` were added to every finding, in `graph._as_finding`.

**Behaviour change in the evaluation.** Every expectation still passes. These findings changed:

| Scenario | COP | Before | After | Why |
|---|---|---|---|---|
| C01, C03, C05 | `resource_management` | NORMAL | INCONCLUSIVE / MISSING_DEPENDENCY | no quota supplied |
| C01 | `workload_optimization` | EFFICIENT | INCONCLUSIVE / REJECTED_BY_VERIFIER (DS-01) | no task classes |
| E2E | `workflow_orchestration` | NORMAL | INCONCLUSIVE / REJECTED_BY_VERIFIER (DS-01) | no task plan |
| E2E | `resource_management` | NORMAL | INCONCLUSIVE / MISSING_DEPENDENCY | no quota |
| E2E | `workload_optimization` | EFFICIENT | INCONCLUSIVE / REJECTED_BY_VERIFIER (DS-01) | no task classes |

**One test expectation changed.** `tests/test_runtime_graph.py::test_resource_management_alternate_outcomes` used to assert NORMAL with no quota, which was the defect. It now asserts NORMAL with an ample quota, and INCONCLUSIVE, `MISSING_DEPENDENCY` and `gated_label == "NORMAL"` without one.

### Discrepancy 2: AU-01 could not fail on the graph path

**Before.**
- `graph.authority_check` called `authority.check` first. That returned a permitted substitute: BLOCK became ALERT in dry run, and an unpermitted action became the COP's fallback.
- AU-01 then ran on the substitute, so it always passed. Its failure branches (`verifiers.py` lines 453 and 455) were never executed.
- Enforcement held, because `authority.check` is the gate. But the check could not fail, which is the B8 defect.

**After.**
- `verifiers.au01(spec, action, mode, subject)` is a single AU-01 implementation.
- `authority_check` runs it on the original proposal before `authority.check`, then records four things separately on `proposed_action`:
  - `original_proposal`: the action, reason and tier as the COP proposed them.
  - `original_verification`: AU-01 on that proposal, with `subject: "original_proposal"`. It can FAIL with `not_permitted` or `intervene_in_dry_run`.
  - `corrected_proposal`: `null` when nothing changed. Otherwise the substitute action, its tier, its source (`authority.check substitute` or `fallback action`) and the reason.
  - `final_authority`: the action that goes forward, AU-01 and PE-01 on it (`subject: "final_action"`), whether it is authorised, and the execution status.
- Both AU-01 results go into `verifier_results`. PE-01 can still block the final action, giving `NOT_EXECUTED_BLOCKED`.
- `VerifierResult` gained a `subject` field.

**Negative tests** (`tests/test_runtime_verifiers.py`):
- `test_au01_fails_directly_on_an_unauthorised_action`: Anomaly Detection BLOCK fails `not_permitted`, an action outside the vocabulary fails `not_permitted`, and Policy Enforcement BLOCK in dry run fails `intervene_in_dry_run`.
- `test_au01_fails_on_an_unauthorised_proposal_inside_the_graph`: a scripted model has Anomaly Detection propose BLOCK in supervised live mode.
  - The original fails AU-01 `not_permitted`.
  - The corrected proposal is WATCH, from the `authority.check` substitute.
  - AU-01 passes on the final action, and BLOCK never reaches execution.
- `test_au01_records_the_dry_run_substitution_as_a_failed_original`: Policy Enforcement BLOCK in dry run fails `intervene_in_dry_run` and is corrected to ALERT.
- `test_au01_passes_and_records_no_correction_for_a_permitted_proposal`.
- `test_runtime_error_paths.py::test_an_intervention_with_no_policy_basis_is_blocked_before_approval`: Incident Response proposes BLOCK in supervised mode with no policy store. The original passes AU-01, but PE-01 fails on the final action, so the status is `NOT_EXECUTED_BLOCKED` and nobody is asked to approve.

**Proof that the tests bite.** With `_gate_required`, the DS-01 requirement check and the original-proposal AU-01 switched off (`mutate.py`), 10 of the new tests fail. Two of the AU-01 graph tests are among them.

**Visible effect in the evaluation.** In C05, Policy Enforcement's original BLOCK fails AU-01 (`intervene_in_dry_run`) and is corrected to ALERT, so "Verifier fails" now lists `policy_enforcement AU-01`. The action that went forward is unchanged: ALERT, `NOT_EXECUTED_DRY_RUN`.

### Discrepancy 3: the all-18 scenario

**Before.**
- The ground truth read: "secret read and a denied proposed write; all artifacts present".
- Its five expectations tested only scheduling, persistence, one consumption, dry run and memory reuse.

**After.**

**E2E: "All 18 COPs through one case graph, five artifacts absent".**
- Ground truth: "secret read and a denied proposed write. Supplied: policy store, risk register, taxonomy, agent registry, entitlements, proposed operations. Not supplied: task plan, context snapshots, recovery records, quota, task classes."
- It has 13 expectations, all passing. Eight new ones test findings:
  - Policy Enforcement VIOLATION, verified.
  - Incident Response INCIDENT, verified.
  - Risk Mitigation EMERGING or MATERIAL, verified.
  - Each of Workflow Orchestration, Context & Memory, Recovery & Resilience, Resource Management and Workload Optimization withholds its quiet label **and names the missing artifact**.

**E2E-full (new): "All 18 COPs through one case graph, every artifact supplied".**
- The session is a deploy that failed, then a restore that passed 3 of 3 checks.
- All ten artifacts are supplied, plus a decision store. No constraint is dropped, and the quota is ample.
- Expectations were written before its first run (copy kept at 08:26 UTC):
  1. All 18 COPs ran in dependency order.
  2. No required artifact is unavailable for any COP.
  3. No finding is `MISSING_DEPENDENCY`.
  4. Recovery & Resilience RECOVERED, verified.
  5. The five COPs gated in E2E each reach a verified substantive finding.
  6. Policy Enforcement ALLOWED, verified.
  7. Incident Response does not read INCIDENT.
  8. Nothing executed.
- **First run: 7 of 8 passed.** Expectation 5 failed because Context & Memory stayed INCONCLUSIVE. Its computational baseline said CONTEXT_OK, but the generic scripted reply (`fixtures.generic_careful`) can cite only scalar tool fields, and the snapshot tools return lists.
- **Fix:** Context & Memory in this scenario uses its own investigation script (`evaluation.cm_steps`, the same one C09 uses), which concludes CONTEXT_OK when no constraint was breached. **The expectation was not changed.**
- **Current run: 8 of 8.** Labels:

```
runtime_monitoring SUFFICIENT · workflow_orchestration NORMAL (LLM) · context_memory CONTEXT_OK (LLM) ·
behavior_analytics STABLE · anomaly_detection INCONCLUSIVE (LLM) · interaction_discovery CONTAINED ·
agent_lifecycle_management UNCHANGED · policy_enforcement ALLOWED · recovery_resilience RECOVERED ·
incident_response WATCH (LLM) · classification_tagging UNCLASSIFIED (LLM) · resource_management NORMAL ·
risk_mitigation LOW (LLM) · governance_intelligence NO_PATTERN (LLM) · workload_optimization EFFICIENT (LLM) ·
audit_traceability COMPLETE · knowledge_policy_management CURRENT (LLM) · governance_architecture ADEQUATE (LLM)
```

Anomaly Detection's INCONCLUSIVE is the generic scripted reply declining to interpret an attention state, the failed deploy, that it has no script for. It is not a missing artifact.

### Discrepancy 4: untested error paths

**New file `tests/test_runtime_error_paths.py`: 35 cases in 30 functions.**

| Area | Tests |
|---|---|
| Required evidence | deterministic quiet label withheld for Agent Lifecycle Management, Policy Enforcement and Resource Management (parametrised); DS-01 rejects an LLM quiet label for Workflow Orchestration, Classification & Tagging, Risk Mitigation and Workload Optimization (parametrised); attention labels not gated; entitlements conditional on MCP use; recovery records not applicable without a failure; optional evidence never gates; every requirement well formed |
| Confidence ceiling | a verified ANOMALOUS at 0.97 is capped to 0.9 and qualified |
| Malformed or incomplete model output | detail failing the COP schema gives `detail_invalid`; `conclude` with no finding gives `conclude_without_finding`; provider failure at the assess stage gives `llm_unavailable`, with both calls recorded; "more evidence" naming no tool stops with no further call; tool-budget exhaustion gives `budget_exhausted:*` |
| Rule, gateway, memory | a rule that raises gives a `FAILED` finding and no action; an enforcement gateway that raises gives `EXECUTION_FAILED`, `applied: False`, EX-01 FAIL and nothing applied; memory read and write denied are recorded, not fatal; no memory gateway is recorded |
| Verifier failure branches | EV-01 bad status, confidence and claim kind; ID-01 unsupplied metric and INVALID row; MC-01 metric row disagreeing with raw data; SC-01 misquoted and unverified upstream; DS-01 nothing judgeable, quiet on missing evidence, and attention from NOT_APPLICABLE rows only; PE-01 `no_policy_store`; AU-02 `awaiting_approval`; MU-01 not owned, unverified, no provenance, other tenant |
| Authority | an intervention with no policy basis is `NOT_EXECUTED_BLOCKED` before approval |

Already covered before this review, and still passing: malformed output and timeouts for all 10 LLM COPs (`test_runtime_llm.py`), and verifier rejection of a confident wrong answer (C16, `test_mc01_recomputes_from_raw_and_can_fail`).

**Coverage, measured with `coverage run --branch --source=collector/cops -m pytest tests/`:**

| Module | Before | After |
|---|---|---|
| `runtime/graph.py` | 93% | 99% |
| `runtime/verifiers.py` | 85% | 94% |
| `runtime/requirements.py` | – | 100% |
| `runtime/rules.py` | 87% | 89% |
| `runtime/tools.py` | 86% | 86% |
| `runtime/llm.py` | 89% | 89% |
| `runtime/memory.py` | 93% | 93% |
| all `runtime/` | 87% | 89% |

**Exact gaps that remain:**

- **`graph.py`:**
  - line 272: an INCONCLUSIVE label with status SUBSTANTIVE is normalised. No caller produces that combination.
  - line 334: the fallback that marks an INCONCLUSIVE rule as MISSING_DEPENDENCY. The rules set that status themselves.
  - line 680: a non-quiet, non-approval action in supervised mode (ALERT, PASS_TO_COP, REQUEST_CONTEXT) is set to `NOT_EXECUTED_NO_ACTION`.
  - `816->818`: persistence with no store.
  - line 943: `run_cop` returning an interrupt for a single COP. Interrupts are tested through `run_case`.
- **`verifiers.py`:**
  - lines 104–130 and 152–193: path-helper and raw-recompute edge returns.
  - line 243: fixture-style references from `make_fixtures.py`.
  - line 262: an OBSERVED row without provenance. It is unreachable, because a row without provenance is classified INVALID first.
  - line 277: a memory record not found.
  - line 384: the SC-02 `confidence_above_evidence` warning.
  - lines 419–420: the DS-01 `inconclusive_without_reason` warning.
- **`llm.py` 197–216 and 242:** `AnthropicProvider` and measured-token costing. They need the opt-in live test, which was not run.
- **`memory.py`:** 154 (invalid feedback outcome), 160 (feedback on an unknown record), 228 and 235–237 (get on a missing or other-tenant record), 247 (a baseline proposal citing a record from another tenant).
- **`rules.py` (89%) and `tools.py` (86%):** mostly alternate labels no scenario reaches yet. See the matrix's "labels not exercised" column and the table of unreachable labels in RUNTIME.md.
- **0% by design:** the CLI and generator scripts `runtime/matrix.py`, `cops/run.py`, `make_fixtures.py` and `gap_registry.py`. The legacy `harness.py` is at 52%.

### Discrepancy 5: ambiguous result labels

**Before.** `evaluation.table` abbreviated COPs by initials, so `RM` meant Runtime Monitoring, Resource Management and Risk Mitigation, and `WO` meant Workflow Orchestration and Workload Optimization. RUNTIME.md table C copied this.

**After.**
- `evaluation.table` writes the spec key in code font, for example `` `resource_management` ``.
- `docs/cops/evaluation/RESULTS.md` and RUNTIME.md table C were regenerated.
- `results.json`, the matrix and all logs already used spec keys. A search of `collector/cops/runtime/` and `docs/cops/` for the initials generator finds nothing left.
- This report uses full names or spec keys throughout.

---

## The 18-COP matrix (after the fixes)

**Common to every COP.**
- **Graph:** one compiled subgraph, `graph.build_cop_graph()`, with the branch chosen by `profiles.build_profiles()[cop]`.
- **Entry points:** `graph.run_cop(rt, cop)` for one COP; `graph.build_case_graph(cops, checkpointer)` and `graph.run_case` for several; `graph.resume_case` after an approval.
- **LLM path:** `computational_baseline → llm_plan_investigation → validate_tool_request → execute_authorised_tool → validate_tool_result → llm_assess_evidence`, looping, then `formulate_finding`. The model is called through `LLMGateway` with prompt `<cop>/prompt-1`, a per-COP detail schema, and a budget of 4 LLM calls and 6 tool calls.
- **Deterministic path:** `rules.<cop>` with `rules.DETERMINISTIC_TOOLS[cop]`, gated by `requirements.py`.
- **Verifiers:**
  - on the finding: EV-01 to DS-02
  - on the action: AU-01 on the original proposal, then PE-01, AU-01, AU-02 and EX-01 on the final action
  - on memory: MU-01
  - diagnostic: RP-01
- **Memory:** `MemoryGateway` over `SQLiteStore`, with the policy in `memory.POLICIES`.
- **Checkpoints:** `SqliteSaver`.

| COP | Path | Tools | Memory writes / scope | Required evidence | Verified attention labels reached | Tests beyond the generic ones |
|---|---|---|---|---|---|---|
| Runtime Monitoring | deterministic | get_collector_breakdown, check_collector_expectations | collector_reliability / tenant | – | DEGRADED, INSUFFICIENT | graph: degraded, insufficient; 23 scenarios |
| Workflow Orchestration | LLM | get_task_milestones, analyse_execution_transitions, inspect_delegation_traces, compare_workflow_failures, retrieve_trace_window | workflow_failure / agent | task_plan | STALLED | DS-01 required-evidence; C03, C07, C09, E2E, E2E-full |
| Context & Memory | LLM | get_context_snapshots, inspect_context_references, compare_instruction_versions, verify_context_provenance | context_integrity_finding / agent | context_snapshots | CONTEXT_LOSS | C09, D-CM, E2E, E2E-full |
| Behavior Analytics | deterministic | compute_sequence_statistics, compare_behaviour_history | behaviour_baseline / agent | – | DRIFTING, POOR_RECOVERY | graph, memory; rule-raises test |
| Anomaly Detection | LLM | retrieve_trace_window, compare_versioned_baseline, find_similar_validated_cases, calculate_deviation, analyse_tool_call_sequence | anomaly_observation / agent | – | ANOMALOUS, INDETERMINATE | confidence ceiling, schema, budgets, AU-01; 14 scenarios |
| Interaction Discovery | deterministic | build_interaction_graph, compare_known_routes | route_record / agent | – | NEW_ROUTE | C04, C05, C15, C15b, D-IR, E2E, E2E-full |
| Agent Lifecycle Management | deterministic | get_agent_registry, detect_configuration_changes | agent_profile / agent | agent_registry | CHANGED (NEW, DRIFTED in tests) | gate test; C04, E2E, E2E-full |
| Policy Enforcement | deterministic | evaluate_session_operations, check_entitlements | enforcement_record / agent | policy_store; entitlements if MCP used | VIOLATION, UNCERTAIN (APPROVAL_REQUIRED in tests) | gate test, AU-01 dry run, gateway raises; 8 scenarios |
| Recovery & Resilience | deterministic | get_recovery_records, validate_checkpoints | recovery_outcome / tenant | recovery_records if a failure occurred | FAILED_RECOVERY (PARTIAL_RECOVERY in tests) | C10a, C10b, E2E, E2E-full |
| Incident Response | LLM | get_incident_evidence, get_verified_findings, check_exposure, check_approval_requirements, get_incident_outcomes | incident_outcome / tenant | policy_store (optional) | INCIDENT, WATCH | PE-01 block test; 10 scenarios |
| Classification & Tagging | LLM | query_taxonomy, compare_validated_examples, inspect_event_metadata | classification_example / tenant | taxonomy (every label) | INTEGRITY | DS-01 required-evidence; C08, E2E, E2E-full |
| Resource Management | deterministic | compute_consumption, get_quota | usage_baseline / agent | budget_or_quota | EXCESSIVE (UNAUTHORIZED in tests) | gate test; C01–C03, C05, E2E, E2E-full |
| Risk Mitigation | LLM | read_risk_register, evaluate_control_prerequisites, get_mitigation_outcomes, calculate_risk_components | mitigation_outcome / tenant | risk_register | EMERGING | DS-01 required-evidence; C05, E2E, E2E-full |
| Governance Intelligence | LLM | query_aggregate_report, compare_time_windows, get_confirmed_incidents, get_previous_reviews | governance_review / tenant | decision_store | quiet only | C12, E2E, E2E-full |
| Workload Optimization | LLM | calculate_usage_rates, compare_task_classes, inspect_repeated_operations, evaluate_previous_changes | optimisation_outcome / tenant | task_classes | quiet only | DS-01 required-evidence; C01, C02, E2E, E2E-full |
| Audit & Traceability | deterministic | reconstruct_trace, check_decision_linkage | trace_gap / tenant | – | quiet only | C01, E2E, E2E-full |
| Knowledge & Policy Management | LLM | get_policy_versions, compare_policy_documents, inspect_rule_dependencies, check_authorisation_records | policy_conflict / tenant | policy_store | quiet only | E2E, E2E-full |
| Governance Architecture | LLM | inspect_cop_catalogue, query_coverage_matrix, validate_cop_relationships, get_confirmed_coverage_gaps | coverage_gap / tenant | – | GAP | C18, C18n, E2E, E2E-full |

Every LLM COP also has five parametrised cases in `test_runtime_llm.py`: wiring, unavailable, timeout, malformed and prohibited tool. The full generated matrix is RUNTIME.md section A and `docs/cops/evaluation/matrix.json`.

## Evaluation scenarios (after the fixes)

**How they run.**
- Command: `cd collector && python3 -m cops.runtime.evaluation`. The same scenarios run in pytest as `tests/test_runtime_evaluation.py::test_scenario[...]`.
- Synthetic timestamped observables go through the real metric layer (`signal_metrics.compute`) and the real graphs.
- Every model reply is scripted, and expectations are data in `evaluation.SCENARIOS`.

**Result: 25 of 25 passed.** The per-scenario table, with expectations, findings, verifier failures, actions, memory, latency and cost, is in RUNTIME.md section C and `docs/cops/evaluation/RESULTS.md`.

**A successful simulated model call is not a successful governance finding:**

| Layer | What counts as success | Across 25 scenarios |
|---|---|---|
| Simulated model interaction | the reply parsed against the schema, stayed within budget, and was recorded `simulated: true` | 114 of 114 calls; 0 provider or schema errors |
| Governance finding | the candidate passed every `BLOCKS_FINDING` verifier and is SUBSTANTIVE and verified | 39 of 52 LLM COP runs accepted; 8 rejected by a verifier; 5 inconclusive |
| Action | passed authority | dry run executes nothing; only C15 and C15b execute anything, and only against `MockEnforcementGateway` |

**The 8 rejections:**
- C06: Incident Response NO_INCIDENT on INSUFFICIENT telemetry.
- C07: Workflow Orchestration NORMAL with stalls UNKNOWN.
- C12: Anomaly Detection citing another tenant's record.
- C16: Anomaly Detection, a confident wrong spike. This is the case where an incorrect LLM answer is blocked.
- C17: Anomaly Detection citing poisoned memory.
- C01 and E2E: Workload Optimization EFFICIENT with no task classes (new).
- E2E: Workflow Orchestration NORMAL with no task plan (new).

**The 5 inconclusive:**
- C02: Workload Optimization, no task classes.
- C12: Governance Intelligence.
- E2E: Context & Memory and Classification & Tagging.
- E2E-full: Anomaly Detection, where the generic script does not interpret the failed deploy.

Many of the 39 accepted findings are generic quiet replies. They show that the verifiers accept a correct quiet reading, not that a model can judge one.

## Tests (after the fixes)

```
pip install -r collector/requirements-cops-runtime.txt
python3 -m pytest -p no:cacheprovider tests/ -q -rs
196 passed, 1 skipped in 8.30s
SKIPPED [1] tests/test_runtime_llm.py:169: opt-in live smoke test: needs COPS_LLM_LIVE=1, COPS_LLM_DATA_APPROVED=1, COPS_MODEL and credentials
```

The environment is Python 3.13.17, langgraph 1.2.18, langgraph-checkpoint-sqlite 3.1.2, pydantic 2.13.5 and pytest 9.2.0.

| File | Items (functions) | Category |
|---|---|---|
| `test_cops.py` | 30 (30) | legacy regression, unchanged |
| `test_runtime_persistence.py` | 6 (6) | B1 schema and writer |
| `test_runtime_llm.py` | 58 (13), 1 skipped | LLM gateway, 5 failure modes × 10 COPs, budgets, pricing, live guard |
| `test_runtime_graph.py` | 17 (17) | edges, B3 approvals, B7 branches, consumption. One test updated, described under Discrepancy 1 |
| `test_runtime_memory.py` | 8 (8) | memory |
| `test_runtime_verifiers.py` | 17 (17) | B4, B8, plus 4 AU-01 tests |
| `test_runtime_error_paths.py` | 35 (30) | new: error branches, described under Discrepancy 4 |
| `test_runtime_evaluation.py` | 26 (2) | 25 scenarios, plus the check that everything is simulated |
| **Total** | **197 (123)** | 196 passed, 1 skipped |

**Not run:**
- Malhar's 191-test baseline and `signal_cops`, which cannot be read from here.
- Any real Supabase.
- The live provider test.
- Any GitHub CI run: the repository has no Python workflow.

## Comparison with the earlier audit blockers

**Limitation:** PR #9 and Malhar's branches could not be read. GitHub access to `malhar-sovereign/project-signal` is refused. This comparison is therefore against the blockers **as described in the task brief**, re-implemented in signal-dashboard. It is not a diff against PR #9.

| Area | What PR #4 does now | Evidence | Still open |
|---|---|---|---|
| Supabase persistence (B1) | `persistence.TABLES` is the single column list; `decision_row()` asserts every column; `SupabaseRuntimeWriter` validates payloads, upserts on primary keys, and never posts without an explicit poster | `test_runtime_persistence.py` | migration not applied; no RLS; no real Supabase test; shared `decision_id` with the legacy writer |
| Memory retrieval | both policy gaps fixed; retrieval buckets established, verified-pending and unverified; only `record_feedback` validates; denials and absence recorded, not fatal | `test_runtime_memory.py`, C11, D-CM, D-IR, E2E; new memory-failure tests | no Supabase memory store; SQLite only |
| Verifier independence (B8) | categorised registry; RP-01 is diagnostic; MC-01 recomputes from raw data; **AU-01 now fails on an unauthorised original proposal** | `test_runtime_verifiers.py`, error-path tests, C16 | VR06, VR07 and KP-1 not reviewed |
| Approval interrupts (B3) | nested interrupt inside the case graph with an inherited checkpointer; fails closed; resumes after restart; **no-policy interventions are blocked before approval** | graph approval tests, C15, new PE-01 test | Postgres checkpointer not tested |
| Missing evidence (B4) | five evidence states; DS-01 on unobservable telemetry; **required artifacts gate quiet labels in rules and DS-01** | verifier tests, error-path tests, C06, C07, E2E | the requirements table needs agreement (Decision 4) |
| Action execution | seven execution statuses; mock-only execution; unconfirmed outcome or **raising gateway** gives `EXECUTION_FAILED`; **original and corrected proposals recorded** | graph tests, C15, C15b, new gateway test | no real enforcement path |
| B2 misleading claims | `harness.reverify` relabelled; limits stated; **E2E description corrected; AU-01 independence fixed** | RUNTIME.md | – |
| B5, B6, B7 | isolated as the seven decisions; edges derived from specs; the named tests exist | RUNTIME.md, `test_runtime_graph.py` | the seven decisions |

## Readiness

**PR #4 is ready for your review once the fix commits are pushed.**
- Tests: 196 passed, 1 skipped. Evaluation: 25 of 25.
- `main` has not moved, so there is no conflict.
- The PR does not touch `src/`, so the Vercel preview is unaffected.
- Nothing claims integration with project-signal.

**Malhar needs to approve** the seven decisions above. Decisions 1, 2, 3 and 6 change shared contracts or data. Decisions 4 and 5 are policy choices to make together, and Decision 4 now includes the requirements table. Decision 7 needs real artifact owners.

**Before a separate integration PR into project-signal:**
1. Get access: install the Claude GitHub App on `malhar-sovereign/project-signal`, or port the code yourself.
2. Run Malhar's 191-test baseline and `signal_cops` on PR #9 (`purvi/cops-langgraph-harnesses`) and `llm-first-run` before changing anything.
3. Write the contract adapter, including the new finding and action fields (Decision 1).
4. Map the verifier ids, and review VR06, VR07 and KP-1 against the B8 test (Decision 2).
5. Agree the edges and the cycle directions (Decision 3), and the DS-01 rule with its requirements table (Decision 4).
6. Choose one canonical runtime path, so two writers never share a `decision_id` (Decision 5).
7. Review the migration, add RLS, and apply it to a non-production Supabase only. Add a PostgREST-backed memory store (Decision 6).
8. Connect the real artifacts and remove the synthetic thresholds (Decision 7).
9. Re-run every suite and all 25 scenarios on that branch, and report any expectation that changes.
10. Optional: with your explicit approval and credentials, run `test_live_provider_smoke`. It is the only step that would make a paid model call.

## Remaining limitations

- **All model behaviour is scripted.** The harness, tool policy, budgets, verifiers, requirements gate and authority are exercised for real. Model judgement is not.
- **The requirements table is a semantic choice.** It is derived from each tool's `required_for` text, but nobody has approved it yet.
- **Memory is durable in SQLite only.** Enforcement is mock only. The injection scan is a regex list. Costs are unpriced unless `COPS_PRICING` is set.
- **There is no GitHub CI for the Python tests.**
- **Four COPs have shown only their quiet label with a verified finding:** Governance Intelligence, Workload Optimization, Audit & Traceability, and Knowledge & Policy Management.
