from __future__ import annotations

import json
import time
import uuid
from dataclasses import asdict
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Sequence, cast

from .compiler import DEFAULT_TIMEOUT_SECONDS, MAX_TIMEOUT_SECONDS, PROJECT_ROOT
from .protocol import WorkerRequest, WorkerResult
from .service import (
    CompilerPool,
    CompilerPoolRuntime,
    PoolClosedError,
    PoolOverloadedError,
    PoolTimeoutError,
    PoolWorkerError,
)
from .workers import LeanCliBackend, WorkerProcessBackend


MAX_REQUEST_BYTES = 2 * 1024 * 1024


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

    def _request_json(self) -> dict[str, Any] | None:
        if self.headers.get_content_type() != "application/json":
            self._json_response(
                HTTPStatus.UNSUPPORTED_MEDIA_TYPE, {"error": "expected application/json"}
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
        if self.path != "/api/v1/check":
            self._json_response(HTTPStatus.NOT_FOUND, {"error": "not found"})
            return
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
                    "timings": {"total_ms": total_ms, "queue_ms": 0, "compile_ms": total_ms},
                },
            )
            return
        except (PoolWorkerError, PoolClosedError) as exc:
            self._json_response(
                HTTPStatus.SERVICE_UNAVAILABLE,
                {"error": str(exc), "retryable": True},
            )
            return

        total_ms = (time.perf_counter() - started) * 1000
        self._json_response(HTTPStatus.OK, _result_body(result, total_ms))


def _result_body(result: WorkerResult, total_ms: float) -> dict[str, Any]:
    queue_ms = max(0.0, total_ms - result.compile_ms)
    return {
        "okay": result.status == "ok",
        "timed_out": False,
        "time_ms": total_ms,
        "lean_version": "4.30.0",
        "warnings": [item.to_dict() for item in result.warnings],
        "errors": [item.to_dict() for item in result.errors],
        "timings": {
            "total_ms": total_ms,
            "queue_ms": queue_ms,
            "compile_ms": result.compile_ms,
        },
    }


def create_runtime(
    *,
    worker_count: int = 2,
    queue_capacity: int = 8,
    worker_command: Sequence[str] | None = None,
) -> CompilerPoolRuntime:
    if worker_command is None:
        backend_factory = LeanCliBackend
    else:
        command = tuple(worker_command)

        def backend_factory() -> WorkerProcessBackend:
            return WorkerProcessBackend(command, cwd=PROJECT_ROOT)

    pool = CompilerPool(
        backend_factory,
        worker_count=worker_count,
        queue_capacity=queue_capacity,
        default_timeout_seconds=DEFAULT_TIMEOUT_SECONDS,
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
) -> LeanHTTPServer:
    runtime = runtime or create_runtime(
        worker_count=worker_count,
        queue_capacity=queue_capacity,
        worker_command=worker_command,
    )
    server = LeanHTTPServer((host, port), runtime)
    try:
        runtime.start()
    except BaseException:
        server.server_close()
        raise
    return server
