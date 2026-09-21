from __future__ import annotations

import json
import unittest
from pathlib import Path

from lean_server.protocol import (
    PROTOCOL_VERSION,
    VERIFY_PROTOCOL_VERSION,
    ProtocolError,
    VerifyWorkerRequest,
    VerifyWorkerResult,
    WorkerReady,
    WorkerRequest,
    WorkerResult,
    decode_worker_message,
)


EXAMPLES = Path(__file__).parents[2] / "protocol" / "v1" / "examples"


class WorkerProtocolTests(unittest.TestCase):
    def test_request_matches_frozen_example(self) -> None:
        expected = json.loads((EXAMPLES / "request.json").read_text())
        request = WorkerRequest(request_id="req-1", code="def answer : Nat := 42")
        self.assertEqual(request.to_dict(), expected)
        self.assertTrue(request.to_json_line().endswith("\n"))

    def test_decodes_ready(self) -> None:
        message = decode_worker_message((EXAMPLES / "ready.json").read_text())
        self.assertEqual(message, WorkerReady(lean_version="4.30.0"))

    def test_decodes_success(self) -> None:
        message = decode_worker_message((EXAMPLES / "success.json").read_text())
        self.assertIsInstance(message, WorkerResult)
        assert isinstance(message, WorkerResult)
        self.assertEqual(message.request_id, "req-1")
        self.assertEqual(message.status, "ok")
        self.assertEqual(message.errors, ())

    def test_decodes_compile_error_and_position(self) -> None:
        message = decode_worker_message((EXAMPLES / "compile-error.json").read_text())
        assert isinstance(message, WorkerResult)
        self.assertEqual(message.status, "compile_error")
        self.assertEqual(message.errors[0].start.line, 1)  # type: ignore[union-attr]
        self.assertEqual(message.errors[0].start.column, 20)  # type: ignore[union-attr]

    def test_rejects_wrong_version(self) -> None:
        line = json.dumps({"protocol_version": PROTOCOL_VERSION + 1, "type": "ready"})
        with self.assertRaisesRegex(ProtocolError, "protocol_version"):
            decode_worker_message(line)

    def test_rejects_non_json_stdout(self) -> None:
        with self.assertRaisesRegex(ProtocolError, "valid JSON"):
            decode_worker_message("worker debug log")

    def test_verify_request_uses_protocol_v2_without_changing_compile_v1(self) -> None:
        request = VerifyWorkerRequest(
            request_id="verify-1",
            formal_statement="theorem answer : True := by sorry",
            content="theorem answer : True := by trivial",
            use_def_eq=False,
        )
        self.assertEqual(
            request.to_dict(),
            {
                "protocol_version": VERIFY_PROTOCOL_VERSION,
                "type": "verify",
                "request_id": "verify-1",
                "formal_statement": "theorem answer : True := by sorry",
                "content": "theorem answer : True := by trivial",
                "use_def_eq": False,
            },
        )
        self.assertEqual(
            WorkerRequest(request_id="compile-1", code="def n := 1").to_dict()[
                "protocol_version"
            ],
            PROTOCOL_VERSION,
        )

    def test_decodes_verify_result_v2(self) -> None:
        line = json.dumps(
            {
                "protocol_version": VERIFY_PROTOCOL_VERSION,
                "type": "verify_result",
                "request_id": "verify-1",
                "status": "ok",
                "compile_ms": 4,
                "formal_statement_ms": 1,
                "candidate_ms": 2,
                "declarations_ms": 1,
                "warnings": [],
                "errors": [],
                "tool_errors": ["Missing required declaration 'answer'"],
                "failed_declarations": ["answer"],
            }
        )
        message = decode_worker_message(line)
        self.assertIsInstance(message, VerifyWorkerResult)
        assert isinstance(message, VerifyWorkerResult)
        self.assertEqual(message.tool_errors, ("Missing required declaration 'answer'",))
        self.assertEqual(message.failed_declarations, ("answer",))

    def test_rejects_wrong_version_and_type_pair(self) -> None:
        line = json.dumps(
            {
                "protocol_version": VERIFY_PROTOCOL_VERSION,
                "type": "result",
                "request_id": "verify-1",
            }
        )
        with self.assertRaisesRegex(ProtocolError, "protocol_version/message type"):
            decode_worker_message(line)

    def test_rejects_invalid_verify_result_arrays(self) -> None:
        line = json.dumps(
            {
                "protocol_version": VERIFY_PROTOCOL_VERSION,
                "type": "verify_result",
                "request_id": "verify-1",
                "status": "ok",
                "compile_ms": 4,
                "formal_statement_ms": 1,
                "candidate_ms": 2,
                "declarations_ms": 1,
                "warnings": [],
                "errors": [],
                "tool_errors": [1],
                "failed_declarations": [],
            }
        )
        with self.assertRaisesRegex(ProtocolError, "tool_errors"):
            decode_worker_message(line)


if __name__ == "__main__":
    unittest.main()
