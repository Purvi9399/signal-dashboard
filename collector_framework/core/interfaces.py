"""
Core interface for the Collector Framework.

DESIGN NOTE (ABC vs Protocol):
We use abc.ABC (not typing.Protocol) because collectors are meant to be
explicitly registered, instantiated, and lifecycle-managed by the
CollectorRegistry -- an explicit nominal "is-a Collector" relationship is
what we want, not structural duck-typing. ABC also lets us provide a real
`abstractmethod` enforcement at instantiation time (a collector that forgets
to implement `capture()` fails fast/loudly at import+instantiation, rather
than at first call the way a Protocol would only be checked statically by
a type-checker and not at all at runtime).

SCOPE NOTE (deliberately out of scope here):
This interface is ONLY about getting raw, agent-shaped events off the
machine and into RawEvent envelopes. It intentionally does NOT define:
  - any policy/permission-checking hooks (e.g. "should this event be
    allowed to be captured", redaction rules, PII scrubbing, etc.)
  - any normalization / schema-mapping logic (turning Claude's
    "hook_event_name" + Cursor's "event_type" into one canonical event
    taxonomy, building the "tracejson" run objects webhook_server.py
    builds, etc.)
Both of those are the responsibility of later stages in the pipeline (a
"Policy Engine" stage and a "Normalizer" stage) that consume the RawEvent
stream this framework produces. Keeping this boundary sharp is what makes
collectors trivially portable across agents -- they just have to speak
"raw agent-native events in, RawEvent envelopes out".
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Iterator, Optional


@dataclass
class RawEvent:
    """A single, completely unprocessed event captured from an agent.

    `raw_payload` is intentionally untouched -- whatever shape/field-names
    the source agent used (Claude's `hook_event_name`, Cursor's
    `event_type`, etc.) are preserved verbatim. No renaming, no coercion,
    no filtering of fields. Normalization into a canonical shape is a
    later pipeline stage's job, not the collector's.
    """

    timestamp: str
    collector_id: str
    agent: str
    raw_payload: Dict[str, Any]

    @staticmethod
    def now_iso() -> str:
        return datetime.now(timezone.utc).isoformat()


class Collector(ABC):
    """Abstract base every telemetry collector must implement.

    Lifecycle, always in this order:
        detect() -> activate() -> capture() [repeatable/streaming] -> deactivate()

    A registry is expected to call detect() first and only activate()
    collectors that returned True. capture() may be called many times (it
    returns a fresh iterator each call) until deactivate() is invoked.
    """

    @property
    @abstractmethod
    def collector_id(self) -> str:
        """Stable identifier, e.g. 'claude_code.hooks' or 'cursor.hooks'."""
        raise NotImplementedError

    @abstractmethod
    def detect(self) -> bool:
        """Return True iff this collector's target agent/CLI is present on
        this machine right now. Must be side-effect-free and cheap (no
        network calls, no writes) -- it may be called speculatively/often.
        """
        raise NotImplementedError

    @abstractmethod
    def activate(self) -> None:
        """One-time setup after a positive detect(): register hooks, set
        env vars, spawn a wrapper/proxy process, open a watch handle, etc.
        Must be idempotent-safe to call once per registry run.
        """
        raise NotImplementedError

    @abstractmethod
    def capture(self) -> Iterator[RawEvent]:
        """Yield RawEvent objects as they become available. No normalization
        -- agent-specific field names/shapes are preserved as-is inside
        raw_payload. May be a live/blocking generator (e.g. tailing a hook
        socket) or a finite one (e.g. draining a queue/file).
        """
        raise NotImplementedError

    @abstractmethod
    def deactivate(self) -> None:
        """Clean shutdown: unregister hooks, close handles, stop processes."""
        raise NotImplementedError
