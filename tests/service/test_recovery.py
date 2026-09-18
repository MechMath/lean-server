from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path

from lean_server.protocol import WorkerRequest
from lean_server.service import CompilerPool, PoolClosedError, PoolTimeoutError, PoolWorkerError
from lean_server.workers import WorkerProcessBackend


FAKE_WORKER = Path(__file__).parents[1] / "fixtures" / "fake_worker.py"


def worker_factory() -> WorkerProcessBackend:
    return WorkerProcessBackend([sys.executable, str(FAKE_WORKER)])


async def wait_until_ready(pool: CompilerPool, count: int) -> None:
    for _ in range(200):
        if pool.snapshot.ready_workers == count:
            return
        await asyncio.sleep(0.01)
    raise AssertionError(f"pool did not recover to {count} ready workers: {pool.snapshot}")


class PoolRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.pool = CompilerPool(
            worker_factory,
            worker_count=2,
            queue_capacity=4,
            default_timeout_seconds=1.0,
        )
        await self.pool.start()

    async def asyncTearDown(self) -> None:
        await self.pool.close()

    async def test_timeout_replaces_worker_and_recovers_capacity(self) -> None:
        with self.assertRaises(PoolTimeoutError):
            await self.pool.compile(
                WorkerRequest("slow", "__SLEEP__:1"), timeout_seconds=0.05
            )
        await wait_until_ready(self.pool, 2)

        result = await self.pool.compile(WorkerRequest("following", "code"))
        self.assertEqual(result.status, "ok")
        self.assertEqual(self.pool.snapshot.replacements, 1)

    async def test_crash_replaces_only_failed_worker(self) -> None:
        unaffected = asyncio.create_task(
            self.pool.compile(WorkerRequest("unaffected", "__SLEEP__:0.1"))
        )
        with self.assertRaises(PoolWorkerError):
            await self.pool.compile(WorkerRequest("crash", "__CRASH__"))

        self.assertEqual((await unaffected).status, "ok")
        await wait_until_ready(self.pool, 2)
        self.assertEqual(self.pool.snapshot.replacements, 1)

    async def test_protocol_error_replaces_worker(self) -> None:
        with self.assertRaises(PoolWorkerError):
            await self.pool.compile(WorkerRequest("invalid", "__INVALID_JSON__"))
        await wait_until_ready(self.pool, 2)

        result = await self.pool.compile(WorkerRequest("following", "code"))
        self.assertEqual(result.status, "ok")

    async def test_close_cancels_active_and_queued_requests(self) -> None:
        active = asyncio.create_task(
            self.pool.compile(WorkerRequest("active", "__SLEEP__:10"))
        )
        while self.pool.snapshot.active_workers == 0:
            await asyncio.sleep(0)
        queued = asyncio.create_task(self.pool.compile(WorkerRequest("queued", "code")))
        await asyncio.sleep(0)

        await self.pool.close()

        for task in (active, queued):
            with self.assertRaises(PoolClosedError):
                await task


if __name__ == "__main__":
    unittest.main()
