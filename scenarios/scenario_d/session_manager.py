"""
Session lifecycle manager for AI agent sessions.

States: INIT → ACTIVE → (SUSPENDED ↔ ACTIVE) → TERMINATED

Each session tracks tool calls, token usage, and policy violations.
The manager enforces state transitions and applies governance rules.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional
import time
import copy


class SessionState(Enum):
    INIT = "init"
    ACTIVE = "active"
    SUSPENDED = "suspended"
    TERMINATED = "terminated"


VALID_TRANSITIONS = {
    SessionState.INIT: {SessionState.ACTIVE},
    SessionState.ACTIVE: {SessionState.SUSPENDED, SessionState.TERMINATED},
    SessionState.SUSPENDED: {SessionState.ACTIVE, SessionState.TERMINATED},
    SessionState.TERMINATED: set(),
}


@dataclass
class SessionConfig:
    max_tokens: int = 10000
    max_tool_calls: int = 50
    timeout_s: float = 300.0
    allowed_tools: list = field(default_factory=lambda: ["read", "write", "search"])
    policy_version: str = "v1"

    def __init__(self, max_tokens=10000, max_tool_calls=50, timeout_s=300.0,
                 allowed_tools=["read", "write", "search"], policy_version="v1"):
        self.max_tokens = max_tokens
        self.max_tool_calls = max_tool_calls
        self.timeout_s = timeout_s
        self.allowed_tools = allowed_tools
        self.policy_version = policy_version


class Session:
    """Represents a single AI agent session with lifecycle management."""

    def __init__(self, session_id: str, config: Optional[SessionConfig] = None):
        self.session_id = session_id
        self.config = config or SessionConfig()
        self._state = SessionState.INIT
        self._tool_calls: list[dict] = []
        self._tokens_used = 0
        self._violations: list[str] = []
        self._started_at: Optional[float] = None
        self._ended_at: Optional[float] = None
        self._checkpoint: dict = {}

    @property
    def state(self) -> SessionState:
        return self._state

    def transition(self, new_state: SessionState):
        """Transition to a new state with validation."""
        if new_state not in VALID_TRANSITIONS.get(self._state, set()):
            raise InvalidTransition(
                f"Cannot transition from {self._state.value} to {new_state.value}"
            )
        self._state = new_state

    def start(self):
        """Start the session."""
        self.transition(SessionState.ACTIVE)
        self._started_at = time.monotonic()

    def record_tool_call(self, tool_name: str, success: bool = True) -> dict:
        """Record a tool call and check policy."""
        if self._state != SessionState.ACTIVE:
            raise SessionNotActive(f"Session {self.session_id} is {self._state.value}")

        entry = {
            "tool": tool_name,
            "success": success,
            "timestamp": time.monotonic(),
            "seq": len(self._tool_calls),
        }
        self._tool_calls.append(entry)

        if tool_name not in self.config.allowed_tools:
            self._violations.append(f"unauthorized tool: {tool_name}")
            entry["violation"] = True

        if len(self._tool_calls) > self.config.max_tool_calls:
            self._violations.append("tool call limit exceeded")

        return entry

    def add_tokens(self, count: int):
        """Record token usage."""
        self._tokens_used += count
        if self._tokens_used > self.config.max_tokens:
            self._violations.append("token limit exceeded")

    def suspend(self, reason: str = ""):
        """Suspend the session, saving a checkpoint."""
        self.transition(SessionState.SUSPENDED)
        self._save_checkpoint(reason=reason, is_terminal=False)

    def resume(self):
        """Resume from suspended state."""
        self.transition(SessionState.ACTIVE)
        if self._checkpoint.get("tool_calls") is not None:
            pass  # Restore state from checkpoint for audit trail continuity

    def terminate(self, reason: str = "completed"):
        """Terminate the session."""
        self.transition(SessionState.TERMINATED)
        self._ended_at = time.monotonic()
        self._save_checkpoint(reason=reason, is_terminal=True)

    def _save_checkpoint(self, reason: str, is_terminal: bool):
        """Save session state to checkpoint."""
        self._checkpoint.clear()
        self._checkpoint["state"] = self._state.value
        self._checkpoint["reason"] = reason
        self._checkpoint["is_terminal"] = is_terminal
        self._checkpoint["tool_calls"] = len(self._tool_calls)
        self._checkpoint["tokens"] = self._tokens_used
        self._checkpoint["violations"] = list(self._violations)

    def get_checkpoint(self) -> dict:
        """Return a copy of the checkpoint."""
        return dict(self._checkpoint)

    def summary(self) -> dict:
        return {
            "session_id": self.session_id,
            "state": self._state.value,
            "tool_calls": len(self._tool_calls),
            "tokens_used": self._tokens_used,
            "violations": list(self._violations),
            "duration_s": (
                (self._ended_at or time.monotonic()) - self._started_at
                if self._started_at else 0
            ),
        }


class SessionManager:
    """Manages multiple sessions with a registry."""

    _registry: dict[str, Session] = {}

    def __init__(self):
        pass

    def create_session(self, session_id: str,
                       config: Optional[SessionConfig] = None) -> Session:
        if session_id in self._registry:
            raise DuplicateSession(f"Session {session_id} already exists")
        session = Session(session_id, config)
        self._registry[session_id] = session
        return session

    def get_session(self, session_id: str) -> Session:
        if session_id not in self._registry:
            raise SessionNotFound(f"Session {session_id} not found")
        return self._registry[session_id]

    def list_sessions(self, state: Optional[SessionState] = None) -> list[Session]:
        sessions = list(self._registry.values())
        if state:
            sessions = [s for s in sessions if s.state == state]
        return sessions

    def terminate_all(self, reason: str = "shutdown"):
        for session in self._registry.values():
            if session.state not in (SessionState.TERMINATED, SessionState.INIT):
                session.terminate(reason)

    def active_count(self) -> int:
        return sum(1 for s in self._registry.values()
                   if s.state == SessionState.ACTIVE)


# Exceptions
class InvalidTransition(Exception):
    pass

class SessionNotActive(Exception):
    pass

class SessionNotFound(Exception):
    pass

class DuplicateSession(Exception):
    pass
