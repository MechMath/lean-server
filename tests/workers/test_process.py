from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path

from lean_server.protocol import VerifyWorkerRequest, VerifyWorkerResult, WorkerRequest
from lean_server.workers import (
    WorkerProcessBackend,
    WorkerProtocolError,
    WorkerStartupError,
)
from lean_server.workers.process import MAX_STDERR_TAIL_BYTES


FAKE_WORKER = Path(__file__).parents[1] / "fixtures" / "fake_worker.py"


class WorkerProcessTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.worker = WorkerProcessBackend([sys.executable, str(FAKE_WORKER)])
        await self.worker.start()

    async def asyncTearDown(self) -> None:
        await self.worker.close()

    async def test_compiles_multiple_requests_in_one_process(self) -> None:
        pid = self.worker.pid
        first = await self.worker.compile(WorkerRequest("req-1", "def one := 1"))
        second = await self.worker.compile(WorkerRequest("req-2", "def two := 2"))

        self.assertEqual(first.status, "ok")
        self.assertEqual(second.status, "ok")
        self.assertEqual(self.worker.pid, pid)

    async def test_returns_compile_error_without_killing_worker(self) -> None:
        result = await self.worker.compile(WorkerRequest("req-error", "__ERROR__"))
        following = await self.worker.compile(WorkerRequest("req-ok", "def ok := 1"))

        self.assertEqual(result.status, "compile_error")
        self.assertEqual(result.errors[0].message, "fake compilation error")
        self.assertEqual(following.status, "ok")

    async def test_rejects_invalid_worker_output(self) -> None:
        with self.assertRaises(WorkerProtocolError):
            await self.worker.compile(WorkerRequest("req-invalid", "__INVALID_JSON__"))

    async def test_verifies_with_v2_after_v1_ready_handshake(self) -> None:
        result = await self.worker.compile(
            VerifyWorkerRequest(
                "verify-1",
                "theorem answer : True := by sorry",
                "theorem answer : True := by trivial",
            )
        )

        self.assertIsInstance(result, VerifyWorkerResult)
        assert isinstance(result, VerifyWorkerResult)
        self.assertEqual(result.status, "ok")
        self.assertEqual(result.formal_statement_ms, 1.0)

    async def test_verify_response_can_exceed_default_stream_reader_limit(self) -> None:
        result = await self.worker.compile(
            VerifyWorkerRequest(
                "verify-large",
                "theorem answer : True := by sorry",
                "__LARGE_RESPONSE__",
            )
        )

        self.assertIsInstance(result, VerifyWorkerResult)
        assert isinstance(result, VerifyWorkerResult)
        self.assertGreater(len(result.warnings[0].message), 64 * 1024)

    async def test_reports_ready_handshake_timeout(self) -> None:
        worker = WorkerProcessBackend(
            [sys.executable, str(FAKE_WORKER), "--startup-delay", "1"],
            startup_timeout_seconds=0.01,
        )
        with self.assertRaisesRegex(
            WorkerStartupError,
            r"worker ready handshake timed out after 0\.01 seconds",
        ):
            await worker.start()
        await worker.close()

    async def test_drains_oversized_stderr_and_bounds_retained_output(self) -> None:
        result = await asyncio.wait_for(
            self.worker.compile(WorkerRequest("flood", "__STDERR_FLOOD__")), timeout=5
        )
        self.assertEqual(result.status, "ok")
        async with asyncio.timeout(2):
            while not self.worker.stderr_tail or self.worker.stderr_tail[-1] != "tail marker":
                await asyncio.sleep(0.01)
        self.assertLessEqual(len("\n".join(self.worker.stderr_tail)), MAX_STDERR_TAIL_BYTES)
        following = await self.worker.compile(WorkerRequest("following", "code"))
        self.assertEqual(following.status, "ok")
        await self.worker.close()

    async def test_cancelling_ready_handshake_reaps_process(self) -> None:
        worker = WorkerProcessBackend(
            [sys.executable, str(FAKE_WORKER), "--startup-delay", "30"]
        )
        startup = asyncio.create_task(worker.start())
        try:
            async with asyncio.timeout(2):
                while worker.pid is None:
                    await asyncio.sleep(0.001)
            startup.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await startup
            self.assertIsNotNone(worker.returncode)
        finally:
            startup.cancel()
            await asyncio.gather(startup, return_exceptions=True)
            await worker.close()


if __name__ == "__main__":
    unittest.main()
