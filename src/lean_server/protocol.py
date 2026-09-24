from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass
from typing import Any, Literal


PROTOCOL_VERSION = 2
WorkerStatus = Literal["ok", "compile_error", "internal_error"]


class ProtocolError(ValueError):
    """The worker emitted a message that does not satisfy a supported protocol."""


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
class VerifyWorkerRequest:
    request_id: str
    formal_statement: str
    content: str
    use_def_eq: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "protocol_version": PROTOCOL_VERSION,
            "type": "verify",
            "request_id": self.request_id,
            "formal_statement": self.formal_statement,
            "content": self.content,
            "use_def_eq": self.use_def_eq,
        }

    def to_json_line(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, separators=(",", ":")) + "\n"


@dataclass(frozen=True, slots=True)
class WorkerReady:
    lean_version: str


@dataclass(frozen=True, slots=True)
class ElaborationTimings:
    header_ms: float
    elaboration_ms: float
    diagnostics_ms: float
    profiling_ms: float

    @classmethod
    def from_dict(cls, value: object) -> ElaborationTimings:
        if not isinstance(value, dict):
            raise ProtocolError("elaboration timings must be an object")
        return cls(**{field: _required_number(value, field) for field in cls.__dataclass_fields__})


@dataclass(frozen=True, slots=True)
class ComparisonError:
    declaration: str
    phase: Literal["type", "value"]
    kind: Literal["resource_limit", "interrupted", "internal_error"]
    message: str

    @classmethod
    def from_dict(cls, value: object) -> ComparisonError:
        if not isinstance(value, dict):
            raise ProtocolError("comparison error must be an object")
        phase = value.get("phase")
        kind = value.get("kind")
        if phase not in ("type", "value"):
            raise ProtocolError("invalid comparison error phase")
        if kind not in ("resource_limit", "interrupted", "internal_error"):
            raise ProtocolError("invalid comparison error kind")
        return cls(
            declaration=_required_string(value, "declaration", nonempty=True),
            phase=phase,
            kind=kind,
            message=_required_string(value, "message"),
        )


@dataclass(frozen=True, slots=True)
class WorkerResult:
    request_id: str
    status: WorkerStatus
    compile_ms: float
    warnings: tuple[WorkerDiagnostic, ...]
    errors: tuple[WorkerDiagnostic, ...]
    timings: ElaborationTimings | None = None


@dataclass(frozen=True, slots=True)
class VerifyWorkerResult:
    request_id: str
    status: WorkerStatus
    compile_ms: float
    formal_statement_ms: float
    candidate_ms: float
    declarations_ms: float
    warnings: tuple[WorkerDiagnostic, ...]
    errors: tuple[WorkerDiagnostic, ...]
    tool_errors: tuple[str, ...]
    failed_declarations: tuple[str, ...]
    formal_timings: ElaborationTimings | None = None
    candidate_timings: ElaborationTimings | None = None
    comparison_errors: tuple[ComparisonError, ...] = ()


WorkerJobRequest = WorkerRequest | VerifyWorkerRequest
WorkerJobResult = WorkerResult | VerifyWorkerResult
WorkerMessage = WorkerReady | WorkerJobResult


def _required_string(value: dict[str, Any], field: str, *, nonempty: bool = False) -> str:
    item = value.get(field)
    if not isinstance(item, str) or (nonempty and not item):
        qualifier = "non-empty " if nonempty else ""
        raise ProtocolError(f"worker message requires {qualifier}string {field}")
    return item


def _required_number(value: dict[str, Any], field: str) -> float:
    item = value.get(field)
    if isinstance(item, bool) or not isinstance(item, (int, float)):
        raise ProtocolError(f"worker message requires numeric {field}")
    number = float(item)
    if not math.isfinite(number) or number < 0:
        raise ProtocolError(f"worker message requires finite non-negative {field}")
    return number


def _worker_status(value: dict[str, Any]) -> WorkerStatus:
    status = value.get("status")
    if status not in ("ok", "compile_error", "internal_error"):
        raise ProtocolError("invalid worker result status")
    return status


def _diagnostics(value: dict[str, Any], field: str) -> tuple[WorkerDiagnostic, ...]:
    items = value.get(field)
    if not isinstance(items, list):
        raise ProtocolError(f"worker result {field} must be an array")
    return tuple(WorkerDiagnostic.from_dict(item) for item in items)


def _string_array(value: dict[str, Any], field: str) -> tuple[str, ...]:
    items = value.get(field)
    if not isinstance(items, list) or not all(isinstance(item, str) for item in items):
        raise ProtocolError(f"worker result {field} must be an array of strings")
    return tuple(items)


def _comparison_errors(value: dict[str, Any]) -> tuple[ComparisonError, ...]:
    items = value.get("comparison_errors", [])
    if not isinstance(items, list):
        raise ProtocolError("comparison_errors must be an array")
    return tuple(ComparisonError.from_dict(item) for item in items)


def decode_worker_message(line: str) -> WorkerMessage:
    try:
        value = json.loads(line)
    except json.JSONDecodeError as exc:
        raise ProtocolError("worker message is not valid JSON") from exc
    if not isinstance(value, dict):
        raise ProtocolError("worker message must be an object")
    version = value.get("protocol_version")
    if type(version) is not int or version != PROTOCOL_VERSION:
        raise ProtocolError(f"unsupported worker protocol_version: {version!r}")
    message_type = value.get("type")
    if message_type == "ready":
        lean_version = value.get("lean_version")
        if not isinstance(lean_version, str):
            raise ProtocolError("ready message requires lean_version")
        return WorkerReady(lean_version=lean_version)
    if message_type == "result":
        return WorkerResult(
            request_id=_required_string(value, "request_id", nonempty=True),
            status=_worker_status(value),
            compile_ms=_required_number(value, "compile_ms"),
            warnings=_diagnostics(value, "warnings"),
            errors=_diagnostics(value, "errors"),
            timings=ElaborationTimings.from_dict(value["timings"]) if "timings" in value else None,
        )
    if message_type == "verify_result":
        formal_timings = candidate_timings = None
        if "timings" in value:
            timings = value["timings"]
            if not isinstance(timings, dict):
                raise ProtocolError("verification timings must be an object")
            formal_timings = ElaborationTimings.from_dict(timings.get("formal_statement"))
            candidate_timings = ElaborationTimings.from_dict(timings.get("candidate"))
        return VerifyWorkerResult(
            request_id=_required_string(value, "request_id", nonempty=True),
            status=_worker_status(value),
            compile_ms=_required_number(value, "compile_ms"),
            formal_statement_ms=_required_number(value, "formal_statement_ms"),
            candidate_ms=_required_number(value, "candidate_ms"),
            declarations_ms=_required_number(value, "declarations_ms"),
            warnings=_diagnostics(value, "warnings"),
            errors=_diagnostics(value, "errors"),
            tool_errors=_string_array(value, "tool_errors"),
            failed_declarations=_string_array(value, "failed_declarations"),
            formal_timings=formal_timings,
            candidate_timings=candidate_timings,
            comparison_errors=_comparison_errors(value),
        )
    raise ProtocolError(f"unsupported worker message type: {message_type!r}")
