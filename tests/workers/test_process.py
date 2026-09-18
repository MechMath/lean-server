from __future__ import annotations

import sys
import unittest
from pathlib import Path

from lean_server.protocol import WorkerRequest
from lean_server.workers import WorkerProcessBackend, WorkerProtocolError


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


if __name__ == "__main__":
    unittest.main()
