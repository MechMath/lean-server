from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import Any, Literal


PROTOCOL_VERSION = 1
WorkerStatus = Literal["ok", "compile_error", "internal_error"]


class ProtocolError(ValueError):
    """The worker emitted a message that does not satisfy protocol v1."""


@dataclass(frozen=True, slots=True)
class Position:
    line: int
    column: int

    @classmethod
    def from_dict(cls, value: object) -> Position | None:
        if value is None:
            return None
        if not isinstance(value, dict):
            raise ProtocolError("diagnostic position must be an object or null")
        line = value.get("line")
        column = value.get("column")
        if not isinstance(line, int) or not isinstance(column, int):
            raise ProtocolError("diagnostic position requires integer line and column")
        return cls(line=line, column=column)


@dataclass(frozen=True, slots=True)
class WorkerDiagnostic:
    severity: Literal["warning", "error"]
    message: str
    file_name: str | None = None
    start: Position | None = None
    end: Position | None = None

    @classmethod
    def from_dict(cls, value: object) -> WorkerDiagnostic:
        if not isinstance(value, dict):
            raise ProtocolError("diagnostic must be an object")
        severity = value.get("severity")
        message = value.get("message")
        file_name = value.get("file_name")
        if severity not in ("warning", "error"):
            raise ProtocolError("diagnostic severity must be warning or error")
        if not isinstance(message, str):
            raise ProtocolError("diagnostic message must be a string")
        if file_name is not None and not isinstance(file_name, str):
            raise ProtocolError("diagnostic file_name must be a string or null")
        return cls(
            severity=severity,
            message=message,
            file_name=file_name,
            start=Position.from_dict(value.get("start")),
            end=Position.from_dict(value.get("end")),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class WorkerRequest:
    request_id: str
    code: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "protocol_version": PROTOCOL_VERSION,
            "type": "compile",
            "request_id": self.request_id,
            "code": self.code,
        }

    def to_json_line(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, separators=(",", ":")) + "\n"


@dataclass(frozen=True, slots=True)
class WorkerReady:
    lean_version: str


@dataclass(frozen=True, slots=True)
class WorkerResult:
    request_id: str
    status: WorkerStatus
    compile_ms: float
    warnings: tuple[WorkerDiagnostic, ...]
    errors: tuple[WorkerDiagnostic, ...]


WorkerMessage = WorkerReady | WorkerResult


def decode_worker_message(line: str) -> WorkerMessage:
    try:
        value = json.loads(line)
    except json.JSONDecodeError as exc:
        raise ProtocolError("worker message is not valid JSON") from exc
    if not isinstance(value, dict):
        raise ProtocolError("worker message must be an object")
    if value.get("protocol_version") != PROTOCOL_VERSION:
        raise ProtocolError(f"worker protocol_version must be {PROTOCOL_VERSION}")

    message_type = value.get("type")
    if message_type == "ready":
        lean_version = value.get("lean_version")
        if not isinstance(lean_version, str):
            raise ProtocolError("ready message requires lean_version")
        return WorkerReady(lean_version=lean_version)
    if message_type != "result":
        raise ProtocolError("worker message type must be ready or result")

    request_id = value.get("request_id")
    status = value.get("status")
    compile_ms = value.get("compile_ms")
    warnings = value.get("warnings")
    errors = value.get("errors")
    if not isinstance(request_id, str) or not request_id:
        raise ProtocolError("result message requires a non-empty request_id")
    if status not in ("ok", "compile_error", "internal_error"):
        raise ProtocolError("invalid worker result status")
    if isinstance(compile_ms, bool) or not isinstance(compile_ms, (int, float)):
        raise ProtocolError("result message requires numeric compile_ms")
    if not isinstance(warnings, list) or not isinstance(errors, list):
        raise ProtocolError("result warnings and errors must be arrays")
    return WorkerResult(
        request_id=request_id,
        status=status,
        compile_ms=float(compile_ms),
        warnings=tuple(WorkerDiagnostic.from_dict(item) for item in warnings),
        errors=tuple(WorkerDiagnostic.from_dict(item) for item in errors),
    )
