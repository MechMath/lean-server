from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Final


COUNTER_NAMES: Final[tuple[str, ...]] = (
    "compile_requests_total",
    "verification_requests_total",
    "compile_semantic_failures_total",
    "verification_semantic_failures_total",
    "queue_timeouts_total",
    "execution_timeouts_total",
    "worker_crashes_total",
    "lean_panics_total",
    "worker_protocol_errors_total",
    "worker_message_too_large_total",
    "worker_replacements_total",
    "replacement_startup_failures_total",
    "overload_responses_total",
)
METRICS_SCHEMA_VERSION: Final[int] = 1


@dataclass(frozen=True, slots=True)
class MetricsSnapshot:
    started_at: str
    uptime_seconds: float
    counters: dict[str, int]

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": METRICS_SCHEMA_VERSION,
            "started_at": self.started_at,
            "uptime_seconds": self.uptime_seconds,
            "counters": self.counters,
        }


class ServiceMetrics:
    """Thread-safe process-lifetime counters shared by HTTP and the pool loop."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._started_monotonic = time.monotonic()
        self._started_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        self._counters = dict.fromkeys(COUNTER_NAMES, 0)

    def increment(self, name: str) -> None:
        if name not in self._counters:
            raise KeyError(f"unknown metrics counter {name!r}")
        with self._lock:
            self._counters[name] += 1

    def snapshot(self) -> MetricsSnapshot:
        with self._lock:
            counters = dict(self._counters)
        return MetricsSnapshot(
            started_at=self._started_at,
            uptime_seconds=max(0.0, time.monotonic() - self._started_monotonic),
            counters=counters,
        )
