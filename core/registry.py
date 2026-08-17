"""
CollectorRegistry: holds collector instances, runs detection, activates
whichever collectors are actually present, and merges their capture()
streams into one iterator -- isolating failures so one broken collector
never takes down the others.
"""

from __future__ import annotations

import itertools
import sys
import traceback
from dataclasses import dataclass, field
from typing import Dict, Iterator, List, Optional

from .interfaces import Collector, RawEvent


@dataclass
class CollectorFailure:
    collector_id: str
    stage: str  # "detect" | "activate" | "capture" | "deactivate"
    error: str


class CollectorRegistry:
    def __init__(self) -> None:
        self._collectors: Dict[str, Collector] = {}
        self._active_ids: List[str] = []
        self._failures: List[CollectorFailure] = []

    def register(self, collector: Collector) -> None:
        self._collectors[collector.collector_id] = collector

    @property
    def failures(self) -> List[CollectorFailure]:
        return list(self._failures)

    @property
    def active_ids(self) -> List[str]:
        return list(self._active_ids)

    def _record_failure(self, collector_id: str, stage: str, exc: Exception) -> None:
        msg = f"{type(exc).__name__}: {exc}"
        self._failures.append(CollectorFailure(collector_id, stage, msg))
        # stderr, not stdout: callers running `signal-agent --json run` rely
        # on stdout being nothing but the final JSON payload (see cli.py's
        # docstring contract). A diagnostic here must never land in that
        # stream, or downstream JSON parsing breaks.
        print(f"[registry] {collector_id} failed at {stage}: {msg}", file=sys.stderr)

    def run_detection(self) -> Dict[str, bool]:
        """Run detect() on every registered collector. Isolates exceptions --
        a collector that raises during detect() is treated as 'not detected'
        and recorded as a failure, but does not stop the others."""
        results: Dict[str, bool] = {}
        for cid, collector in self._collectors.items():
            try:
                results[cid] = bool(collector.detect())
            except Exception as exc:  # noqa: BLE001 - intentional broad isolation
                self._record_failure(cid, "detect", exc)
                results[cid] = False
        return results

    def activate_detected(self, detection_results: Optional[Dict[str, bool]] = None) -> List[str]:
        """activate() only the collectors whose detect() returned True.
        Returns the list of collector_ids successfully activated."""
        if detection_results is None:
            detection_results = self.run_detection()

        activated: List[str] = []
        for cid, detected in detection_results.items():
            if not detected:
                continue
            collector = self._collectors[cid]
            try:
                collector.activate()
                activated.append(cid)
            except Exception as exc:  # noqa: BLE001
                self._record_failure(cid, "activate", exc)

        self._active_ids = activated
        return activated

    def _safe_capture_stream(self, collector: Collector) -> Iterator[RawEvent]:
        """Wrap a single collector's capture() generator so an exception
        raised mid-stream is caught and recorded instead of propagating and
        killing the merged stream for every other collector."""
        try:
            for event in collector.capture():
                yield event
        except Exception as exc:  # noqa: BLE001
            self._record_failure(collector.collector_id, "capture", exc)

    def merged_capture(self) -> Iterator[RawEvent]:
        """Aggregate capture() from all *active* collectors into one merged
        stream. Uses round-robin interleaving across each collector's own
        (possibly finite, possibly infinite) generator so no single
        collector can starve the others out even if it's very chatty.
        """
        streams = {
            cid: self._safe_capture_stream(self._collectors[cid])
            for cid in self._active_ids
        }
        # Round-robin: keep cycling through live generators, dropping any
        # that are exhausted (StopIteration) or that failed internally
        # (the wrapper above already logged the failure and stops yielding).
        live = dict(streams)
        while live:
            exhausted = []
            for cid, gen in live.items():
                try:
                    yield next(gen)
                except StopIteration:
                    exhausted.append(cid)
            for cid in exhausted:
                live.pop(cid, None)

    def deactivate_all(self) -> None:
        for cid in list(self._active_ids):
            collector = self._collectors[cid]
            try:
                collector.deactivate()
            except Exception as exc:  # noqa: BLE001
                self._record_failure(cid, "deactivate", exc)
        self._active_ids = []
