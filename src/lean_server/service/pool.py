from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
from collections import OrderedDict, deque
from collections.abc import Callable
from dataclasses import dataclass

from lean_server.backend import CompilerBackend
from lean_server.protocol import WorkerJobRequest, WorkerJobResult


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
    """A worker failed, or the input was quarantined after an earlier panic."""

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
    quarantined_inputs: int
    quarantine_hits: int
    quarantine_evictions: int


@dataclass(slots=True)
class _Job:
    request: WorkerJobRequest
    fingerprint: bytes
    future: asyncio.Future[WorkerJobResult]
    timeout_seconds: float
    deadline: float
    started: bool = False
    execution_started_at: float | None = None


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


def _request_fingerprint(request: WorkerJobRequest) -> bytes:
    payload = request.to_dict()
    # IDs identify attempts, not input. Include operation, protocol, and every
    # compiler option; HTTP deadlines and allow_sorry do not affect execution.
    del payload["request_id"]
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).digest()


class CompilerPool:
    """Bounded pool dispatching the earliest job whose input is not in flight."""

    def __init__(
        self,
        backend_factory: Callable[[], CompilerBackend],
        *,
        worker_count: int,
        queue_capacity: int,
        default_timeout_seconds: float = 30.0,
        startup_parallelism: int = 8,
        quarantine_capacity: int = 4096,
    ) -> None:
        if worker_count < 1:
            raise ValueError("worker_count must be at least 1")
        if queue_capacity < 1:
            raise ValueError("queue_capacity must be at least 1")
        if not math.isfinite(default_timeout_seconds) or default_timeout_seconds <= 0:
            raise ValueError("default_timeout_seconds must be finite and positive")
        if startup_parallelism < 1:
            raise ValueError("startup_parallelism must be at least 1")
        if quarantine_capacity < 1:
            raise ValueError("quarantine_capacity must be at least 1")
        self._backend_factory = backend_factory
        self._worker_count = worker_count
        self._queue_capacity = queue_capacity
        self._default_timeout_seconds = default_timeout_seconds
        self._startup_parallelism = startup_parallelism
        self._quarantine_capacity = quarantine_capacity
        # Pool instances own a fixed backend environment. Restarting the service
        # (including a toolchain upgrade) starts a fresh quarantine namespace.
        self._quarantine: OrderedDict[bytes, str] = OrderedDict()
        self._quarantine_hits = 0
        self._quarantine_evictions = 0
        self._in_flight: set[bytes] = set()
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
            quarantined_inputs=len(self._quarantine),
            quarantine_hits=self._quarantine_hits,
            quarantine_evictions=self._quarantine_evictions,
        )

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
        fingerprint = _request_fingerprint(request)
        if error := self._quarantined_error(fingerprint):
            raise error
        if len(self._queue) >= self._queue_capacity:
            raise PoolOverloadedError("compiler queue is full")
        loop = asyncio.get_running_loop()
        future = loop.create_future()
        job = _Job(request, fingerprint, future, float(timeout), loop.time() + timeout)
        self._queue.append(job)
        self._jobs_available.set()
        try:
            async with asyncio.timeout_at(job.deadline):
                return await future
        except TimeoutError as exc:
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
            while True:
                self._jobs_available.clear()
                item = next(
                    (job for job in self._queue if job.fingerprint not in self._in_flight),
                    None,
                )
                if item is not None:
                    self._queue.remove(item)
                    break
                await self._jobs_available.wait()
            item.started = True
            if item.future.done():
                continue
            remaining = item.deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                item.future.set_exception(_timeout_error(item))
                continue
            item.execution_started_at = asyncio.get_running_loop().time()
            self._in_flight.add(item.fingerprint)
            self._active_workers += 1
            replace = False
            try:
                result = await asyncio.wait_for(backend.compile(item.request), timeout=remaining)
            except TimeoutError:
                if not item.future.done():
                    item.future.set_exception(_timeout_error(item))
                replace = True
            except asyncio.CancelledError:
                if not item.future.done():
                    item.future.set_exception(PoolClosedError("pool is closing"))
                raise
            except Exception as exc:
                # Only an explicit backend panic signal poisons input. Ordinary
                # timeouts, protocol failures and transient exits stay retryable.
                if getattr(exc, "quarantine_input", False):
                    self._quarantine_input(item.fingerprint, exc.error_type)
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
                self._in_flight.remove(item.fingerprint)
                self._jobs_available.set()
            if replace:
                replacement = await self._replace_backend(index, backend)
                if replacement is None:
                    return
                backend = replacement

    def _quarantined_error(self, fingerprint: bytes) -> PoolWorkerError | None:
        error_type = self._quarantine.get(fingerprint)
        if error_type is None:
            return None
        self._quarantine.move_to_end(fingerprint)
        self._quarantine_hits += 1
        return PoolWorkerError(
            "worker previously panicked for this input; input is quarantined",
            retryable=False,
            error_type=error_type,
        )

    def _quarantine_input(self, fingerprint: bytes, error_type: str) -> None:
        self._quarantine[fingerprint] = error_type
        self._quarantine.move_to_end(fingerprint)
        if len(self._quarantine) > self._quarantine_capacity:
            self._quarantine.popitem(last=False)
            self._quarantine_evictions += 1
        logger.warning("quarantined worker input %s (%s)", fingerprint.hex(), error_type)
        # Reject waiting duplicates now, even if the only worker is restarting.
        for job in tuple(self._queue):
            if job.fingerprint == fingerprint:
                self._queue.remove(job)
                if not job.future.done():
                    job.future.set_exception(self._quarantined_error(fingerprint))

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
            return replacement
        return None

    def _cancel_queued_jobs(self) -> None:
        while self._queue:
            item = self._queue.popleft()
            if not item.future.done():
                item.future.set_exception(PoolClosedError("pool is closing"))
