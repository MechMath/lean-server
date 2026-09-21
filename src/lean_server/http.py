from __future__ import annotations

import json
import math
import time
import uuid
from dataclasses import asdict, dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Sequence, cast

from .compiler import DEFAULT_TIMEOUT_SECONDS, MAX_TIMEOUT_SECONDS, PROJECT_ROOT
from .protocol import VerifyWorkerRequest, VerifyWorkerResult, WorkerRequest, WorkerResult
from .service import (
    CompilerPool,
    CompilerPoolRuntime,
    PoolClosedError,
    PoolOverloadedError,
    PoolTimeoutError,
    PoolWorkerError,
)
from .workers import WorkerProcessBackend


MAX_REQUEST_BYTES = 2 * 1024 * 1024
SORRY_WARNING_MESSAGE = "declaration uses `sorry`"
VERIFY_DEFAULT_TIMEOUT_SECONDS = 600.0
VERIFY_MAX_TIMEOUT_SECONDS = 600.0
SUPPORTED_ENVIRONMENT = "lean-4.30.0"
DEFAULT_WORKER_COMMAND = (
    "lake",
    "env",
    str(PROJECT_ROOT / ".lake" / "build" / "bin" / "lean-server-worker"),
)
VERIFY_REQUEST_FIELDS = frozenset(
    {
        "formal_statement",
        "content",
        "environment",
        "permitted_sorries",
        "mathlib_options",
        "global_options",
        "use_def_eq",
        "verify_negation",
        "ignore_imports",
        "timeout_seconds",
    }
)


class RequestValidationError(ValueError):
    """The HTTP request does not belong to the supported AXLE subset."""


def _worker_error_body(exc: PoolWorkerError | PoolClosedError) -> dict[str, Any]:
    body: dict[str, Any] = {"error": str(exc), "retryable": True}
    if isinstance(exc, PoolWorkerError):
        body["retryable"] = exc.retryable
        if exc.error_type is not None:
            body["error_type"] = exc.error_type
    return body


@dataclass(frozen=True, slots=True)
class VerifyProofRequest:
    formal_statement: str
    content: str
    use_def_eq: bool
    timeout_seconds: float

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> VerifyProofRequest:
        unknown = sorted(set(value) - VERIFY_REQUEST_FIELDS)
        if unknown:
            raise RequestValidationError(
                "unsupported request field(s): " + ", ".join(unknown)
            )

        formal_statement = value.get("formal_statement")
        if not isinstance(formal_statement, str):
            raise RequestValidationError("formal_statement must be a string")
        content = value.get("content")
        if not isinstance(content, str):
            raise RequestValidationError("content must be a string")

        environment = value.get("environment")
        if not isinstance(environment, str):
            raise RequestValidationError("environment must be a string")
        if environment != SUPPORTED_ENVIRONMENT:
            raise RequestValidationError(
                f"unsupported environment {environment!r}; expected {SUPPORTED_ENVIRONMENT!r}"
            )

        permitted_sorries = value.get("permitted_sorries", [])
        if not isinstance(permitted_sorries, list) or not all(
            isinstance(item, str) for item in permitted_sorries
        ):
            raise RequestValidationError("permitted_sorries must be an array of strings")
        if permitted_sorries:
            raise RequestValidationError("non-empty permitted_sorries is not supported")

        mathlib_options = value.get("mathlib_options", False)
        if not isinstance(mathlib_options, bool):
            raise RequestValidationError("mathlib_options must be a boolean")
        if mathlib_options:
            raise RequestValidationError("mathlib_options=true is not supported")

        global_options = value.get("global_options", {})
        if not isinstance(global_options, dict):
            raise RequestValidationError("global_options must be an object")
        if global_options:
            raise RequestValidationError("non-empty global_options is not supported")

        use_def_eq = value.get("use_def_eq", True)
        if not isinstance(use_def_eq, bool):
            raise RequestValidationError("use_def_eq must be a boolean")

        verify_negation = value.get("verify_negation", False)
        if not isinstance(verify_negation, bool):
            raise RequestValidationError("verify_negation must be a boolean")
        if verify_negation:
            raise RequestValidationError("verify_negation=true is not supported")

        ignore_imports = value.get("ignore_imports", True)
        if not isinstance(ignore_imports, bool):
            raise RequestValidationError("ignore_imports must be a boolean")
        if not ignore_imports:
            raise RequestValidationError("ignore_imports=false is not supported")

        timeout = value.get("timeout_seconds", VERIFY_DEFAULT_TIMEOUT_SECONDS)
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
            raise RequestValidationError("timeout_seconds must be a number")
        timeout_seconds = float(timeout)
        if not math.isfinite(timeout_seconds) or not 0 < timeout_seconds <= VERIFY_MAX_TIMEOUT_SECONDS:
            raise RequestValidationError(
                f"timeout_seconds must be between 0 and {VERIFY_MAX_TIMEOUT_SECONDS:g}"
            )

        return cls(
            formal_statement=formal_statement,
            content=content,
            use_def_eq=use_def_eq,
            timeout_seconds=timeout_seconds,
        )


class LeanHTTPServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(
        self,
        server_address: tuple[str, int],
        runtime: CompilerPoolRuntime,
    ) -> None:
        self.runtime = runtime
        super().__init__(server_address, LeanRequestHandler)

    def server_close(self) -> None:
        self.runtime.close()
        super().server_close()


class LeanRequestHandler(BaseHTTPRequestHandler):
    server_version = "lean-server/0.2"

    @property
    def runtime(self) -> CompilerPoolRuntime:
        return cast(LeanHTTPServer, self.server).runtime

    def _json_response(self, status: HTTPStatus, body: dict[str, Any]) -> None:
        encoded = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def _request_json(self, *, allow_text_plain: bool = False) -> dict[str, Any] | None:
        accepted_content_types = {"application/json"}
        if allow_text_plain:
            # axiom-axle sends json.dumps(request) through aiohttp's `data=`, which
            # uses text/plain rather than application/json for a Python string.
            accepted_content_types.add("text/plain")
        if self.headers.get_content_type() not in accepted_content_types:
            expected = "application/json or text/plain" if allow_text_plain else "application/json"
            self._json_response(
                HTTPStatus.UNSUPPORTED_MEDIA_TYPE, {"error": f"expected {expected}"}
            )
            return None
        try:
            size = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._json_response(HTTPStatus.BAD_REQUEST, {"error": "invalid Content-Length"})
            return None
        if size <= 0:
            self._json_response(HTTPStatus.BAD_REQUEST, {"error": "request body is empty"})
            return None
        if size > MAX_REQUEST_BYTES:
            self._json_response(
                HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"error": "request body is too large"}
            )
            return None
        try:
            value = json.loads(self.rfile.read(size))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._json_response(HTTPStatus.BAD_REQUEST, {"error": "invalid JSON body"})
            return None
        if not isinstance(value, dict):
            self._json_response(HTTPStatus.BAD_REQUEST, {"error": "JSON body must be an object"})
            return None
        return value

    def do_GET(self) -> None:  # noqa: N802
        snapshot = self.runtime.snapshot()
        pool = asdict(snapshot)
        if self.path == "/healthz":
            self._json_response(
                HTTPStatus.OK, {"status": "ok", "lean_version": "4.30.0", "pool": pool}
            )
            return
        if self.path == "/readyz":
            ready = snapshot.state == "running" and snapshot.ready_workers > 0
            self._json_response(
                HTTPStatus.OK if ready else HTTPStatus.SERVICE_UNAVAILABLE,
                {"status": "ready" if ready else "not_ready", "pool": pool},
            )
            return
        self._json_response(HTTPStatus.NOT_FOUND, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        if self.path == "/api/v1/check":
            self._handle_check()
            return
        if self.path == "/api/v1/verify_proof":
            self._handle_verify_proof()
            return
        self._json_response(HTTPStatus.NOT_FOUND, {"error": "not found"})

    def _handle_check(self) -> None:
        request = self._request_json()
        if request is None:
            return
        code = request.get("code")
        if not isinstance(code, str) or not code.strip():
            self._json_response(
                HTTPStatus.BAD_REQUEST, {"error": "code must be a non-empty string"}
            )
            return
        timeout = request.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS)
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
            self._json_response(
                HTTPStatus.BAD_REQUEST, {"error": "timeout_seconds must be a number"}
            )
            return
        if not 0 < float(timeout) <= MAX_TIMEOUT_SECONDS:
            self._json_response(
                HTTPStatus.BAD_REQUEST,
                {"error": f"timeout_seconds must be between 0 and {MAX_TIMEOUT_SECONDS:g}"},
            )
            return
        allow_sorry = request.get("allow_sorry", False)
        if not isinstance(allow_sorry, bool):
            self._json_response(
                HTTPStatus.BAD_REQUEST, {"error": "allow_sorry must be a boolean"}
            )
            return

        started = time.perf_counter()
        worker_request = WorkerRequest(request_id=uuid.uuid4().hex, code=code)
        try:
            result = self.runtime.compile(worker_request, timeout_seconds=float(timeout))
        except PoolOverloadedError:
            self._json_response(
                HTTPStatus.SERVICE_UNAVAILABLE,
                {"error": "compiler queue is full", "retryable": True},
            )
            return
        except PoolTimeoutError as exc:
            total_ms = (time.perf_counter() - started) * 1000
            self._json_response(
                HTTPStatus.OK,
                {
                    "okay": False,
                    "timed_out": True,
                    "time_ms": total_ms,
                    "lean_version": "4.30.0",
                    "warnings": [],
                    "errors": [{"severity": "error", "message": str(exc)}],
                    "timings": {
                        "total_ms": total_ms,
                        "queue_ms": exc.queue_ms,
                        "compile_ms": exc.compile_ms,
                    },
                },
            )
            return
        except (PoolWorkerError, PoolClosedError) as exc:
            self._json_response(
                HTTPStatus.SERVICE_UNAVAILABLE,
                _worker_error_body(exc),
            )
            return

        total_ms = (time.perf_counter() - started) * 1000
        self._json_response(
            HTTPStatus.OK, _result_body(result, total_ms, allow_sorry=allow_sorry)
        )

    def _handle_verify_proof(self) -> None:
        request_json = self._request_json(allow_text_plain=True)
        if request_json is None:
            return
        try:
            request = VerifyProofRequest.from_dict(request_json)
        except RequestValidationError as exc:
            self._json_response(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
            return

        started = time.perf_counter()
        worker_request = VerifyWorkerRequest(
            request_id=uuid.uuid4().hex,
            formal_statement=request.formal_statement,
            content=request.content,
            use_def_eq=request.use_def_eq,
        )
        try:
            result = self.runtime.verify(
                worker_request, timeout_seconds=request.timeout_seconds
            )
        except PoolOverloadedError:
            self._json_response(
                HTTPStatus.SERVICE_UNAVAILABLE,
                {"error": "compiler queue is full", "retryable": True},
            )
            return
        except PoolTimeoutError as exc:
            self._json_response(
                HTTPStatus.OK,
                {"error": str(exc), "error_type": "LeanTimeout"},
            )
            return
        except (PoolWorkerError, PoolClosedError) as exc:
            self._json_response(
                HTTPStatus.SERVICE_UNAVAILABLE,
                _worker_error_body(exc),
            )
            return

        total_ms = (time.perf_counter() - started) * 1000
        self._json_response(
            HTTPStatus.OK,
            _verify_result_body(result, total_ms=total_ms, content=request.content),
        )


def _result_body(
    result: WorkerResult, total_ms: float, *, allow_sorry: bool
) -> dict[str, Any]:
    queue_ms = max(0.0, total_ms - result.compile_ms)
    warnings = [item.to_dict() for item in result.warnings]
    errors = [item.to_dict() for item in result.errors]
    rejected_sorries = [
        item for item in result.warnings if item.message.strip() == SORRY_WARNING_MESSAGE
    ]
    if not allow_sorry:
        for diagnostic in rejected_sorries:
            error = diagnostic.to_dict()
            error["severity"] = "error"
            error["message"] = "declaration uses `sorry`, but allow_sorry is false"
            errors.append(error)
    return {
        "okay": result.status == "ok" and (allow_sorry or not rejected_sorries),
        "timed_out": False,
        "time_ms": total_ms,
        "lean_version": "4.30.0",
        "warnings": warnings,
        "errors": errors,
        "timings": {
            "total_ms": total_ms,
            "queue_ms": queue_ms,
            "compile_ms": result.compile_ms,
        },
    }


def _verify_result_body(
    result: VerifyWorkerResult, *, total_ms: float, content: str
) -> dict[str, Any]:
    lean_errors = [item.message for item in result.errors]
    lean_warnings = [item.message for item in result.warnings]
    tool_errors = list(result.tool_errors)
    failed_declarations = list(result.failed_declarations)
    okay = (
        result.status == "ok"
        and not lean_errors
        and not tool_errors
        and not failed_declarations
    )
    return {
        "okay": okay,
        "content": content,
        "lean_messages": {
            "errors": lean_errors,
            "warnings": lean_warnings,
            "infos": [],
        },
        "tool_messages": {
            "errors": tool_errors,
            "warnings": [],
            "infos": [],
        },
        "timings": {
            "total_ms": total_ms,
            "formal_statement_ms": result.formal_statement_ms,
            "declarations_ms": result.declarations_ms,
            "candidate_ms": result.candidate_ms,
        },
        "failed_declarations": failed_declarations,
    }


def create_runtime(
    *,
    worker_count: int = 2,
    queue_capacity: int = 8,
    worker_command: Sequence[str] | None = None,
    worker_startup_timeout_seconds: float = 180.0,
    worker_startup_parallelism: int = 8,
) -> CompilerPoolRuntime:
    command = DEFAULT_WORKER_COMMAND if worker_command is None else tuple(worker_command)

    def backend_factory() -> WorkerProcessBackend:
        return WorkerProcessBackend(
            command,
            cwd=PROJECT_ROOT,
            startup_timeout_seconds=worker_startup_timeout_seconds,
        )

    pool = CompilerPool(
        backend_factory,
        worker_count=worker_count,
        queue_capacity=queue_capacity,
        default_timeout_seconds=DEFAULT_TIMEOUT_SECONDS,
        startup_parallelism=worker_startup_parallelism,
    )
    return CompilerPoolRuntime(pool)


def create_server(
    host: str = "127.0.0.1",
    port: int = 8000,
    *,
    runtime: CompilerPoolRuntime | None = None,
    worker_count: int = 2,
    queue_capacity: int = 8,
    worker_command: Sequence[str] | None = None,
    worker_startup_timeout_seconds: float = 180.0,
    worker_startup_parallelism: int = 8,
) -> LeanHTTPServer:
    runtime = runtime or create_runtime(
        worker_count=worker_count,
        queue_capacity=queue_capacity,
        worker_command=worker_command,
        worker_startup_timeout_seconds=worker_startup_timeout_seconds,
        worker_startup_parallelism=worker_startup_parallelism,
    )
    server = LeanHTTPServer((host, port), runtime)
    try:
        runtime.start()
    except BaseException:
        server.server_close()
        raise
    return server
