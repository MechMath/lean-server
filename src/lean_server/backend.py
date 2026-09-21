from __future__ import annotations

from typing import Protocol

from .protocol import WorkerJobRequest, WorkerJobResult


class CompilerBackend(Protocol):
    """Stable boundary between scheduling code and a compiler implementation."""

    async def start(self) -> None: ...

    async def compile(self, request: WorkerJobRequest) -> WorkerJobResult: ...

    async def close(self) -> None: ...
