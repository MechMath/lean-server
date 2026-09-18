from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass

from lean_server.backend import CompilerBackend
from lean_server.protocol import WorkerRequest, WorkerResult


class PoolError(RuntimeError):
    """Base class for scheduler errors."""


class PoolClosedError(PoolError):
    """The pool is not accepting requests."""


class PoolOverloadedError(PoolError):
    """The bounded pending queue is full."""


class PoolTimeoutError(PoolError):
    """A compiler request exceeded its wall-clock execution timeout."""


class PoolWorkerError(PoolError):
    """A worker failed and its process slot is being replaced."""


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
    request: WorkerRequest
    future: asyncio.Future[WorkerResult]
    timeout_seconds: float


_STOP = object()


class CompilerPool:
    """Fixed-size compiler pool with a bounded FIFO pending queue."""

    def __init__(
        self,
        backend_factory: Callable[[], CompilerBackend],
        *,
        worker_count: int,
        queue_capacity: int,
        default_timeout_seconds: float = 30.0,
    ) -> None:
        if worker_count < 1:
            raise ValueError("worker_count must be at least 1")
        if queue_capacity < 1:
            raise ValueError("queue_capacity must be at least 1")
        if default_timeout_seconds <= 0:
            raise ValueError("default_timeout_seconds must be positive")
        self._backend_factory = backend_factory
        self._worker_count = worker_count
        self._queue_capacity = queue_capacity
        self._default_timeout_seconds = default_timeout_seconds
        self._queue: asyncio.Queue[_Job | object] = asyncio.Queue(maxsize=queue_capacity)
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
            queue_depth=self._queue.qsize(),
            queue_capacity=self._queue_capacity,
            replacements=self._replacements,
        )

    async def start(self) -> None:
        if self._state != "created":
            raise PoolError(f"cannot start pool in state {self._state}")
        self._state = "starting"
        backends = [self._backend_factory() for _ in range(self._worker_count)]
        started: list[CompilerBackend] = []
        try:
            for backend in backends:
                await backend.start()
                started.append(backend)
        except BaseException:
            await asyncio.gather(*(backend.close() for backend in started), return_exceptions=True)
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
        self, request: WorkerRequest, *, timeout_seconds: float | None = None
    ) -> WorkerResult:
        if self._state != "running":
            raise PoolClosedError(f"pool is {self._state}")
        timeout = self._default_timeout_seconds if timeout_seconds is None else timeout_seconds
        if timeout <= 0:
            raise ValueError("timeout_seconds must be positive")
        future = asyncio.get_running_loop().create_future()
        try:
            self._queue.put_nowait(
                _Job(request=request, future=future, timeout_seconds=float(timeout))
            )
        except asyncio.QueueFull as exc:
            raise PoolOverloadedError("compiler queue is full") from exc
        return await future

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
            item = await self._queue.get()
            try:
                if item is _STOP:
                    return
                assert isinstance(item, _Job)
                if item.future.cancelled():
                    continue
                self._active_workers += 1
                try:
                    result = await asyncio.wait_for(
                        backend.compile(item.request), timeout=item.timeout_seconds
                    )
                except TimeoutError:
                    if not item.future.done():
                        item.future.set_exception(
                            PoolTimeoutError(
                                f"request exceeded {item.timeout_seconds:g} seconds"
                            )
                        )
                    replacement = await self._replace_backend(index, backend)
                    if replacement is None:
                        return
                    backend = replacement
                except asyncio.CancelledError:
                    if not item.future.done():
                        item.future.set_exception(PoolClosedError("pool is closing"))
                    raise
                except Exception as exc:
                    if not item.future.done():
                        item.future.set_exception(PoolWorkerError(str(exc)))
                    replacement = await self._replace_backend(index, backend)
                    if replacement is None:
                        return
                    backend = replacement
                else:
                    if not item.future.done():
                        item.future.set_result(result)
                finally:
                    self._active_workers -= 1
            finally:
                self._queue.task_done()

    async def _replace_backend(
        self, index: int, backend: CompilerBackend
    ) -> CompilerBackend | None:
        self._ready_workers -= 1
        await backend.close()
        delay = 0.05
        while self._state == "running":
            replacement = self._backend_factory()
            try:
                await replacement.start()
            except Exception:
                await replacement.close()
                await asyncio.sleep(delay)
                delay = min(delay * 2, 2.0)
                continue
            self._backends[index] = replacement
            self._ready_workers += 1
            self._replacements += 1
            return replacement
        return None

    def _cancel_queued_jobs(self) -> None:
        while True:
            try:
                item = self._queue.get_nowait()
            except asyncio.QueueEmpty:
                return
            try:
                if isinstance(item, _Job) and not item.future.done():
                    item.future.set_exception(PoolClosedError("pool is closing"))
            finally:
                self._queue.task_done()
