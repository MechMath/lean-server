from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path

from lean_server.protocol import VerifyWorkerRequest, WorkerJobRequest, WorkerRequest
from lean_server.service import CompilerPool, PoolTimeoutError, PoolWorkerError
from lean_server.workers import WorkerProcessBackend


FAKE_WORKER = Path(__file__).parents[1] / "fixtures" / "fake_worker.py"
PANIC = "__NAT_POW_PANIC__"


class TrackedWorker(WorkerProcessBackend):
    def __init__(self, tracker: Tracker, index: int) -> None:
        super().__init__([sys.executable, str(FAKE_WORKER)])
        self.tracker = tracker
        self.index = index

    async def start(self) -> None:
        if self.index >= self.tracker.worker_count:
            self.tracker.replacement_started.set()
            await self.tracker.replacement_gate.wait()
        await super().start()

    async def compile(self, request: WorkerJobRequest):
        self.tracker.executed.append(request.request_id)
        content = request.code if isinstance(request, WorkerRequest) else request.content
        if content == PANIC:
            self.tracker.panic_entered.set()
            await self.tracker.panic_gate.wait()
        return await super().compile(request)


class Tracker:
    def __init__(self, worker_count: int = 2) -> None:
        self.worker_count = worker_count
        self.backends: list[TrackedWorker] = []
        self.executed: list[str] = []
        self.panic_entered = asyncio.Event()
        self.panic_gate = asyncio.Event()
        self.panic_gate.set()
        self.replacement_started = asyncio.Event()
        self.replacement_gate = asyncio.Event()
        self.replacement_gate.set()

    def factory(self) -> TrackedWorker:
        backend = TrackedWorker(self, len(self.backends))
        self.backends.append(backend)
        return backend


class QuarantineTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.tracker = Tracker()
        self.pool = CompilerPool(
            self.tracker.factory, worker_count=2, queue_capacity=8,
            default_timeout_seconds=5, quarantine_capacity=2,
        )
        await self.pool.start()

    async def asyncTearDown(self) -> None:
        await self.pool.close()

    async def assert_panic(self, request: WorkerJobRequest) -> None:
        with self.assertRaises(PoolWorkerError) as caught:
            await asyncio.wait_for(self.pool.compile(request), timeout=2)
        self.assertEqual(caught.exception.error_type, "LeanPanic")
        self.assertFalse(caught.exception.retryable)

    async def wait_for_replacements(self, count: int) -> None:
        async with asyncio.timeout(3):
            while self.pool.snapshot.replacements < count:
                await asyncio.sleep(0.005)

    async def test_repeated_panic_is_rejected_without_executing_or_replacing(self) -> None:
        await self.assert_panic(WorkerRequest("first", PANIC))
        await self.wait_for_replacements(1)
        for index in range(5):
            await self.assert_panic(WorkerRequest(f"retry-{index}", PANIC))
        result = await self.pool.compile(WorkerRequest("healthy", "code"))
        self.assertEqual(result.status, "ok")
        self.assertEqual(self.tracker.executed, ["first", "healthy"])
        self.assertEqual(self.pool.snapshot.replacements, 1)
        self.assertEqual(self.pool.snapshot.quarantined_inputs, 1)
        self.assertEqual(self.pool.snapshot.quarantine_hits, 5)

    async def test_concurrent_duplicates_leave_other_worker_available(self) -> None:
        self.tracker.panic_gate.clear()
        first = asyncio.create_task(self.pool.compile(WorkerRequest("first", PANIC)))
        await asyncio.wait_for(self.tracker.panic_entered.wait(), timeout=2)
        duplicates = [
            asyncio.create_task(self.pool.compile(WorkerRequest(f"duplicate-{i}", PANIC)))
            for i in range(3)
        ]
        try:
            # The healthy job must bypass the blocked duplicates in the queue.
            result = await asyncio.wait_for(
                self.pool.compile(WorkerRequest("healthy", "code")), timeout=2,
            )
            self.assertEqual(result.status, "ok")
            self.assertEqual(self.tracker.executed, ["first", "healthy"])
            self.assertEqual(self.pool.snapshot.queue_depth, 3)
            self.tracker.panic_gate.set()
            outcomes = await asyncio.wait_for(
                asyncio.gather(first, *duplicates, return_exceptions=True), timeout=2,
            )
            for outcome in outcomes:
                self.assertIsInstance(outcome, PoolWorkerError)
                self.assertEqual(outcome.error_type, "LeanPanic")
                self.assertFalse(outcome.retryable)
            await self.wait_for_replacements(1)
            self.assertEqual(self.pool.snapshot.replacements, 1)
            self.assertEqual(self.pool.snapshot.queue_depth, 0)
            self.assertEqual(self.pool.snapshot.quarantine_hits, 3)
        finally:
            self.tracker.panic_gate.set()
            await asyncio.gather(first, *duplicates, return_exceptions=True)

    async def test_healthy_requests_work_while_replacement_is_blocked(self) -> None:
        self.tracker.replacement_gate.clear()
        await self.assert_panic(WorkerRequest("panic", PANIC))
        await asyncio.wait_for(self.tracker.replacement_started.wait(), timeout=2)
        self.assertEqual(self.pool.snapshot.ready_workers, 1)
        await self.assert_panic(WorkerRequest("retry", PANIC))
        result = await asyncio.wait_for(
            self.pool.compile(WorkerRequest("healthy", "code")), timeout=2,
        )
        self.assertEqual(result.status, "ok")
        self.assertEqual(self.pool.snapshot.replacements, 0)
        self.tracker.replacement_gate.set()
        await self.wait_for_replacements(1)

    async def test_fingerprint_includes_operation_statement_and_options(self) -> None:
        requests = [
            WorkerRequest("compile", PANIC),
            VerifyWorkerRequest("verify", "statement A", PANIC),
            VerifyWorkerRequest("statement", "statement B", PANIC),
            VerifyWorkerRequest("option", "statement B", PANIC, use_def_eq=False),
        ]
        for index, request in enumerate(requests, start=1):
            await self.assert_panic(request)
            await self.wait_for_replacements(index)
        await self.assert_panic(
            VerifyWorkerRequest("same-input-new-id", "statement B", PANIC, use_def_eq=False)
        )
        self.assertEqual(self.tracker.executed, [request.request_id for request in requests])
        self.assertEqual(self.pool.snapshot.replacements, 4)

    async def test_quarantine_is_bounded_and_recently_used_inputs_are_retained(self) -> None:
        for label in ("A", "B", "A", "C", "A"):
            await self.assert_panic(VerifyWorkerRequest(label, label, PANIC))
        await self.wait_for_replacements(3)
        self.assertEqual(self.tracker.executed, ["A", "B", "C"])
        self.assertEqual(self.pool.snapshot.quarantined_inputs, 2)
        self.assertEqual(self.pool.snapshot.quarantine_evictions, 1)
        # Eviction is deliberate: B is eligible for execution again.
        await self.assert_panic(VerifyWorkerRequest("B-again", "B", PANIC))
        await self.wait_for_replacements(4)
        self.assertEqual(self.tracker.executed[-1], "B-again")

    async def test_cancelled_caller_still_quarantines_a_completed_panic(self) -> None:
        self.tracker.panic_gate.clear()
        first = asyncio.create_task(self.pool.compile(WorkerRequest("cancelled", PANIC)))
        await asyncio.wait_for(self.tracker.panic_entered.wait(), timeout=2)
        first.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await first
        self.tracker.panic_gate.set()
        await self.wait_for_replacements(1)
        await self.assert_panic(WorkerRequest("retry", PANIC))
        self.assertEqual(self.tracker.executed, ["cancelled"])

    async def test_waiting_duplicate_can_expire_or_cancel_without_executing(self) -> None:
        self.tracker.panic_gate.clear()
        first = asyncio.create_task(self.pool.compile(WorkerRequest("first", PANIC)))
        await asyncio.wait_for(self.tracker.panic_entered.wait(), timeout=2)
        try:
            with self.assertRaises(PoolTimeoutError) as caught:
                await self.pool.compile(WorkerRequest("expired", PANIC), timeout_seconds=0.02)
            self.assertEqual(caught.exception.compile_ms, 0)
            cancelled = asyncio.create_task(self.pool.compile(WorkerRequest("cancelled", PANIC)))
            await asyncio.sleep(0)
            cancelled.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await cancelled
            self.assertEqual(self.pool.snapshot.queue_depth, 0)
            self.tracker.panic_gate.set()
            with self.assertRaises(PoolWorkerError):
                await first
            self.assertEqual(self.tracker.executed, ["first"])
        finally:
            self.tracker.panic_gate.set()
            await asyncio.gather(first, return_exceptions=True)

    async def test_normal_results_and_transient_failures_are_not_quarantined(self) -> None:
        for code in ("code", "__ERROR__", "__CRASH__", "__INVALID_JSON__", "__SLEEP__:1"):
            for attempt in range(2):
                request = WorkerRequest(f"{code}-{attempt}", code)
                try:
                    result = await self.pool.compile(
                        request, timeout_seconds=0.05 if code == "__SLEEP__:1" else 5,
                    )
                except PoolWorkerError as exc:
                    self.assertTrue(exc.retryable)
                except PoolTimeoutError:
                    self.assertEqual(code, "__SLEEP__:1")
                else:
                    self.assertEqual(result.request_id, request.request_id)
                    self.assertEqual(result.status, "compile_error" if code == "__ERROR__" else "ok")
            self.assertEqual(self.pool.snapshot.quarantined_inputs, 0)
        self.assertEqual(len(self.tracker.executed), 10)

    async def test_successful_duplicates_keep_their_own_request_ids(self) -> None:
        results = await asyncio.gather(*(
            self.pool.compile(WorkerRequest(str(i), "code")) for i in range(5)
        ))
        self.assertEqual([result.request_id for result in results], [str(i) for i in range(5)])
        self.assertEqual(len(self.tracker.executed), 5)
        self.assertEqual(self.pool.snapshot.replacements, 0)

    async def test_quarantine_is_checked_before_full_queue_during_restart(self) -> None:
        tracker = Tracker(worker_count=1)
        pool = CompilerPool(tracker.factory, worker_count=1, queue_capacity=1)
        await pool.start()
        queued = None
        try:
            tracker.replacement_gate.clear()
            with self.assertRaises(PoolWorkerError):
                await pool.compile(WorkerRequest("first", PANIC))
            await asyncio.wait_for(tracker.replacement_started.wait(), timeout=2)
            queued = asyncio.create_task(pool.compile(WorkerRequest("healthy", "code")))
            await asyncio.sleep(0)
            self.assertEqual(pool.snapshot.queue_depth, 1)
            with self.assertRaises(PoolWorkerError) as caught:
                await asyncio.wait_for(pool.compile(WorkerRequest("retry", PANIC)), timeout=1)
            self.assertFalse(caught.exception.retryable)
            tracker.replacement_gate.set()
            self.assertEqual((await asyncio.wait_for(queued, timeout=2)).status, "ok")
            self.assertEqual(tracker.executed, ["first", "healthy"])
        finally:
            await pool.close()
            if queued is not None:
                await asyncio.gather(queued, return_exceptions=True)


if __name__ == "__main__":
    unittest.main()
