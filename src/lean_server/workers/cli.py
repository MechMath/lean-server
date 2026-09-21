from __future__ import annotations

import asyncio
import contextlib
import json
import os
import signal
import time
from pathlib import Path

from lean_server.compiler import LEAN_TOOLCHAIN, PROJECT_ROOT
from lean_server.protocol import (
    Position,
    VerifyWorkerRequest,
    WorkerDiagnostic,
    WorkerJobRequest,
    WorkerJobResult,
    WorkerResult,
)


class LeanCliBackend:
    """Transitional backend that starts one Lean CLI process per request."""

    def __init__(self, *, cwd: Path = PROJECT_ROOT) -> None:
        self.cwd = cwd
        self._started = False
        self._process: asyncio.subprocess.Process | None = None

    async def start(self) -> None:
        self._started = True

    async def compile(self, request: WorkerJobRequest) -> WorkerJobResult:
        if not self._started:
            raise RuntimeError("CLI backend has not been started")
        if isinstance(request, VerifyWorkerRequest):
            raise RuntimeError("strict verification requires the persistent Lean worker")
        started = time.perf_counter()
        self._process = await asyncio.create_subprocess_exec(
            "elan",
            "run",
            LEAN_TOOLCHAIN,
            "lake",
            "env",
            "lean",
            "--json",
            "--stdin",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=self.cwd,
            start_new_session=True,
        )
        try:
            stdout, stderr = await self._process.communicate(request.code.encode("utf-8"))
        except asyncio.CancelledError:
            await self._kill_process()
            raise

        compile_ms = (time.perf_counter() - started) * 1000
        returncode = self._process.returncode
        self._process = None
        warnings, errors, malformed = _parse_lean_output(stdout.decode("utf-8"))
        stderr_text = stderr.decode("utf-8", errors="replace").strip()
        if malformed:
            errors.append(WorkerDiagnostic("error", malformed))
        if stderr_text:
            errors.append(WorkerDiagnostic("error", stderr_text))
        if malformed or stderr_text or (returncode != 0 and not errors):
            if returncode != 0 and not errors:
                errors.append(WorkerDiagnostic("error", f"Lean exited with status {returncode}"))
            status = "internal_error"
        elif errors:
            status = "compile_error"
        else:
            status = "ok"
        return WorkerResult(
            request_id=request.request_id,
            status=status,
            compile_ms=compile_ms,
            warnings=tuple(warnings),
            errors=tuple(errors),
        )

    async def close(self) -> None:
        self._started = False
        await self._kill_process()

    async def _kill_process(self) -> None:
        process = self._process
        if process is None or process.returncode is not None:
            self._process = None
            return
        with contextlib.suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
        await process.wait()
        self._process = None


def _parse_lean_output(
    output: str,
) -> tuple[list[WorkerDiagnostic], list[WorkerDiagnostic], str | None]:
    warnings: list[WorkerDiagnostic] = []
    errors: list[WorkerDiagnostic] = []
    for line in output.splitlines():
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            return warnings, errors, f"non-JSON Lean output: {line}"
        if not isinstance(value, dict):
            return warnings, errors, "Lean diagnostic must be a JSON object"
        severity = value.get("severity")
        if severity not in ("warning", "error"):
            continue
        diagnostic = WorkerDiagnostic(
            severity=severity,
            message=str(value.get("data", "")),
            file_name=value.get("fileName"),
            start=_position(value.get("pos")),
            end=_position(value.get("endPos")),
        )
        if severity == "warning":
            warnings.append(diagnostic)
        else:
            errors.append(diagnostic)
    return warnings, errors, None


def _position(value: object) -> Position | None:
    if not isinstance(value, dict):
        return None
    line = value.get("line")
    column = value.get("column")
    if not isinstance(line, int) or not isinstance(column, int):
        return None
    return Position(line=line, column=column)
