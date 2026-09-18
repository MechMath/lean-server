from __future__ import annotations

import unittest

from lean_server.protocol import WorkerRequest
from lean_server.workers import LeanCliBackend


class LeanCliBackendTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.backend = LeanCliBackend()
        await self.backend.start()

    async def asyncTearDown(self) -> None:
        await self.backend.close()

    async def test_returns_success_error_and_warning(self) -> None:
        success = await self.backend.compile(WorkerRequest("success", "def answer : Nat := 42"))
        error = await self.backend.compile(WorkerRequest("error", "def answer : Nat := true"))
        warning = await self.backend.compile(
            WorkerRequest("warning", "def constant (unused : Nat) : Nat := 42")
        )

        self.assertEqual(success.status, "ok")
        self.assertEqual(error.status, "compile_error")
        self.assertIn("Type mismatch", error.errors[0].message)
        self.assertEqual(warning.status, "ok")
        self.assertIn("unused variable", warning.warnings[0].message)

    async def test_reports_sorry_as_a_warning(self) -> None:
        result = await self.backend.compile(
            WorkerRequest("sorry", "theorem unfinished : True := by sorry")
        )

        self.assertEqual(result.status, "ok")
        self.assertEqual(result.warnings[0].message, "declaration uses `sorry`")


if __name__ == "__main__":
    unittest.main()
