from __future__ import annotations

import asyncio
import unittest

from lean_server.protocol import WorkerRequest, WorkerResult
from lean_server.service import CompilerPool, PoolClosedError, PoolOverloadedError, PoolTimeoutError


def successful_result(request: WorkerRequest) -> WorkerResult:
    return WorkerResult(request.request_id, "ok", 1.0, (), ())


class RecordingBackend:
    def __init__(self, tracker: "BackendTracker") -> None:
        self.tracker = tracker

    async def start(self) -> None:
        self.tracker.started += 1

    async def compile(self, request: WorkerRequest) -> WorkerResult:
        self.tracker.order.append(request.request_id)
        self.tracker.active += 1
        self.tracker.max_active = max(self.tracker.max_active, self.tracker.active)
        self.tracker.entered.set()
        try:
            await self.tracker.gate.wait()
            return successful_result(request)
        finally:
            self.tracker.active -= 1

    async def close(self) -> None:
        self.tracker.closed += 1


class BackendTracker:
    def __init__(self, *, blocked: bool = False) -> None:
        self.started = 0
        self.closed = 0
        self.active = 0
        self.max_active = 0
        self.order: list[str] = []
        self.entered = asyncio.Event()
        self.gate = asyncio.Event()
        if not blocked:
            self.gate.set()

    def factory(self) -> RecordingBackend:
        return RecordingBackend(self)


class StartupTracker:
    def __init__(self) -> None:
        self.active = 0
        self.max_active = 0
        self.started = 0
        self.two_active = asyncio.Event()
        self.gate = asyncio.Event()

    def factory(self) -> BlockingStartupBackend:
        return BlockingStartupBackend(self)


class BlockingStartupBackend:
    def __init__(self, tracker: StartupTracker) -> None:
        self.tracker = tracker

    async def start(self) -> None:
        self.tracker.active += 1
        self.tracker.max_active = max(self.tracker.max_active, self.tracker.active)
        if self.tracker.active == 2:
            self.tracker.two_active.set()
        try:
            await self.tracker.gate.wait()
            self.tracker.started += 1
        finally:
            self.tracker.active -= 1

    async def compile(self, request: WorkerRequest) -> WorkerResult:
        return successful_result(request)

    async def close(self) -> None:
        pass


class CompilerPoolTests(unittest.IsolatedAsyncioTestCase):
    async def test_limits_parallel_worker_startup(self) -> None:
        tracker = StartupTracker()
        pool = CompilerPool(
            tracker.factory,
            worker_count=5,
            queue_capacity=1,
            startup_parallelism=2,
        )
        start_task = asyncio.create_task(pool.start())
        await asyncio.wait_for(tracker.two_active.wait(), timeout=1)

        self.assertEqual(tracker.max_active, 2)
        tracker.gate.set()
        await start_task
        self.assertEqual(tracker.started, 5)
        self.assertEqual(tracker.max_active, 2)
        await pool.close()

    async def test_limits_concurrency_to_worker_count(self) -> None:
        tracker = BackendTracker(blocked=True)
        pool = CompilerPool(tracker.factory, worker_count=2, queue_capacity=8)
        await pool.start()
        tasks = [
            asyncio.create_task(pool.compile(WorkerRequest(f"req-{index}", "code")))
            for index in range(6)
        ]
        while pool.snapshot.active_workers < 2:
            await asyncio.sleep(0)

        self.assertEqual(pool.snapshot.queue_depth, 4)
        self.assertEqual(tracker.max_active, 2)
        tracker.gate.set()
        await asyncio.gather(*tasks)
        await pool.close()
        self.assertEqual(tracker.started, 2)
        self.assertEqual(tracker.closed, 2)

    async def test_single_worker_preserves_fifo_order(self) -> None:
        tracker = BackendTracker(blocked=True)
        pool = CompilerPool(tracker.factory, worker_count=1, queue_capacity=3)
        await pool.start()
        tasks = [
            asyncio.create_task(pool.compile(WorkerRequest(request_id, "code")))
            for request_id in ("first", "second", "third")
        ]
        await tracker.entered.wait()
        tracker.gate.set()
        await asyncio.gather(*tasks)
        await pool.close()

        self.assertEqual(tracker.order, ["first", "second", "third"])

    async def test_rejects_when_pending_queue_is_full(self) -> None:
        tracker = BackendTracker(blocked=True)
        pool = CompilerPool(tracker.factory, worker_count=1, queue_capacity=1)
        await pool.start()
        active = asyncio.create_task(pool.compile(WorkerRequest("active", "code")))
        await tracker.entered.wait()
        queued = asyncio.create_task(pool.compile(WorkerRequest("queued", "code")))
        while pool.snapshot.queue_depth < 1:
            await asyncio.sleep(0)

        with self.assertRaises(PoolOverloadedError):
            await pool.compile(WorkerRequest("rejected", "code"))

        tracker.gate.set()
        await asyncio.gather(active, queued)
        await pool.close()

    async def test_rejects_after_close(self) -> None:
        tracker = BackendTracker()
        pool = CompilerPool(tracker.factory, worker_count=1, queue_capacity=1)
        await pool.start()
        await pool.close()

        with self.assertRaises(PoolClosedError):
            await pool.compile(WorkerRequest("late", "code"))

    async def test_queued_timeout_releases_capacity_without_executing_job(self) -> None:
        tracker = BackendTracker(blocked=True)
        pool = CompilerPool(tracker.factory, worker_count=1, queue_capacity=1)
        await pool.start()
        active = asyncio.create_task(pool.compile(WorkerRequest("active", "code")))
        try:
            await tracker.entered.wait()
            with self.assertRaises(PoolTimeoutError):
                await asyncio.wait_for(
                    pool.compile(WorkerRequest("expired", "code"), timeout_seconds=0.02),
                    timeout=1,
                )
            self.assertEqual(pool.snapshot.queue_depth, 0)
            self.assertEqual(tracker.order, ["active"])
            following = asyncio.create_task(pool.compile(WorkerRequest("following", "code")))
            await asyncio.sleep(0)
            tracker.gate.set()
            await asyncio.gather(active, following)
            self.assertEqual(tracker.order, ["active", "following"])
            self.assertEqual(pool.snapshot.replacements, 0)
        finally:
            await pool.close()
            await asyncio.gather(active, return_exceptions=True)

    async def test_cancelling_queued_request_releases_capacity(self) -> None:
        tracker = BackendTracker(blocked=True)
        pool = CompilerPool(tracker.factory, worker_count=1, queue_capacity=1)
        await pool.start()
        active = asyncio.create_task(pool.compile(WorkerRequest("active", "code")))
        try:
            await tracker.entered.wait()
            queued = asyncio.create_task(pool.compile(WorkerRequest("cancelled", "code")))
            await asyncio.sleep(0)
            queued.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await queued
            self.assertEqual(pool.snapshot.queue_depth, 0)
            tracker.gate.set()
            await active
            self.assertEqual(tracker.order, ["active"])
        finally:
            await pool.close()
            await asyncio.gather(active, return_exceptions=True)


if __name__ == "__main__":
    unittest.main()
