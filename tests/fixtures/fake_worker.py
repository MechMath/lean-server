from __future__ import annotations

import json
import sys
import time


def emit(value: dict) -> None:
    sys.stdout.write(json.dumps(value, separators=(",", ":")) + "\n")
    sys.stdout.flush()


if len(sys.argv) == 3 and sys.argv[1] == "--startup-delay":
    time.sleep(float(sys.argv[2]))

emit({"protocol_version": 1, "type": "ready", "lean_version": "4.30.0"})

for line in sys.stdin:
    request = json.loads(line)
    request_id = request["request_id"]
    if request.get("code", request.get("content")) == "__NAT_POW_PANIC__":
        sys.stderr.write("INTERNAL PANIC: Nat.pow exponent is too big\n")
        sys.stderr.flush()
        raise SystemExit(1)
    if request.get("protocol_version") == 2 and request.get("type") == "verify":
        content = request["content"]
        if content == "__CRASH__":
            sys.stderr.write("intentional fake worker crash\n")
            sys.stderr.flush()
            raise SystemExit(23)
        if content == "__INVALID_JSON__":
            print("not-json", flush=True)
            continue
        if content.startswith("__SLEEP__:"):
            time.sleep(float(content.split(":", 1)[1]))

        status = "ok"
        warnings = []
        errors = []
        tool_errors = []
        failed_declarations = []
        if content == "__ERROR__":
            status = "compile_error"
            errors = [
                {
                    "severity": "error",
                    "message": "fake candidate error",
                    "file_name": "<content>",
                    "start": {"line": 1, "column": 0},
                    "end": {"line": 1, "column": 1},
                }
            ]
        elif content == "__INTERNAL_ERROR__":
            status = "internal_error"
            errors = [
                {
                    "severity": "error",
                    "message": "fake internal error",
                    "file_name": None,
                    "start": None,
                    "end": None,
                }
            ]
        elif content == "__TOOL_ERROR__":
            tool_errors = ["In 'answer': Axiom 'bad' is not allowed"]
            failed_declarations = ["answer"]
        elif content == "__WARNING__":
            warnings = [
                {
                    "severity": "warning",
                    "message": "fake verification warning",
                    "file_name": "<content>",
                    "start": {"line": 1, "column": 0},
                    "end": {"line": 1, "column": 1},
                }
            ]
        elif content == "__LARGE_RESPONSE__":
            warnings = [
                {
                    "severity": "warning",
                    "message": "x" * (128 * 1024),
                    "file_name": "<content>",
                    "start": None,
                    "end": None,
                }
            ]

        emit(
            {
                "protocol_version": 2,
                "type": "verify_result",
                "request_id": request_id,
                "status": status,
                "compile_ms": 4.0,
                "formal_statement_ms": 1.0,
                "candidate_ms": 2.0,
                "declarations_ms": 1.0,
                "warnings": warnings,
                "errors": errors,
                "tool_errors": tool_errors,
                "failed_declarations": failed_declarations,
            }
        )
        continue

    code = request["code"]
    if code in ("__STDERR_FLOOD__", "__STDERR_FLOOD_CRASH__"):
        sys.stderr.write("x" * (9 * 1024 * 1024) + "\ntail marker\n")
        sys.stderr.flush()
        if code == "__STDERR_FLOOD_CRASH__":
            raise SystemExit(23)
    if code == "__CRASH__":
        sys.stderr.write("intentional fake worker crash\n")
        sys.stderr.flush()
        raise SystemExit(23)
    if code == "__INVALID_JSON__":
        print("not-json", flush=True)
        continue
    if code == "__OVERSIZED_RESPONSE__":
        emit(
            {
                "protocol_version": 1,
                "type": "result",
                "request_id": request_id,
                "status": "compile_error",
                "compile_ms": 1.0,
                "warnings": [],
                "errors": [
                    {
                        "severity": "error",
                        "message": "x" * (9 * 1024 * 1024),
                        "file_name": "<stdin>",
                        "start": None,
                        "end": None,
                    }
                ],
            }
        )
        continue
    if code.startswith("__SLEEP__:"):
        time.sleep(float(code.split(":", 1)[1]))
    if code == "__ERROR__":
        emit(
            {
                "protocol_version": 1,
                "type": "result",
                "request_id": request_id,
                "status": "compile_error",
                "compile_ms": 1.0,
                "warnings": [],
                "errors": [
                    {
                        "severity": "error",
                        "message": "fake compilation error",
                        "file_name": "<stdin>",
                        "start": {"line": 1, "column": 0},
                        "end": {"line": 1, "column": 1},
                    }
                ],
            }
        )
        continue
    if code == "__INTERNAL_ERROR__":
        emit(
            {
                "protocol_version": 1,
                "type": "result",
                "request_id": request_id,
                "status": "internal_error",
                "compile_ms": 1.0,
                "warnings": [],
                "errors": [
                    {
                        "severity": "error",
                        "message": "fake internal error",
                        "file_name": None,
                        "start": None,
                        "end": None,
                    }
                ],
            }
        )
        continue
    if code == "__WARNING__":
        emit(
            {
                "protocol_version": 1,
                "type": "result",
                "request_id": request_id,
                "status": "ok",
                "compile_ms": 1.0,
                "warnings": [
                    {
                        "severity": "warning",
                        "message": "fake warning",
                        "file_name": "<stdin>",
                        "start": {"line": 1, "column": 0},
                        "end": {"line": 1, "column": 1},
                    }
                ],
                "errors": [],
            }
        )
        continue
    if code == "__SORRY__":
        emit(
            {
                "protocol_version": 1,
                "type": "result",
                "request_id": request_id,
                "status": "ok",
                "compile_ms": 1.0,
                "warnings": [
                    {
                        "severity": "warning",
                        "message": "declaration uses `sorry`",
                        "file_name": "<stdin>",
                        "start": {"line": 1, "column": 8},
                        "end": {"line": 1, "column": 18},
                    }
                ],
                "errors": [],
            }
        )
        continue
    emit(
        {
            "protocol_version": 1,
            "type": "result",
            "request_id": request_id,
            "status": "ok",
            "compile_ms": 1.0,
            "warnings": [],
            "errors": [],
        }
    )
