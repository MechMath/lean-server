from __future__ import annotations

import json
import os
import signal
import subprocess
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


LEAN_TOOLCHAIN = "leanprover/lean4:v4.30.0"
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_TIMEOUT_SECONDS = 30.0
MAX_TIMEOUT_SECONDS = 120.0


@dataclass(frozen=True, slots=True)
class Diagnostic:
    severity: str
    message: str
    file_name: str | None = None
    start: dict[str, int] | None = None
    end: dict[str, int] | None = None

    @classmethod
    def from_lean(cls, value: dict[str, Any]) -> "Diagnostic":
        return cls(
            severity=str(value.get("severity", "info")),
            message=str(value.get("data", "")),
            file_name=value.get("fileName"),
            start=value.get("pos"),
            end=value.get("endPos"),
        )


@dataclass(frozen=True, slots=True)
class CompileResult:
    okay: bool
    timed_out: bool
    time_ms: float
    lean_version: str
    warnings: list[Diagnostic]
    errors: list[Diagnostic]

    def to_dict(self) -> dict[str, Any]:
        return {
            "okay": self.okay,
            "timed_out": self.timed_out,
            "time_ms": self.time_ms,
            "lean_version": self.lean_version,
            "warnings": [asdict(item) for item in self.warnings],
            "errors": [asdict(item) for item in self.errors],
        }


def _diagnostics(output: str) -> tuple[list[Diagnostic], list[Diagnostic]]:
    warnings: list[Diagnostic] = []
    errors: list[Diagnostic] = []
    for line in output.splitlines():
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            errors.append(Diagnostic(severity="error", message=line))
            continue
        if not isinstance(value, dict):
            continue
        diagnostic = Diagnostic.from_lean(value)
        if diagnostic.severity == "warning":
            warnings.append(diagnostic)
        elif diagnostic.severity == "error":
            errors.append(diagnostic)
    return warnings, errors


def _stop_process(process: subprocess.Popen[str]) -> None:
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    process.wait()


def compile_lean(code: str, timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS) -> CompileResult:
    if not 0 < timeout_seconds <= MAX_TIMEOUT_SECONDS:
        raise ValueError(f"timeout_seconds must be between 0 and {MAX_TIMEOUT_SECONDS:g}")

    started = time.perf_counter()
    try:
        process = subprocess.Popen(
            ["elan", "run", LEAN_TOOLCHAIN, "lake", "env", "lean", "--json", "--stdin"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
            cwd=PROJECT_ROOT,
        )
    except OSError as exc:
        elapsed = (time.perf_counter() - started) * 1000
        error = Diagnostic(severity="error", message=f"Unable to start Lean: {exc}")
        return CompileResult(False, False, elapsed, "4.30.0", [], [error])

    try:
        stdout, stderr = process.communicate(code, timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        _stop_process(process)
        elapsed = (time.perf_counter() - started) * 1000
        error = Diagnostic(
            severity="error",
            message=f"Lean compilation exceeded {timeout_seconds:g} seconds",
        )
        return CompileResult(False, True, elapsed, "4.30.0", [], [error])

    elapsed = (time.perf_counter() - started) * 1000
    warnings, errors = _diagnostics(stdout)
    if stderr.strip():
        errors.append(Diagnostic(severity="error", message=stderr.strip()))
    if process.returncode != 0 and not errors:
        errors.append(
            Diagnostic(severity="error", message=f"Lean exited with status {process.returncode}")
        )
    return CompileResult(
        okay=process.returncode == 0 and not errors,
        timed_out=False,
        time_ms=elapsed,
        lean_version="4.30.0",
        warnings=warnings,
        errors=errors,
    )
