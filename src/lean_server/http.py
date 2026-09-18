from __future__ import annotations

import json
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from .compiler import DEFAULT_TIMEOUT_SECONDS, MAX_TIMEOUT_SECONDS, compile_lean


MAX_REQUEST_BYTES = 2 * 1024 * 1024


class LeanHTTPServer(ThreadingHTTPServer):
    daemon_threads = True


class LeanRequestHandler(BaseHTTPRequestHandler):
    server_version = "lean-server/0.1"

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
        if self.path == "/healthz":
            self._json_response(HTTPStatus.OK, {"status": "ok", "lean_version": "4.30.0"})
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
        result = compile_lean(code, float(timeout))
        self._json_response(HTTPStatus.OK, result.to_dict())


def create_server(host: str = "127.0.0.1", port: int = 8000) -> LeanHTTPServer:
    return LeanHTTPServer((host, port), LeanRequestHandler)
