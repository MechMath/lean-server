from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import signal
from pathlib import Path
from typing import Sequence

from lean_server.protocol import (
    ProtocolError,
    VerifyWorkerRequest,
    VerifyWorkerResult,
    WorkerJobRequest,
    WorkerJobResult,
    WorkerReady,
    WorkerResult,
    decode_worker_message,
)


MAX_WORKER_MESSAGE_BYTES = 8 * 1024 * 1024
MAX_STDERR_TAIL_BYTES = 64 * 1024
logger = logging.getLogger(__name__)


class WorkerError(RuntimeError):
    """Base class for worker infrastructure failures."""


class WorkerStartupError(WorkerError):
    """The worker did not complete its ready handshake."""


class WorkerExitedError(WorkerError):
    """The worker exited before returning a result."""


class WorkerPanicError(WorkerExitedError):
    """A known input-dependent Lean panic must not be retried unchanged."""

    retryable = False
    error_type = "LeanPanic"


class WorkerProtocolError(WorkerError):
    """The worker violated the versioned wire protocol."""


class WorkerProcessBackend:
    """One asynchronous connection to one NDJSON worker process.

    Calls are serialized because the worker protocols allow one in-flight request per worker.
    Pooling and request timeouts belong to the scheduling layer.
    """

    def __init__(
        self,
        command: Sequence[str],
        *,
        cwd: Path | None = None,
        startup_timeout_seconds: float = 180.0,
    ) -> None:
        if not command:
            raise ValueError("worker command cannot be empty")
        if startup_timeout_seconds <= 0:
            raise ValueError("startup_timeout_seconds must be positive")
        self.command = tuple(command)
        self.cwd = cwd
        self.startup_timeout_seconds = startup_timeout_seconds
        self._process: asyncio.subprocess.Process | None = None
        self._compile_lock = asyncio.Lock()
        self._stderr_task: asyncio.Task[None] | None = None
        self._stderr_buffer = bytearray()

    @property
    def pid(self) -> int | None:
        return self._process.pid if self._process is not None else None

    @property
    def returncode(self) -> int | None:
        return self._process.returncode if self._process is not None else None

    @property
    def stderr_tail(self) -> tuple[str, ...]:
        return tuple(self._stderr_buffer.decode("utf-8", errors="replace").splitlines()[-100:])

    async def start(self) -> None:
        if self._process is not None:
            if self._process.returncode is None:
                return
            raise WorkerStartupError("worker backend cannot be restarted")
        try:
            self._process = await asyncio.create_subprocess_exec(
                *self.command,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=self.cwd,
                start_new_session=True,
                limit=MAX_WORKER_MESSAGE_BYTES,
            )
        except OSError as exc:
            raise WorkerStartupError(f"unable to start worker: {exc}") from exc

        self._stderr_task = asyncio.create_task(self._drain_stderr())
        try:
            message = await asyncio.wait_for(
                self._read_message(), timeout=self.startup_timeout_seconds
            )
        except (TimeoutError, asyncio.TimeoutError) as exc:
            await self.close()
            raise WorkerStartupError(
                "worker ready handshake timed out after "
                f"{self.startup_timeout_seconds:g} seconds"
            ) from exc
        except WorkerError as exc:
            await self.close()
            raise WorkerStartupError(f"worker ready handshake failed: {exc}") from exc
        except asyncio.CancelledError:
            await self.close()
            raise
        if not isinstance(message, WorkerReady):
            await self.close()
            raise WorkerStartupError("worker sent a result before ready")
        if message.lean_version != "4.30.0":
            await self.close()
            raise WorkerStartupError(
                f"worker uses Lean {message.lean_version}, expected Lean 4.30.0"
            )

    async def compile(self, request: WorkerJobRequest) -> WorkerJobResult:
        async with self._compile_lock:
            process = self._require_running()
            assert process.stdin is not None
            process.stdin.write(request.to_json_line().encode("utf-8"))
            try:
                await process.stdin.drain()
            except (BrokenPipeError, ConnectionResetError) as exc:
                raise WorkerExitedError("worker stdin closed") from exc
            message = await self._read_message()
            expected_type = (
                VerifyWorkerResult if isinstance(request, VerifyWorkerRequest) else WorkerResult
            )
            if not isinstance(message, expected_type):
                raise WorkerProtocolError(
                    f"worker emitted {type(message).__name__}, expected {expected_type.__name__}"
                )
            if message.request_id != request.request_id:
                raise WorkerProtocolError(
                    f"worker returned request_id {message.request_id!r}, "
                    f"expected {request.request_id!r}"
                )
            return message

    async def close(self) -> None:
        process = self._process
        if process is not None and process.returncode is None:
            self._signal_process_group(signal.SIGTERM)
            try:
                await asyncio.wait_for(process.wait(), timeout=2.0)
            except (TimeoutError, asyncio.TimeoutError):
                self._signal_process_group(signal.SIGKILL)
                await process.wait()
        if self._stderr_task is not None:
            self._stderr_task.cancel()
            try:
                await self._stderr_task
            except asyncio.CancelledError:
                pass
            except Exception:
                logger.exception("worker stderr drain failed")

    def _require_running(self) -> asyncio.subprocess.Process:
        process = self._process
        if process is None:
            raise WorkerError("worker has not been started")
        if process.returncode is not None:
            raise WorkerExitedError(f"worker exited with status {process.returncode}")
        return process

    async def _read_message(self) -> WorkerReady | WorkerJobResult:
        process = self._require_running()
        assert process.stdout is not None
        try:
            line = await process.stdout.readline()
        except ValueError as exc:
            raise WorkerProtocolError(
                f"worker message exceeds {MAX_WORKER_MESSAGE_BYTES} bytes"
            ) from exc
        if not line:
            await process.wait()
            if self._stderr_task is not None:
                await asyncio.gather(self._stderr_task, return_exceptions=True)
            detail = f"worker exited with status {process.returncode}"
            if self.stderr_tail:
                detail += f": {self.stderr_tail[-1]}"
            if b"INTERNAL PANIC: Nat.pow exponent is too big" in self._stderr_buffer:
                raise WorkerPanicError(detail)
            raise WorkerExitedError(detail)
        try:
            if len(line) > MAX_WORKER_MESSAGE_BYTES:
                raise WorkerProtocolError(
                    f"worker message exceeds {MAX_WORKER_MESSAGE_BYTES} bytes"
                )
            return decode_worker_message(line.decode("utf-8"))
        except (UnicodeDecodeError, ProtocolError) as exc:
            raise WorkerProtocolError(str(exc)) from exc

    async def _drain_stderr(self) -> None:
        assert self._process is not None and self._process.stderr is not None
        # stderr is an arbitrary byte stream, not the line-delimited JSON protocol.
        # Drain even newline-free output and retain only a bounded diagnostic tail.
        while chunk := await self._process.stderr.read(16 * 1024):
            self._stderr_buffer.extend(chunk)
            del self._stderr_buffer[:-MAX_STDERR_TAIL_BYTES]

    def _signal_process_group(self, sig: signal.Signals) -> None:
        if self._process is None:
            return
        with contextlib.suppress(ProcessLookupError):
            os.killpg(self._process.pid, sig)
