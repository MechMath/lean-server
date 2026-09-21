from __future__ import annotations

import json
import selectors
import subprocess
import time
from pathlib import Path
from typing import Any

from lean_server.protocol import WorkerReady, WorkerResult, decode_worker_message


PROJECT_ROOT = Path(__file__).parents[2]
WORKER = PROJECT_ROOT / ".lake" / "build" / "bin" / "lean-server-worker"


class Worker:
    def __init__(self, startup_timeout: float = 180.0) -> None:
        self.started_at = time.perf_counter()
        self.process = subprocess.Popen(
            ["lake", "env", str(WORKER)],
            cwd=PROJECT_ROOT,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        assert self.process.stdout is not None
        # Mathlib is cached, but loading its environment can still take several seconds.
        deadline = time.monotonic() + startup_timeout
        selector = selectors.DefaultSelector()
        selector.register(self.process.stdout, selectors.EVENT_READ)
        while (remaining := deadline - time.monotonic()) > 0:
            if not selector.select(remaining):
                break
            line = self.process.stdout.readline()
            if line:
                message = decode_worker_message(line)
                if not isinstance(message, WorkerReady):
                    raise AssertionError(f"expected ready, got {message!r}")
                self.ready_json = json.loads(line)
                self.ready_ms = (time.perf_counter() - self.started_at) * 1000
                selector.close()
                return
            if self.process.poll() is not None:
                break
        selector.close()
        if self.process.poll() is None:
            self.process.kill()
        stderr = self.process.stderr.read() if self.process.stderr else ""
        self.process.wait()
        if self.process.stdin is not None:
            self.process.stdin.close()
        if self.process.stdout is not None:
            self.process.stdout.close()
        if self.process.stderr is not None:
            self.process.stderr.close()
        raise AssertionError(f"worker did not become ready: {stderr}")

    def compile_wire(self, request_id: str, code: str) -> tuple[dict[str, Any], WorkerResult]:
        assert self.process.stdin is not None
        assert self.process.stdout is not None
        request: dict[str, Any] = {
            "protocol_version": 2,
            "type": "compile",
            "request_id": request_id,
            "code": code,
        }
        self.process.stdin.write(json.dumps(request, separators=(",", ":")) + "\n")
        self.process.stdin.flush()
        line = self.process.stdout.readline()
        raw_message = json.loads(line)
        message = decode_worker_message(line)
        if not isinstance(message, WorkerResult):
            raise AssertionError(f"expected result, got {message!r}")
        if message.request_id != request_id:
            raise AssertionError(f"wrong request id: {message.request_id!r}")
        return raw_message, message

    def compile(self, request_id: str, code: str) -> WorkerResult:
        return self.compile_wire(request_id, code)[1]

    def stdout_is_quiet(self, timeout: float = 0.1) -> bool:
        assert self.process.stdout is not None
        selector = selectors.DefaultSelector()
        selector.register(self.process.stdout, selectors.EVENT_READ)
        try:
            return not selector.select(timeout)
        finally:
            selector.close()

    def close(self) -> None:
        if self.process.stdin is not None:
            try:
                self.process.stdin.close()
            except BrokenPipeError:
                pass
        try:
            returncode = self.process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait()
            raise
        if returncode != 0:
            stderr = self.process.stderr.read() if self.process.stderr else ""
            raise AssertionError(f"worker exited with {returncode}: {stderr}")
        if self.process.stdout is not None:
            self.process.stdout.close()
        if self.process.stderr is not None:
            self.process.stderr.close()

    def __enter__(self) -> Worker:
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.close()
