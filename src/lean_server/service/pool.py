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


@dataclass(frozen=True, slots=True)
class PoolSnapshot:
    state: str
    worker_count: int
    active_workers: int
    queue_depth: int
    queue_capacity: int


@dataclass(slots=True)
class _Job:
    request: WorkerRequest
    future: asyncio.Future[WorkerResult]


_STOP = object()


class CompilerPool:
    """Fixed-size compiler pool with a bounded FIFO pending queue."""

    def __init__(
        self,
        backend_factory: Callable[[], CompilerBackend],
        *,
        worker_count: int,
        queue_capacity: int,
    ) -> None:
        if worker_count < 1:
            raise ValueError("worker_count must be at least 1")
        if queue_capacity < 1:
            raise ValueError("queue_capacity must be at least 1")
        self._backend_factory = backend_factory
        self._worker_count = worker_count
        self._queue_capacity = queue_capacity
        self._queue: asyncio.Queue[_Job | object] = asyncio.Queue(maxsize=queue_capacity)
        self._backends: list[CompilerBackend] = []
        self._tasks: list[asyncio.Task[None]] = []
        self._state = "created"
        self._active_workers = 0

    @property
    def snapshot(self) -> PoolSnapshot:
        return PoolSnapshot(
            state=self._state,
            worker_count=len(self._backends),
            active_workers=self._active_workers,
            queue_depth=self._queue.qsize(),
            queue_capacity=self._queue_capacity,
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
        self._tasks = [
            asyncio.create_task(self._run_worker(index, backend), name=f"compiler-worker-{index}")
            for index, backend in enumerate(backends)
        ]
        self._state = "running"

    async def compile(self, request: WorkerRequest) -> WorkerResult:
        if self._state != "running":
            raise PoolClosedError(f"pool is {self._state}")
        future = asyncio.get_running_loop().create_future()
        try:
            self._queue.put_nowait(_Job(request=request, future=future))
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
        await self._queue.join()
        for _ in self._tasks:
            await self._queue.put(_STOP)
        await asyncio.gather(*self._tasks, return_exceptions=True)
        await asyncio.gather(*(backend.close() for backend in self._backends), return_exceptions=True)
        self._tasks.clear()
        self._backends.clear()
        self._state = "closed"

    async def _run_worker(self, index: int, backend: CompilerBackend) -> None:
        del index
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
                    result = await backend.compile(item.request)
                except BaseException as exc:
                    if not item.future.done():
                        item.future.set_exception(exc)
                else:
                    if not item.future.done():
                        item.future.set_result(result)
                finally:
                    self._active_workers -= 1
            finally:
                self._queue.task_done()
