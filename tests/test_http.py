from __future__ import annotations

import json
import sys
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from http.client import HTTPConnection
from pathlib import Path

from lean_server.http import create_runtime, create_server


FAKE_WORKER = Path(__file__).parent / "fixtures" / "fake_worker.py"


class HTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        runtime = create_runtime(
            worker_count=1,
            queue_capacity=1,
            worker_command=[sys.executable, str(FAKE_WORKER)],
        )
        cls.server = create_server("127.0.0.1", 0, runtime=runtime)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.port = cls.server.server_address[1]

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def request(
        self,
        method: str,
        path: str,
        body: object | None = None,
        *,
        content_type: str = "application/json",
    ) -> tuple[int, dict]:
        connection = HTTPConnection("127.0.0.1", self.port, timeout=5)
        encoded = None if body is None else json.dumps(body)
        headers = {} if body is None else {"Content-Type": content_type}
        connection.request(method, path, body=encoded, headers=headers)
        response = connection.getresponse()
        value = json.loads(response.read())
        connection.close()
        return response.status, value

    def test_health(self) -> None:
        status, body = self.request("GET", "/healthz")
        self.assertEqual(status, 200)
        self.assertEqual(body["status"], "ok")
        self.assertEqual(body["lean_version"], "4.30.0")
        self.assertEqual(body["pool"]["ready_workers"], 1)

    def test_known_panic_is_explicit_and_not_a_proof_rejection(self) -> None:
        cases = (
            ("/check", {"code": "__NAT_POW_PANIC__"}),
            ("/verify_proof", {
                "formal_statement": "theorem answer : True := by sorry",
                "content": "__NAT_POW_PANIC__",
                "environment": "lean-4.30.0",
            }),
        )
        for path, payload in cases:
            with self.subTest(path=path):
                replacements = self.server.runtime.snapshot().replacements
                for attempt in range(3):
                    status, body = self.request("POST", path, payload)
                    self.assertEqual(status, 503)
                    self.assertEqual(body["error_type"], "LeanPanic")
                    self.assertFalse(body["retryable"])
                    self.assertNotIn("okay", body)
                    if attempt:
                        self.assertIn("quarantined", body["error"])
                status, body = self.request("POST", "/check", {"code": "healthy after panic"})
                self.assertEqual(status, 200)
                self.assertTrue(body["okay"])
                self.assertEqual(self.server.runtime.snapshot().replacements, replacements + 1)

    def test_ready(self) -> None:
        for _ in range(200):
            if self.server.runtime.snapshot().ready_workers == 1:
                break
            time.sleep(0.005)
        status, body = self.request("GET", "/readyz")
        self.assertEqual(status, 200)
        self.assertEqual(body["status"], "ready")

    def test_rejects_missing_code(self) -> None:
        status, body = self.request("POST", "/check", {})
        self.assertEqual(status, 400)
        self.assertIn("code", body["error"])

    def test_rejects_invalid_allow_sorry(self) -> None:
        status, body = self.request(
            "POST", "/check", {"code": "code", "allow_sorry": "yes"}
        )
        self.assertEqual(status, 400)
        self.assertIn("allow_sorry", body["error"])

    def test_compiles_valid_code(self) -> None:
        status, body = self.request("POST", "/check", {"code": "def answer : Nat := 42"})
        self.assertEqual(status, 200)
        self.assertTrue(body["okay"])
        self.assertGreater(body["time_ms"], 0)
        self.assertEqual(body["lean_version"], "4.30.0")
        self.assertEqual(body["errors"], [])

    def test_returns_lean_warning(self) -> None:
        status, body = self.request(
            "POST",
            "/check",
            {"code": "__WARNING__"},
        )
        self.assertEqual(status, 200)
        self.assertTrue(body["okay"])
        self.assertEqual(body["warnings"][0]["message"], "fake warning")

    def test_returns_lean_error(self) -> None:
        status, body = self.request(
            "POST", "/check", {"code": "__ERROR__"}
        )
        self.assertEqual(status, 200)
        self.assertFalse(body["okay"])
        self.assertEqual(body["errors"][0]["message"], "fake compilation error")

    def test_returns_timeout_result(self) -> None:
        status, body = self.request(
            "POST",
            "/check",
            {"code": "__SLEEP__:1", "timeout_seconds": 0.05},
        )
        self.assertEqual(status, 200)
        self.assertFalse(body["okay"])
        self.assertTrue(body["timed_out"])

    def test_queue_timeout_does_not_count_as_compilation(self) -> None:
        with ThreadPoolExecutor(max_workers=1) as executor:
            active = executor.submit(
                self.request, "POST", "/check", {"code": "__SLEEP__:0.2"}
            )
            deadline = time.monotonic() + 2
            while self.server.runtime.snapshot().active_workers == 0:
                self.assertLess(time.monotonic(), deadline)
                time.sleep(0.001)
            status, body = self.request(
                "POST", "/check", {"code": "code", "timeout_seconds": 0.02}
            )
            self.assertEqual(status, 200)
            self.assertTrue(body["timed_out"])
            self.assertGreater(body["timings"]["queue_ms"], 0)
            self.assertEqual(body["timings"]["compile_ms"], 0)
            self.assertEqual(self.server.runtime.snapshot().queue_depth, 0)
            self.assertTrue(active.result()[1]["okay"])

    def test_rejects_sorry_by_default(self) -> None:
        status, body = self.request("POST", "/check", {"code": "__SORRY__"})
        self.assertEqual(status, 200)
        self.assertFalse(body["okay"])
        self.assertEqual(body["warnings"][0]["message"], "declaration uses `sorry`")
        self.assertIn("allow_sorry is false", body["errors"][0]["message"])

    def test_accepts_sorry_when_enabled(self) -> None:
        status, body = self.request(
            "POST",
            "/check",
            {"code": "__SORRY__", "allow_sorry": True},
        )
        self.assertEqual(status, 200)
        self.assertTrue(body["okay"])
        self.assertEqual(body["errors"], [])
        self.assertEqual(body["warnings"][0]["message"], "declaration uses `sorry`")

    def test_verify_proof_returns_axle_compatible_success(self) -> None:
        candidate = "theorem answer : True := by trivial"
        status, body = self.request(
            "POST",
            "/verify_proof",
            {
                "formal_statement": "theorem answer : True := by sorry",
                "content": candidate,
                "environment": "lean-4.30.0",
            },
        )

        self.assertEqual(status, 200)
        self.assertTrue(body["okay"])
        self.assertEqual(body["content"], candidate)
        self.assertEqual(body["lean_messages"], {"errors": [], "warnings": [], "infos": []})
        self.assertEqual(body["tool_messages"], {"errors": [], "warnings": [], "infos": []})
        self.assertEqual(body["failed_declarations"], [])
        self.assertEqual(body["timings"]["formal_statement_ms"], 1.0)
        self.assertEqual(body["timings"]["candidate_ms"], 2.0)
        self.assertEqual(body["timings"]["declarations_ms"], 1.0)

    def test_verify_proof_accepts_axle_sdk_text_plain_json(self) -> None:
        status, body = self.request(
            "POST",
            "/verify_proof",
            {
                "formal_statement": "theorem answer : True := by sorry",
                "content": "theorem answer : True := by trivial",
                "environment": "lean-4.30.0",
            },
            content_type="text/plain; charset=utf-8",
        )

        self.assertEqual(status, 200)
        self.assertTrue(body["okay"])

    def test_check_still_rejects_text_plain(self) -> None:
        status, body = self.request(
            "POST",
            "/check",
            {"code": "def answer := 42"},
            content_type="text/plain",
        )

        self.assertEqual(status, 415)
        self.assertIn("application/json", body["error"])

    def test_verify_proof_returns_semantic_failure_as_http_200(self) -> None:
        status, body = self.request(
            "POST",
            "/verify_proof",
            {
                "formal_statement": "theorem answer : True := by sorry",
                "content": "__TOOL_ERROR__",
                "environment": "lean-4.30.0",
            },
        )

        self.assertEqual(status, 200)
        self.assertFalse(body["okay"])
        self.assertIn("Axiom", body["tool_messages"]["errors"][0])
        self.assertEqual(body["failed_declarations"], ["answer"])

    def test_verify_proof_returns_lean_error_as_http_200(self) -> None:
        status, body = self.request(
            "POST",
            "/verify_proof",
            {
                "formal_statement": "theorem answer : True := by sorry",
                "content": "__ERROR__",
                "environment": "lean-4.30.0",
            },
        )

        self.assertEqual(status, 200)
        self.assertFalse(body["okay"])
        self.assertEqual(body["lean_messages"]["errors"], ["fake candidate error"])

    def test_verify_proof_timeout_uses_axle_error_envelope(self) -> None:
        status, body = self.request(
            "POST",
            "/verify_proof",
            {
                "formal_statement": "theorem answer : True := by sorry",
                "content": "__SLEEP__:1",
                "environment": "lean-4.30.0",
                "timeout_seconds": 0.05,
            },
        )

        self.assertEqual(status, 200)
        self.assertEqual(body["error_type"], "LeanTimeout")
        self.assertIn("exceeded", body["error"])

    def test_verify_proof_allows_explicit_long_budget(self) -> None:
        payload = {
            "formal_statement": "theorem answer : True := by sorry",
            "content": "theorem answer : True := by trivial",
            "environment": "lean-4.30.0",
            "timeout_seconds": 600,
        }
        status, body = self.request("POST", "/verify_proof", payload)
        self.assertEqual(status, 200)
        self.assertTrue(body["okay"])
        for invalid in (601, 0, -1, True, float("inf"), float("nan")):
            with self.subTest(timeout=invalid):
                status, body = self.request(
                    "POST", "/verify_proof", {**payload, "timeout_seconds": invalid}
                )
                self.assertEqual(status, 400)
                self.assertIn("timeout_seconds", body["error"])
        status, body = self.request(
            "POST", "/check", {"code": "def answer := 42", "timeout_seconds": 121}
        )
        self.assertEqual(status, 400)
        self.assertIn("timeout_seconds", body["error"])

    def test_verify_proof_maps_worker_failure_to_retryable_503(self) -> None:
        status, body = self.request(
            "POST",
            "/verify_proof",
            {
                "formal_statement": "theorem answer : True := by sorry",
                "content": "__INTERNAL_ERROR__",
                "environment": "lean-4.30.0",
            },
        )

        self.assertEqual(status, 503)
        self.assertTrue(body["retryable"])

    def test_verify_proof_rejects_unsupported_options(self) -> None:
        base = {
            "formal_statement": "theorem answer : True := by sorry",
            "content": "theorem answer : True := by trivial",
            "environment": "lean-4.30.0",
        }
        cases = (
            ({"environment": "lean-4.29.0"}, "environment"),
            ({"permitted_sorries": ["helper"]}, "permitted_sorries"),
            ({"mathlib_options": True}, "mathlib_options"),
            ({"global_options": {"maxHeartbeats": 1}}, "global_options"),
            ({"verify_negation": True}, "verify_negation"),
            ({"ignore_imports": False}, "ignore_imports"),
            ({"unknown": True}, "unknown"),
        )
        for update, expected in cases:
            with self.subTest(update=update):
                status, body = self.request(
                    "POST", "/verify_proof", {**base, **update}
                )
                self.assertEqual(status, 400)
                self.assertIn(expected, body["error"])

    def test_verify_proof_rejects_non_boolean_use_def_eq(self) -> None:
        status, body = self.request(
            "POST",
            "/verify_proof",
            {
                "formal_statement": "theorem answer : True := by sorry",
                "content": "theorem answer : True := by trivial",
                "environment": "lean-4.30.0",
                "use_def_eq": 1,
            },
        )

        self.assertEqual(status, 400)
        self.assertIn("use_def_eq", body["error"])

    def test_maps_worker_crash_to_retryable_error(self) -> None:
        status, body = self.request("POST", "/check", {"code": "__CRASH__"})
        self.assertEqual(status, 503)
        self.assertTrue(body["retryable"])

    def test_returns_overload_when_queue_is_full(self) -> None:
        with ThreadPoolExecutor(max_workers=2) as executor:
            active = executor.submit(
                self.request,
                "POST",
                "/check",
                {"code": "__SLEEP__:0.2"},
            )
            self._wait_for_pool(active_workers=1, queue_depth=0)
            queued = executor.submit(
                self.request,
                "POST",
                "/check",
                {"code": "code"},
            )
            self._wait_for_pool(active_workers=1, queue_depth=1)
            status, body = self.request("POST", "/check", {"code": "rejected"})

            self.assertEqual(status, 503)
            self.assertTrue(body["retryable"])
            self.assertEqual(active.result()[0], 200)
            self.assertEqual(queued.result()[0], 200)

    def _wait_for_pool(self, *, active_workers: int, queue_depth: int) -> None:
        for _ in range(200):
            snapshot = self.server.runtime.snapshot()
            if (
                snapshot.active_workers == active_workers
                and snapshot.queue_depth == queue_depth
            ):
                return
            time.sleep(0.005)
        self.fail(f"pool state not reached: {self.server.runtime.snapshot()}")


if __name__ == "__main__":
    unittest.main()
