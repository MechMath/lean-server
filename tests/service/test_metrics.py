from __future__ import annotations

import unittest
from concurrent.futures import ThreadPoolExecutor

from lean_server.service import COUNTER_NAMES, ServiceMetrics


class ServiceMetricsTests(unittest.TestCase):
    def test_snapshot_starts_at_zero_and_rejects_unknown_counters(self) -> None:
        metrics = ServiceMetrics()
        snapshot = metrics.snapshot()

        self.assertEqual(tuple(snapshot.counters), COUNTER_NAMES)
        self.assertTrue(all(value == 0 for value in snapshot.counters.values()))
        self.assertTrue(snapshot.started_at.endswith("Z"))
        self.assertGreaterEqual(snapshot.uptime_seconds, 0)
        with self.assertRaises(KeyError):
            metrics.increment("not_a_counter")

    def test_concurrent_increments_are_not_lost(self) -> None:
        metrics = ServiceMetrics()

        def increment_many() -> None:
            for _ in range(1_000):
                metrics.increment("compile_requests_total")

        with ThreadPoolExecutor(max_workers=8) as executor:
            list(executor.map(lambda _: increment_many(), range(8)))

        self.assertEqual(metrics.snapshot().counters["compile_requests_total"], 8_000)


if __name__ == "__main__":
    unittest.main()
