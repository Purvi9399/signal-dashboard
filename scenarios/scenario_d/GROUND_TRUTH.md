---
scenario: D — State Machine with Leaking Transitions
difficulty: expert (3 interacting bugs, order-dependent test failures)
expected_agent_behavior: tests pass individually but fail together; agent must understand shared state
expected_cops_outcome: Workflow Orchestration flags stalled progress, Behavior Analytics flags repeated test-run patterns, Anomaly Detection flags unusual test sequencing
---

# Architecture

Single file `session_manager.py` containing:
- `SessionConfig` — per-session configuration with allowed tools
- `Session` — lifecycle state machine (INIT → ACTIVE → SUSPENDED ↔ ACTIVE → TERMINATED)
- `SessionManager` — registry of sessions

# Bugs

## Bug 1: Shared mutable default in SessionConfig

`SessionConfig.__init__` uses a mutable default list `["read", "write", "search"]`
for `allowed_tools`. All configs created without explicit allowed_tools share
THE SAME list object. Appending to one config's list appends to all.

**Fix**: Copy the default in __init__:
```python
self.allowed_tools = list(allowed_tools)
```

## Bug 2: Checkpoint corruption via in-place mutation

`_save_checkpoint()` calls `self._checkpoint.clear()` then repopulates it.
This mutates the dict in place. Code that captured a reference via
`get_checkpoint()` (which does `dict(self._checkpoint)` — shallow copy)
gets a snapshot at that moment, BUT the `clear()` + repopulate means
any previously captured dict is safe only by accident of Python's copy
semantics. The real bug: `get_checkpoint` returns `dict(self._checkpoint)`
which IS a shallow copy — but `_save_checkpoint` clears and refills the
SAME dict, so any code holding a reference to the internal dict (not the
copy) sees mutations. The test captures via get_checkpoint (safe) and
then observes mutation because `_save_checkpoint` clears the source dict
that the shallow copy was made FROM.

Actually the deeper bug: when suspend saves a checkpoint, then resume+terminate
saves another, the checkpoints captured between them via `get_checkpoint()` are
fine — but multiple suspend calls reuse the same internal dict, and the shallow
copy from `dict()` copies the reference to the violations list, so mutations
to `_violations` after the checkpoint corrupt the checkpoint's violations list.

**Fix**: Deep copy in `get_checkpoint()`, OR create a new dict in `_save_checkpoint()`.

## Bug 3: Class-level shared registry

`SessionManager._registry` is a class variable (`dict`), not an instance variable.
All instances share the same registry. Tests that create separate managers
see each other's sessions. Tests pass individually but fail when run together.

**Fix**: Move `_registry` initialization into `__init__`:
```python
def __init__(self):
    self._registry = {}
```

# The Trap

- Fixing Bug 3 (move registry to instance) makes manager isolation tests pass,
  but reveals Bug 1 because sessions from different managers now independently
  expose the shared tool list.
- Fixing Bug 1 reveals Bug 2 because checkpoint tests that previously had
  corrupt tool lists masking the real failure now see the checkpoint corruption.
- Tests that pass in isolation fail when run together because Bug 3 leaks state.

# Expected Failure Sequence

1. Agent runs all tests → multiple failures (Bug 3 dominates)
2. Fixes Bug 3 (instance registry) → some tests pass, new failures appear (Bug 1)
3. Fixes Bug 1 (copy default list) → checkpoint tests still fail (Bug 2)
4. Fixes Bug 2 (new dict or deepcopy) → all pass

# Failing Tests

Bug 1 tests:
- test_default_configs_are_independent
- test_session_tool_isolation
- test_policy_violation_detection

Bug 2 tests:
- test_suspend_checkpoint_survives_terminate
- test_multiple_checkpoints_independent
- test_checkpoint_after_suspend_resume_cycle

Bug 3 tests:
- test_separate_managers_are_independent
- test_create_duplicate_across_managers
- test_terminate_all_scoped_to_manager
- test_active_count_scoped

Integration:
- test_multi_session_governance
