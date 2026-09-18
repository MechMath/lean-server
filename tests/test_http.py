from __future__ import annotations

import json
import threading
import unittest
from http.client import HTTPConnection

from lean_server.http import create_server


class HTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.server = create_server("127.0.0.1", 0)
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
        self.assertEqual(body, {"status": "ok", "lean_version": "4.30.0"})

    def test_rejects_missing_code(self) -> None:
        status, body = self.request("POST", "/api/v1/check", {})
        self.assertEqual(status, 400)
        self.assertIn("code", body["error"])

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
            {"code": "def constant (unused : Nat) : Nat := 42"},
        )
        self.assertEqual(status, 200)
        self.assertTrue(body["okay"])
        self.assertIn("unused variable", body["warnings"][0]["message"])

    def test_returns_lean_error(self) -> None:
        status, body = self.request(
            "POST", "/api/v1/check", {"code": "def answer : Nat := true"}
        )
        self.assertEqual(status, 200)
        self.assertFalse(body["okay"])
        self.assertIn("Type mismatch", body["errors"][0]["message"])


if __name__ == "__main__":
    unittest.main()
