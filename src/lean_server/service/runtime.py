from __future__ import annotations

import asyncio
import threading
from concurrent.futures import Future
from typing import Any, Coroutine

from lean_server.protocol import (
    VerifyWorkerRequest,
    VerifyWorkerResult,
    WorkerRequest,
    WorkerResult,
)

from .metrics import MetricsSnapshot
from .pool import CompilerPool, PoolSnapshot


class CompilerPoolRuntime:
    """Own an asyncio compiler pool on a dedicated event-loop thread."""

    def __init__(self, pool: CompilerPool) -> None:
        self.pool = pool
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._startup_error: BaseException | None = None

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._run_loop, name="compiler-pool-runtime", daemon=True
        )
        self._thread.start()
        self._ready.wait()
        if self._startup_error is not None:
            raise RuntimeError("compiler pool failed to start") from self._startup_error

    def compile(self, request: WorkerRequest, *, timeout_seconds: float) -> WorkerResult:
        future = self._submit(self.pool.compile(request, timeout_seconds=timeout_seconds))
        result = future.result()
        if not isinstance(result, WorkerResult):
            raise RuntimeError("compiler pool returned a verification result for compile request")
        return result

    def verify(
        self, request: VerifyWorkerRequest, *, timeout_seconds: float
    ) -> VerifyWorkerResult:
        future = self._submit(self.pool.compile(request, timeout_seconds=timeout_seconds))
        result = future.result()
        if not isinstance(result, VerifyWorkerResult):
            raise RuntimeError("compiler pool returned a compile result for verification request")
        return result

    def snapshot(self) -> PoolSnapshot:
        async def get_snapshot() -> PoolSnapshot:
            return self.pool.snapshot

        return self._submit(get_snapshot()).result()

    def metrics_snapshot(self) -> MetricsSnapshot:
        return self.pool.metrics_snapshot

    def increment_metric(self, name: str) -> None:
        self.pool.metrics.increment(name)

    def close(self) -> None:
        loop = self._loop
        thread = self._thread
        if loop is None or thread is None:
            return
        if loop.is_running():
            self._submit(self.pool.close()).result()
            loop.call_soon_threadsafe(loop.stop)
        thread.join()
        self._thread = None
        self._loop = None

    def _submit(self, coroutine: Coroutine[Any, Any, Any]) -> Future[Any]:
        loop = self._loop
        if loop is None or not loop.is_running():
            coroutine.close()
            raise RuntimeError("compiler pool runtime is not running")
        return asyncio.run_coroutine_threadsafe(coroutine, loop)

    def _run_loop(self) -> None:
        loop = asyncio.new_event_loop()
        self._loop = loop
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self.pool.start())
        except BaseException as exc:
            self._startup_error = exc
            self._ready.set()
            loop.close()
            return
        self._ready.set()
        try:
            loop.run_forever()
        finally:
            loop.close()
