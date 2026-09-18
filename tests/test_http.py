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

    def request(self, method: str, path: str, body: object | None = None) -> tuple[int, dict]:
        connection = HTTPConnection("127.0.0.1", self.port, timeout=5)
        encoded = None if body is None else json.dumps(body)
        headers = {} if body is None else {"Content-Type": "application/json"}
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

    def test_ready(self) -> None:
        for _ in range(200):
            if self.server.runtime.snapshot().ready_workers == 1:
                break
            time.sleep(0.005)
        status, body = self.request("GET", "/readyz")
        self.assertEqual(status, 200)
        self.assertEqual(body["status"], "ready")

    def test_rejects_missing_code(self) -> None:
        status, body = self.request("POST", "/api/v1/check", {})
        self.assertEqual(status, 400)
        self.assertIn("code", body["error"])

    def test_rejects_invalid_allow_sorry(self) -> None:
        status, body = self.request(
            "POST", "/api/v1/check", {"code": "code", "allow_sorry": "yes"}
        )
        self.assertEqual(status, 400)
        self.assertIn("allow_sorry", body["error"])

    def test_compiles_valid_code(self) -> None:
        status, body = self.request("POST", "/api/v1/check", {"code": "def answer : Nat := 42"})
        self.assertEqual(status, 200)
        self.assertTrue(body["okay"])
        self.assertGreater(body["time_ms"], 0)
        self.assertEqual(body["lean_version"], "4.30.0")
        self.assertEqual(body["errors"], [])

    def test_returns_lean_warning(self) -> None:
        status, body = self.request(
            "POST",
            "/api/v1/check",
            {"code": "__WARNING__"},
        )
        self.assertEqual(status, 200)
        self.assertTrue(body["okay"])
        self.assertEqual(body["warnings"][0]["message"], "fake warning")

    def test_returns_lean_error(self) -> None:
        status, body = self.request(
            "POST", "/api/v1/check", {"code": "__ERROR__"}
        )
        self.assertEqual(status, 200)
        self.assertFalse(body["okay"])
        self.assertEqual(body["errors"][0]["message"], "fake compilation error")

    def test_returns_timeout_result(self) -> None:
        status, body = self.request(
            "POST",
            "/api/v1/check",
            {"code": "__SLEEP__:1", "timeout_seconds": 0.05},
        )
        self.assertEqual(status, 200)
        self.assertFalse(body["okay"])
        self.assertTrue(body["timed_out"])

    def test_rejects_sorry_by_default(self) -> None:
        status, body = self.request("POST", "/api/v1/check", {"code": "__SORRY__"})
        self.assertEqual(status, 200)
        self.assertFalse(body["okay"])
        self.assertEqual(body["warnings"][0]["message"], "declaration uses `sorry`")
        self.assertIn("allow_sorry is false", body["errors"][0]["message"])

    def test_accepts_sorry_when_enabled(self) -> None:
        status, body = self.request(
            "POST",
            "/api/v1/check",
            {"code": "__SORRY__", "allow_sorry": True},
        )
        self.assertEqual(status, 200)
        self.assertTrue(body["okay"])
        self.assertEqual(body["errors"], [])
        self.assertEqual(body["warnings"][0]["message"], "declaration uses `sorry`")

    def test_maps_worker_crash_to_retryable_error(self) -> None:
        status, body = self.request("POST", "/api/v1/check", {"code": "__CRASH__"})
        self.assertEqual(status, 503)
        self.assertTrue(body["retryable"])

    def test_returns_overload_when_queue_is_full(self) -> None:
        with ThreadPoolExecutor(max_workers=2) as executor:
            active = executor.submit(
                self.request,
                "POST",
                "/api/v1/check",
                {"code": "__SLEEP__:0.2"},
            )
            self._wait_for_pool(active_workers=1, queue_depth=0)
            queued = executor.submit(
                self.request,
                "POST",
                "/api/v1/check",
                {"code": "code"},
            )
            self._wait_for_pool(active_workers=1, queue_depth=1)
            status, body = self.request("POST", "/api/v1/check", {"code": "rejected"})

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
