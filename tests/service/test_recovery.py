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


async def wait_until_ready(pool: CompilerPool, count: int, *, replacements: int = 0) -> None:
    for _ in range(200):
        if pool.snapshot.ready_workers == count and pool.snapshot.replacements >= replacements:
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
        await wait_until_ready(self.pool, 2, replacements=1)

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

    async def test_internal_error_replaces_worker(self) -> None:
        with self.assertRaises(PoolWorkerError):
            await self.pool.compile(WorkerRequest("internal", "__INTERNAL_ERROR__"))
        await wait_until_ready(self.pool, 2)

        result = await self.pool.compile(WorkerRequest("following", "code"))
        self.assertEqual(result.status, "ok")

    async def test_drained_oversize_response_preserves_capacity(self) -> None:
        for attempt in range(3):
            with self.assertRaises(PoolWorkerError) as caught:
                await self.pool.compile(
                    WorkerRequest(str(attempt), "__RESPONSE_BYTES__:8388609"), timeout_seconds=5,
                )
            self.assertEqual(caught.exception.error_type, "WorkerMessageTooLarge")
            self.assertFalse(caught.exception.retryable)
        self.assertEqual(self.pool.snapshot.replacements, 0)
        result = await self.pool.compile(WorkerRequest("following", "healthy"))
        self.assertEqual(result.status, "ok")

    async def test_unterminated_oversize_response_replaces_worker_and_recovers(self) -> None:
        with self.assertRaises(PoolWorkerError) as caught:
            await self.pool.compile(WorkerRequest("unterminated", "__OVERSIZE_FLOOD__"),
                                    timeout_seconds=5)
        self.assertFalse(caught.exception.retryable)
        self.assertEqual(caught.exception.error_type, "WorkerMessageTooLarge")
        await wait_until_ready(self.pool, 2, replacements=1)
        result = await self.pool.compile(WorkerRequest("following", "healthy"))
        self.assertEqual(result.status, "ok")

    async def test_known_panic_is_not_retryable_and_capacity_recovers(self) -> None:
        with self.assertRaises(PoolWorkerError) as caught:
            await self.pool.compile(WorkerRequest("panic", "__NAT_POW_PANIC__"))
        self.assertFalse(caught.exception.retryable)
        self.assertEqual(caught.exception.error_type, "LeanPanic")
        await wait_until_ready(self.pool, 2, replacements=1)
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

    async def test_stderr_flood_does_not_prevent_crash_recovery(self) -> None:
        with self.assertRaises(PoolWorkerError):
            await self.pool.compile(
                WorkerRequest("flood-crash", "__STDERR_FLOOD_CRASH__"), timeout_seconds=5
            )
        await wait_until_ready(self.pool, 2, replacements=1)
        result = await self.pool.compile(WorkerRequest("following", "code"))
        self.assertEqual(result.status, "ok")

    async def test_close_reaps_replacement_during_startup(self) -> None:
        backends = []

        def factory() -> WorkerProcessBackend:
            command = [sys.executable, str(FAKE_WORKER)]
            if backends:
                command += ["--startup-delay", "30"]
            backend = WorkerProcessBackend(command)
            backends.append(backend)
            return backend

        pool = CompilerPool(factory, worker_count=1, queue_capacity=1)
        await pool.start()
        try:
            with self.assertRaises(PoolWorkerError):
                await pool.compile(WorkerRequest("crash", "__CRASH__"))
            async with asyncio.timeout(2):
                while len(backends) < 2 or backends[1].pid is None:
                    await asyncio.sleep(0.001)
            # A request must also expire while all capacity is restarting.
            with self.assertRaises(PoolTimeoutError):
                await asyncio.wait_for(
                    pool.compile(WorkerRequest("queued", "code"), timeout_seconds=0.02),
                    timeout=1,
                )
            self.assertEqual(pool.snapshot.queue_depth, 0)
            await pool.close()
            self.assertEqual(pool.snapshot.state, "closed")
            self.assertIsNotNone(backends[1].returncode)
        finally:
            await pool.close()
            await asyncio.gather(*(backend.close() for backend in backends))

    async def test_queue_time_is_deducted_from_execution_budget(self) -> None:
        pool = CompilerPool(worker_factory, worker_count=1, queue_capacity=1)
        await pool.start()
        active = asyncio.create_task(pool.compile(WorkerRequest("active", "__SLEEP__:0.15")))
        try:
            async with asyncio.timeout(2):
                while pool.snapshot.active_workers == 0:
                    await asyncio.sleep(0.001)
            with self.assertRaises(PoolTimeoutError):
                await pool.compile(
                    WorkerRequest("limited", "__SLEEP__:0.15"), timeout_seconds=0.25
                )
            await active
            await wait_until_ready(pool, 1, replacements=1)
            self.assertEqual((await pool.compile(WorkerRequest("following", "code"))).status, "ok")
        finally:
            await pool.close()
            await asyncio.gather(active, return_exceptions=True)

    async def test_cleanup_exception_does_not_kill_worker_slot(self) -> None:
        class BrokenCleanupWorker(WorkerProcessBackend):
            async def close(self) -> None:
                await super().close()
                raise RuntimeError("injected cleanup failure")

        created = 0

        def factory() -> WorkerProcessBackend:
            nonlocal created
            created += 1
            cls = BrokenCleanupWorker if created == 1 else WorkerProcessBackend
            return cls([sys.executable, str(FAKE_WORKER)])

        pool = CompilerPool(factory, worker_count=1, queue_capacity=1)
        await pool.start()
        try:
            with self.assertLogs("lean_server.service.pool", level="ERROR"):
                with self.assertRaises(PoolWorkerError):
                    await pool.compile(WorkerRequest("crash", "__CRASH__"))
                await wait_until_ready(pool, 1, replacements=1)
            self.assertEqual((await pool.compile(WorkerRequest("following", "code"))).status, "ok")
        finally:
            await pool.close()


if __name__ == "__main__":
    unittest.main()
