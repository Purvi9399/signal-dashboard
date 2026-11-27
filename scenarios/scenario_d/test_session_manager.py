"""Tests for the session lifecycle manager. Run: pytest test_session_manager.py -v"""

import pytest
from session_manager import (
    Session, SessionConfig, SessionManager, SessionState,
    InvalidTransition, SessionNotActive, SessionNotFound, DuplicateSession,
)


# ---------------------------------------------------------------------------
# Basic tests (these pass even with bugs, because they don't trigger them)
# ---------------------------------------------------------------------------

class TestSessionLifecycle:
    def test_create_session(self):
        session = Session("s-1")
        assert session.state == SessionState.INIT

    def test_start_session(self):
        session = Session("s-1")
        session.start()
        assert session.state == SessionState.ACTIVE

    def test_invalid_transition(self):
        session = Session("s-1")
        with pytest.raises(InvalidTransition):
            session.terminate()  # Can't go INIT → TERMINATED

    def test_record_tool_call(self):
        session = Session("s-1")
        session.start()
        entry = session.record_tool_call("read")
        assert entry["tool"] == "read"
        assert entry["seq"] == 0

    def test_cannot_record_when_not_active(self):
        session = Session("s-1")
        with pytest.raises(SessionNotActive):
            session.record_tool_call("read")


# ---------------------------------------------------------------------------
# Tests that expose Bug 1: shared mutable default in SessionConfig
# ---------------------------------------------------------------------------

class TestConfigIsolation:
    def test_default_configs_are_independent(self):
        """Two configs created with defaults should NOT share their tool lists.

        BUG 1: allowed_tools is a mutable default — both configs share the
        same list object. Appending to one appends to both.
        """
        config_a = SessionConfig()
        config_b = SessionConfig()

        config_a.allowed_tools.append("dangerous_tool")

        assert "dangerous_tool" not in config_b.allowed_tools, (
            "Configs share allowed_tools list — mutable default argument bug"
        )

    def test_session_tool_isolation(self):
        """Adding allowed tools to one session must not affect another.

        BUG 1: Sessions created with default config share the same
        allowed_tools list.
        """
        s1 = Session("s-1")
        s2 = Session("s-2")

        s1.config.allowed_tools.append("execute_code")

        s1.start()
        s2.start()

        # s2 should flag "execute_code" as unauthorized
        entry = s2.record_tool_call("execute_code")
        assert "violation" in entry, (
            "execute_code should be unauthorized for s2 — "
            "shared config allowed it through"
        )

    def test_policy_violation_detection(self):
        """Unauthorized tools must be caught even after another session allows them.

        BUG 1: After s1.config.allowed_tools.append("admin"), s2 also has "admin"
        in its allowed list because they share the same list.
        """
        s1 = Session("s-1")
        s1.start()
        s1.config.allowed_tools.append("admin_delete")

        s2 = Session("s-2")
        s2.start()

        s2.record_tool_call("admin_delete")
        assert len(s2._violations) > 0, (
            "admin_delete should be a violation for s2"
        )


# ---------------------------------------------------------------------------
# Tests that expose Bug 2: checkpoint corruption from in-place mutation
# ---------------------------------------------------------------------------

class TestCheckpointIntegrity:
    def test_suspend_checkpoint_survives_terminate(self):
        """A suspended session's checkpoint should be preserved even after termination.

        BUG 2: _save_checkpoint mutates self._checkpoint in place.
        After suspend() then terminate(), the suspend checkpoint is gone.
        """
        session = Session("s-1")
        session.start()
        session.record_tool_call("read")
        session.record_tool_call("write")

        session.suspend(reason="user_pause")
        suspend_cp = session.get_checkpoint()

        assert suspend_cp["reason"] == "user_pause"
        assert suspend_cp["is_terminal"] is False
        assert suspend_cp["tool_calls"] == 2

        # Now resume and terminate
        session.resume()
        session.record_tool_call("search")
        session.terminate(reason="completed")

        # The suspend checkpoint we captured earlier should still be valid
        assert suspend_cp["reason"] == "user_pause", (
            f"Suspend checkpoint was mutated: reason is now '{suspend_cp['reason']}'"
        )
        assert suspend_cp["is_terminal"] is False, (
            "Suspend checkpoint was mutated: is_terminal is now True"
        )

    def test_multiple_checkpoints_independent(self):
        """Each checkpoint capture should be an independent snapshot.

        BUG 2: get_checkpoint returns dict(self._checkpoint) which is shallow.
        After _save_checkpoint clears and refills the dict, previously
        captured references see the mutations (because clear() + update
        mutates the same underlying dict the shallow copy references).
        """
        session = Session("s-1")
        session.start()
        session.record_tool_call("read")

        session.suspend(reason="pause_1")
        cp1 = session.get_checkpoint()

        session.resume()
        session.record_tool_call("write")
        session.record_tool_call("search")

        session.suspend(reason="pause_2")
        cp2 = session.get_checkpoint()

        # cp1 and cp2 should be completely independent
        assert cp1["reason"] == "pause_1", (
            f"cp1 was mutated: reason={cp1['reason']}"
        )
        assert cp1["tool_calls"] == 1
        assert cp2["reason"] == "pause_2"
        assert cp2["tool_calls"] == 3

    def test_checkpoint_after_suspend_resume_cycle(self):
        """Suspend→Resume→Suspend should give correct checkpoint each time."""
        session = Session("s-1")
        session.start()

        checkpoints = []
        for i in range(3):
            session.record_tool_call(f"tool_{i}")
            session.suspend(reason=f"pause_{i}")
            checkpoints.append(session.get_checkpoint())
            session.resume()

        # Each checkpoint should reflect the state at that suspend point
        for i, cp in enumerate(checkpoints):
            assert cp["reason"] == f"pause_{i}", (
                f"Checkpoint {i}: expected reason='pause_{i}', got '{cp['reason']}'"
            )
            assert cp["tool_calls"] == i + 1, (
                f"Checkpoint {i}: expected tool_calls={i + 1}, got {cp['tool_calls']}"
            )


# ---------------------------------------------------------------------------
# Tests that expose Bug 3: class-level shared registry
# ---------------------------------------------------------------------------

class TestManagerIsolation:
    def test_separate_managers_are_independent(self):
        """Two manager instances should not share sessions.

        BUG 3: _registry is a class variable, so all managers share it.
        Creating a session in manager_a makes it visible in manager_b.
        """
        manager_a = SessionManager()
        manager_b = SessionManager()

        manager_a.create_session("shared-1")

        sessions_b = manager_b.list_sessions()
        assert len(sessions_b) == 0, (
            f"manager_b sees {len(sessions_b)} sessions — "
            "class-level registry is shared"
        )

    def test_create_duplicate_across_managers(self):
        """The same session_id in different managers should not conflict.

        BUG 3: Because the registry is shared, creating "s-1" in manager_a
        then "s-1" in manager_b raises DuplicateSession.
        """
        manager_a = SessionManager()
        manager_b = SessionManager()

        manager_a.create_session("s-1")
        # Should succeed — different manager
        try:
            session = manager_b.create_session("s-1")
            assert session.session_id == "s-1"
        except DuplicateSession:
            pytest.fail("Separate managers should allow same session_id")

    def test_terminate_all_scoped_to_manager(self):
        """terminate_all on one manager must not affect another's sessions.

        BUG 3: terminate_all iterates the shared registry, terminating
        sessions from ALL managers.
        """
        manager_a = SessionManager()
        manager_b = SessionManager()

        sa = manager_a.create_session("a-1")
        sa.start()

        sb = manager_b.create_session("b-1")
        sb.start()

        manager_a.terminate_all(reason="test cleanup")

        assert sb.state == SessionState.ACTIVE, (
            f"manager_b's session was terminated by manager_a: {sb.state}"
        )

    def test_active_count_scoped(self):
        """active_count should only count sessions in THIS manager.

        BUG 3: Counts sessions from all managers.
        """
        manager_a = SessionManager()
        manager_b = SessionManager()

        for i in range(3):
            s = manager_a.create_session(f"a-{i}")
            s.start()

        assert manager_b.active_count() == 0, (
            f"manager_b reports {manager_b.active_count()} active sessions"
        )


# ---------------------------------------------------------------------------
# Integration test — exercises all three bugs together
# ---------------------------------------------------------------------------

class TestFullLifecycle:
    def test_multi_session_governance(self):
        """Full lifecycle with multiple sessions, configs, checkpoints.

        Exercises:
        - Bug 1: default config sharing across sessions
        - Bug 2: checkpoint corruption across suspend/terminate
        - Bug 3: manager isolation
        """
        manager = SessionManager()

        # Session 1: standard config
        s1 = manager.create_session("gov-1")
        s1.start()
        s1.record_tool_call("read")
        s1.record_tool_call("write")
        s1.config.allowed_tools.append("deploy")

        # Session 2: should NOT have "deploy" in allowed tools
        s2 = manager.create_session("gov-2")
        s2.start()

        deploy_entry = s2.record_tool_call("deploy")
        assert "violation" in deploy_entry, (
            "'deploy' should be unauthorized for gov-2"
        )

        # Suspend s1, capture checkpoint
        s1.suspend(reason="review_needed")
        s1_cp = s1.get_checkpoint()

        # Terminate s1
        s1.resume()
        s1.terminate(reason="approved")

        # The suspend checkpoint we captured should be intact
        assert s1_cp["reason"] == "review_needed", (
            f"Suspend checkpoint corrupted: {s1_cp['reason']}"
        )
        assert s1_cp["is_terminal"] is False

        # s2 should still be active
        assert s2.state == SessionState.ACTIVE

        summary = s2.summary()
        assert summary["violations"] == ["unauthorized tool: deploy"]
