"""
`core` package: the Collector Framework's foundation.

Re-exports everything the rest of this package (and any external caller)
needs, so `from core import Collector, RawEvent, CollectorRegistry,
CollectorFailure` keeps working exactly as it did before this package was
split into `core/interfaces.py` (the `Collector` ABC + `RawEvent`) and
`core/registry.py` (`CollectorRegistry`) -- this file is the only place
that boundary is visible.
"""

from __future__ import annotations

from .interfaces import Collector, RawEvent
from .registry import CollectorFailure, CollectorRegistry

__all__ = ["Collector", "RawEvent", "CollectorFailure", "CollectorRegistry"]
