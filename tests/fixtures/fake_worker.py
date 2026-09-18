from __future__ import annotations

import json
import sys
import time


def emit(value: dict) -> None:
    sys.stdout.write(json.dumps(value, separators=(",", ":")) + "\n")
    sys.stdout.flush()


emit({"protocol_version": 1, "type": "ready", "lean_version": "4.30.0"})

for line in sys.stdin:
    request = json.loads(line)
    request_id = request["request_id"]
    code = request["code"]
    if code == "__CRASH__":
        sys.stderr.write("intentional fake worker crash\n")
        sys.stderr.flush()
        raise SystemExit(23)
    if code == "__INVALID_JSON__":
        print("not-json", flush=True)
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
