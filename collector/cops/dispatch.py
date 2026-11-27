"""Action dispatcher — executes the action a COP decision selects.

Closes the pipeline: investigation → decision → authorised action.

Each dispatcher implements execute(decision) and returns an ActionResult
recording what happened. The runner calls the dispatcher after clamping
the action to the spec's permitted set.

Dispatchers:
  LogDispatcher        — writes to Python logging (always safe, default)
  WebhookDispatcher    — POSTs the decision to a URL
  CompositeDispatcher  — chains multiple dispatchers
"""

from __future__ import annotations

import json
import logging
import time
import urllib.request
import urllib.error
from abc import ABC, abstractmethod
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from .models import COPDecision, INTERVENING

logger = logging.getLogger("cops.dispatch")


@dataclass
class ActionResult:
    action: str
    cop: str
    session_id: str
    dispatched: bool = False
    dispatcher: str = ""
    timestamp: str = ""
    detail: str = ""
    error: Optional[str] = None

    def row(self) -> Dict[str, Any]:
        return asdict(self)


class ActionDispatcher(ABC):
    @abstractmethod
    def execute(self, decision: COPDecision) -> ActionResult:
        ...

    @property
    def name(self) -> str:
        return self.__class__.__name__


class LogDispatcher(ActionDispatcher):
    """Logs the action — suitable for dry-run and audit trails."""

    def __init__(self, level: int = logging.INFO):
        self._level = level

    def execute(self, decision: COPDecision) -> ActionResult:
        ts = datetime.now(timezone.utc).isoformat()
        msg = (f"COP {decision.cop} → {decision.action} "
               f"(session={decision.session_id}, "
               f"interpretation={decision.interpretation}, "
               f"severity={decision.severity})")
        if decision.action_reason:
            msg += f" reason: {decision.action_reason}"
        logger.log(self._level, msg)
        return ActionResult(
            action=decision.action, cop=decision.cop,
            session_id=decision.session_id, dispatched=True,
            dispatcher=self.name, timestamp=ts, detail=msg,
        )


class WebhookDispatcher(ActionDispatcher):
    """POSTs the decision to a webhook URL."""

    def __init__(self, url: str, timeout: float = 10.0, headers: Optional[Dict[str, str]] = None):
        self._url = url
        self._timeout = timeout
        self._headers = headers or {}

    def execute(self, decision: COPDecision) -> ActionResult:
        ts = datetime.now(timezone.utc).isoformat()
        payload = {
            "event": "cop_action",
            "cop": decision.cop,
            "action": decision.action,
            "session_id": decision.session_id,
            "interpretation": decision.interpretation,
            "severity": decision.severity,
            "action_reason": decision.action_reason,
            "evidence_status": decision.evidence_status,
            "decision_id": decision.decision_id,
            "decided_at": decision.decided_at,
            "dispatched_at": ts,
        }
        body = json.dumps(payload).encode()
        headers = {"Content-Type": "application/json", **self._headers}
        req = urllib.request.Request(self._url, data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                status = resp.status
            return ActionResult(
                action=decision.action, cop=decision.cop,
                session_id=decision.session_id, dispatched=True,
                dispatcher=self.name, timestamp=ts,
                detail=f"POST {self._url} → {status}",
            )
        except (urllib.error.URLError, OSError) as exc:
            return ActionResult(
                action=decision.action, cop=decision.cop,
                session_id=decision.session_id, dispatched=False,
                dispatcher=self.name, timestamp=ts,
                error=str(exc),
            )


class CallbackDispatcher(ActionDispatcher):
    """Calls a Python callable — for in-process agent control loops."""

    def __init__(self, callback):
        self._callback = callback

    def execute(self, decision: COPDecision) -> ActionResult:
        ts = datetime.now(timezone.utc).isoformat()
        try:
            result = self._callback(decision)
            return ActionResult(
                action=decision.action, cop=decision.cop,
                session_id=decision.session_id, dispatched=True,
                dispatcher=self.name, timestamp=ts,
                detail=str(result)[:500],
            )
        except Exception as exc:
            return ActionResult(
                action=decision.action, cop=decision.cop,
                session_id=decision.session_id, dispatched=False,
                dispatcher=self.name, timestamp=ts,
                error=str(exc),
            )


class CompositeDispatcher(ActionDispatcher):
    """Chains dispatchers — e.g. log + webhook."""

    def __init__(self, dispatchers: List[ActionDispatcher]):
        self._dispatchers = dispatchers

    def execute(self, decision: COPDecision) -> ActionResult:
        last = None
        for d in self._dispatchers:
            last = d.execute(decision)
        return last or ActionResult(
            action=decision.action, cop=decision.cop,
            session_id=decision.session_id, dispatched=False,
            dispatcher=self.name, detail="no dispatchers configured",
        )

    @property
    def name(self) -> str:
        return "+".join(d.name for d in self._dispatchers)


# Action routing: which actions actually get dispatched
NO_DISPATCH = frozenset({"NONE"})

# Actions that require live mode (they affect the running agent)
LIVE_ONLY = frozenset(INTERVENING)  # PAUSE, BLOCK

# Actions safe in observe mode (they don't change agent behaviour)
OBSERVE_SAFE = frozenset({"WATCH", "ALERT", "INSTRUCT", "REQUEST_CONTEXT",
                          "RECOMMEND", "REQUEST_APPROVAL", "PASS_TO_COP", "ESCALATE"})


def should_dispatch(action: str, mode: str) -> bool:
    """Whether an action should be dispatched in the given mode."""
    if action in NO_DISPATCH:
        return False
    if action in LIVE_ONLY and mode != "live":
        return False
    return True
