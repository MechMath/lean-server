from __future__ import annotations

import asyncio
import logging
import math
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass

from lean_server.backend import CompilerBackend
from lean_server.protocol import WorkerJobRequest, WorkerJobResult
from lean_server.workers.process import (
    WorkerExitedError,
    WorkerMessageTooLargeError,
    WorkerPanicError,
    WorkerProtocolError,
)

from .metrics import MetricsSnapshot, ServiceMetrics


logger = logging.getLogger(__name__)


class PoolError(RuntimeError):
    """Base class for scheduler errors."""


class PoolClosedError(PoolError):
    """The pool is not accepting requests."""


class PoolOverloadedError(PoolError):
    """The bounded pending queue is full."""


class PoolTimeoutError(PoolError):
    """A compiler request exceeded its total queue and execution budget."""

    def __init__(self, message: str, *, queue_ms: float = 0, compile_ms: float = 0) -> None:
        super().__init__(message)
        self.queue_ms = queue_ms
        self.compile_ms = compile_ms


class PoolWorkerError(PoolError):
    """A worker failed and its process slot is being replaced."""

    def __init__(self, message: str, *, retryable: bool = True, error_type: str | None = None):
        super().__init__(message)
        self.retryable = retryable
        self.error_type = error_type


@dataclass(frozen=True, slots=True)
class PoolSnapshot:
    state: str
    worker_count: int
    ready_workers: int
    active_workers: int
    queue_depth: int
    queue_capacity: int
    replacements: int


@dataclass(slots=True)
class _Job:
    request: WorkerJobRequest
    future: asyncio.Future[WorkerJobResult]
    timeout_seconds: float
    deadline: float
    started: bool = False
    execution_started_at: float | None = None
    timeout_recorded: bool = False


def _timeout_error(job: _Job) -> PoolTimeoutError:
    now = asyncio.get_running_loop().time()
    enqueued_at = job.deadline - job.timeout_seconds
    execution_started_at = job.execution_started_at
    return PoolTimeoutError(
        f"request exceeded {job.timeout_seconds:g} seconds including queue wait",
        queue_ms=((execution_started_at if execution_started_at is not None else now) - enqueued_at)
        * 1000,
        compile_ms=0 if execution_started_at is None else (now - execution_started_at) * 1000,
    )


class CompilerPool:
    """Fixed-size compiler pool with a bounded FIFO pending queue."""

    def __init__(
        self,
        backend_factory: Callable[[], CompilerBackend],
        *,
        worker_count: int,
        queue_capacity: int,
        default_timeout_seconds: float = 30.0,
        startup_parallelism: int = 8,
        metrics: ServiceMetrics | None = None,
    ) -> None:
        if worker_count < 1:
            raise ValueError("worker_count must be at least 1")
        if queue_capacity < 1:
            raise ValueError("queue_capacity must be at least 1")
        if not math.isfinite(default_timeout_seconds) or default_timeout_seconds <= 0:
            raise ValueError("default_timeout_seconds must be finite and positive")
        if startup_parallelism < 1:
            raise ValueError("startup_parallelism must be at least 1")
        self._backend_factory = backend_factory
        self._worker_count = worker_count
        self._queue_capacity = queue_capacity
        self._default_timeout_seconds = default_timeout_seconds
        self._startup_parallelism = startup_parallelism
        self.metrics = metrics or ServiceMetrics()
        self._queue: deque[_Job] = deque()
        self._jobs_available = asyncio.Event()
        self._backends: list[CompilerBackend] = []
        self._tasks: list[asyncio.Task[None]] = []
        self._state = "created"
        self._active_workers = 0
        self._ready_workers = 0
        self._replacements = 0

    @property
    def snapshot(self) -> PoolSnapshot:
        return PoolSnapshot(
            state=self._state,
            worker_count=self._worker_count,
            ready_workers=self._ready_workers,
            active_workers=self._active_workers,
            queue_depth=len(self._queue),
            queue_capacity=self._queue_capacity,
            replacements=self._replacements,
        )

    @property
    def metrics_snapshot(self) -> MetricsSnapshot:
        return self.metrics.snapshot()

    async def start(self) -> None:
        if self._state != "created":
            raise PoolError(f"cannot start pool in state {self._state}")
        self._state = "starting"
        backends = [self._backend_factory() for _ in range(self._worker_count)]
        startup_limit = asyncio.Semaphore(
            min(self._startup_parallelism, self._worker_count)
        )

        async def start_backend(backend: CompilerBackend) -> None:
            async with startup_limit:
                await backend.start()

        startup_tasks = [
            asyncio.create_task(start_backend(backend)) for backend in backends
        ]
        try:
            await asyncio.gather(*startup_tasks)
        except BaseException:
            for task in startup_tasks:
                task.cancel()
            await asyncio.gather(*startup_tasks, return_exceptions=True)
            await asyncio.gather(
                *(backend.close() for backend in backends), return_exceptions=True
            )
            self._state = "closed"
            raise

        self._backends = backends
        self._ready_workers = len(backends)
        self._tasks = [
            asyncio.create_task(self._run_worker(index, backend), name=f"compiler-worker-{index}")
            for index, backend in enumerate(backends)
        ]
        self._state = "running"

    async def compile(
        self, request: WorkerJobRequest, *, timeout_seconds: float | None = None
    ) -> WorkerJobResult:
        if self._state != "running":
            raise PoolClosedError(f"pool is {self._state}")
        timeout = self._default_timeout_seconds if timeout_seconds is None else timeout_seconds
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("timeout_seconds must be finite and positive")
        if len(self._queue) >= self._queue_capacity:
            self.metrics.increment("overload_responses_total")
            raise PoolOverloadedError("compiler queue is full")
        loop = asyncio.get_running_loop()
        future = loop.create_future()
        job = _Job(request, future, float(timeout), loop.time() + timeout)
        self._queue.append(job)
        self._jobs_available.set()
        try:
            async with asyncio.timeout_at(job.deadline):
                return await future
        except TimeoutError as exc:
            self._record_timeout(job)
            raise _timeout_error(job) from exc
        finally:
            # Expired/cancelled queued jobs must release capacity immediately, even
            # when every worker is stuck restarting and cannot dequeue them.
            if not job.started and job in self._queue:
                self._queue.remove(job)

    async def close(self) -> None:
        if self._state == "closed":
            return
        if self._state == "created":
            self._state = "closed"
            return
        if self._state == "starting":
            raise PoolError("cannot close pool while it is starting")

        self._state = "closing"
        self._cancel_queued_jobs()
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        await asyncio.gather(*(backend.close() for backend in self._backends), return_exceptions=True)
        self._tasks.clear()
        self._backends.clear()
        self._ready_workers = 0
        self._state = "closed"

    async def _run_worker(self, index: int, backend: CompilerBackend) -> None:
        while True:
            while not self._queue:
                self._jobs_available.clear()
                await self._jobs_available.wait()
            item = self._queue.popleft()
            item.started = True
            if item.future.done():
                continue
            remaining = item.deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                self._record_timeout(item)
                item.future.set_exception(_timeout_error(item))
                continue
            item.execution_started_at = asyncio.get_running_loop().time()
            self._active_workers += 1
            replace = False
            try:
                result = await asyncio.wait_for(backend.compile(item.request), timeout=remaining)
            except TimeoutError:
                self._record_timeout(item)
                if not item.future.done():
                    item.future.set_exception(_timeout_error(item))
                replace = True
            except asyncio.CancelledError:
                if not item.future.done():
                    item.future.set_exception(PoolClosedError("pool is closing"))
                raise
            except Exception as exc:
                self._record_worker_failure(exc)
                if not item.future.done():
                    item.future.set_exception(PoolWorkerError(
                        str(exc),
                        retryable=getattr(exc, "retryable", True),
                        error_type=getattr(exc, "error_type", None),
                    ))
                replace = True
            else:
                if result.status == "internal_error":
                    if not item.future.done():
                        item.future.set_exception(PoolWorkerError("worker returned internal_error"))
                    replace = True
                elif not item.future.done():
                    item.future.set_result(result)
            finally:
                self._active_workers -= 1
            if replace:
                replacement = await self._replace_backend(index, backend)
                if replacement is None:
                    return
                backend = replacement

    async def _close_backend(self, backend: CompilerBackend) -> None:
        try:
            await backend.close()
        except Exception:
            # A failed log drain or cleanup must not silently kill this slot.
            logger.exception("compiler worker cleanup failed")

    async def _replace_backend(
        self, index: int, backend: CompilerBackend
    ) -> CompilerBackend | None:
        self._ready_workers -= 1
        await self._close_backend(backend)
        delay = 0.05
        while self._state == "running":
            try:
                replacement = self._backend_factory()
                # Track ownership before awaiting ready, so shutdown also owns
                # partially started replacements.
                self._backends[index] = replacement
                await replacement.start()
            except Exception:
                self.metrics.increment("replacement_startup_failures_total")
                logger.exception("compiler worker replacement failed")
                await self._close_backend(self._backends[index])
                await asyncio.sleep(delay)
                delay = min(delay * 2, 2.0)
                continue
            except asyncio.CancelledError:
                await self._close_backend(self._backends[index])
                raise
            self._ready_workers += 1
            self._replacements += 1
            self.metrics.increment("worker_replacements_total")
            return replacement
        return None

    def _record_timeout(self, job: _Job) -> None:
        if job.timeout_recorded:
            return
        job.timeout_recorded = True
        name = (
            "queue_timeouts_total"
            if job.execution_started_at is None
            else "execution_timeouts_total"
        )
        self.metrics.increment(name)

    def _record_worker_failure(self, exc: Exception) -> None:
        if isinstance(exc, WorkerExitedError):
            self.metrics.increment("worker_crashes_total")
        if isinstance(exc, WorkerPanicError):
            self.metrics.increment("lean_panics_total")
        if isinstance(exc, WorkerProtocolError):
            self.metrics.increment("worker_protocol_errors_total")
        if isinstance(exc, WorkerMessageTooLargeError):
            self.metrics.increment("worker_message_too_large_total")

    def _cancel_queued_jobs(self) -> None:
        while self._queue:
            item = self._queue.popleft()
            if not item.future.done():
                item.future.set_exception(PoolClosedError("pool is closing"))
