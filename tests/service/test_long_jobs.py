from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path

from lean_server.protocol import VerifyWorkerRequest, WorkerRequest
from lean_server.service import CompilerPool, PoolClosedError, PoolOverloadedError, PoolTimeoutError, PoolWorkerError
from lean_server.workers import WorkerProcessBackend


FAKE_WORKER = Path(__file__).parents[1] / "fixtures" / "fake_worker.py"


class LongJobTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.pool = CompilerPool(
            lambda: WorkerProcessBackend([sys.executable, str(FAKE_WORKER)]),
            worker_count=1, queue_capacity=1, long_worker_count=1,
            long_queue_capacity=1, long_request_threshold_seconds=0.02,
        )
        await self.pool.start()

    async def asyncTearDown(self) -> None:
        await self.pool.close()

    async def wait_for(self, condition) -> None:
        async with asyncio.timeout(3):
            while not condition(self.pool.snapshot):
                await asyncio.sleep(0.001)

    def verify(self, request_id: str, content: str, timeout: float = 5):
        return asyncio.create_task(self.pool.compile(
            VerifyWorkerRequest(request_id, "statement", content), timeout_seconds=timeout,
        ))

    async def test_saturated_long_lane_leaves_normal_capacity_available(self) -> None:
        first = self.verify("first", "__SLEEP__:0.3")
        await self.wait_for(lambda s: s.long_active_workers == 1)
        second = self.verify("second", "__SLEEP__:0.1")
        await self.wait_for(lambda s: s.long_queue_depth == 1)
        with self.assertRaisesRegex(PoolOverloadedError, "long verification queue"):
            await self.verify("overflow", "other")
        normal = await asyncio.wait_for(self.pool.compile(WorkerRequest("normal", "healthy")), 0.2)
        self.assertEqual(normal.status, "ok")
        self.assertFalse(first.done())
        short = await self.pool.compile(VerifyWorkerRequest("short", "statement", "healthy"),
                                       timeout_seconds=0.02)
        self.assertEqual(short.status, "ok")
        results = await asyncio.gather(first, second)
        self.assertEqual([r.request_id for r in results], ["first", "second"])

    async def test_queued_long_timeout_and_cancellation_release_capacity(self) -> None:
        first = self.verify("first", "__SLEEP__:0.3")
        await self.wait_for(lambda s: s.long_active_workers == 1)
        with self.assertRaises(PoolTimeoutError) as caught:
            await self.verify("expired", "healthy", timeout=0.05)
        self.assertEqual(caught.exception.compile_ms, 0)
        self.assertEqual(self.pool.snapshot.long_queue_depth, 0)
        cancelled = self.verify("cancelled", "healthy")
        await self.wait_for(lambda s: s.long_queue_depth == 1)
        cancelled.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await cancelled
        self.assertEqual(self.pool.snapshot.long_queue_depth, 0)
        await first

    async def test_long_timeout_replaces_only_long_worker_and_recovers(self) -> None:
        with self.assertRaises(PoolTimeoutError):
            await self.verify("expired", "__SLEEP__:1", timeout=0.05)
        normal = await self.pool.compile(WorkerRequest("normal", "healthy"))
        self.assertEqual(normal.status, "ok")
        following = await self.verify("following", "healthy")
        self.assertEqual(following.status, "ok")
        self.assertEqual(self.pool.snapshot.replacements, 1)
        self.assertEqual(self.pool.snapshot.long_active_workers, 0)

    async def test_panic_quarantine_is_shared_across_lanes(self) -> None:
        request = VerifyWorkerRequest("panic", "statement", "__NAT_POW_PANIC__")
        with self.assertRaises(PoolWorkerError):
            await self.pool.compile(request, timeout_seconds=5)
        with self.assertRaises(PoolWorkerError) as caught:
            await self.pool.compile(request, timeout_seconds=0.02)
        self.assertEqual(caught.exception.error_type, "LeanPanic")
        self.assertIn("quarantined", str(caught.exception))
        self.assertFalse(caught.exception.retryable)

    async def test_close_cleans_active_and_queued_long_jobs(self) -> None:
        first = self.verify("first", "__SLEEP__:30")
        await self.wait_for(lambda s: s.long_active_workers == 1)
        second = self.verify("second", "other")
        await self.wait_for(lambda s: s.long_queue_depth == 1)
        await self.pool.close()
        outcomes = await asyncio.gather(first, second, return_exceptions=True)
        self.assertTrue(all(isinstance(exc, PoolClosedError) for exc in outcomes), outcomes)
        self.assertEqual(self.pool.snapshot.long_active_workers, 0)
        self.assertEqual(self.pool.snapshot.long_queue_depth, 0)
