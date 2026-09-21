from __future__ import annotations

import argparse
import shlex

from .http import create_server
from .preflight import StartupCheckError, run_startup_checks


def main() -> None:
    parser = argparse.ArgumentParser(description="Lean 4.30 HTTP compilation server")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=8000, type=int)
    parser.add_argument("--workers", default=2, type=int)
    parser.add_argument("--queue-capacity", default=8, type=int)
    parser.add_argument(
        "--worker-command",
        help=(
            "NDJSON worker command; defaults to "
            "'lake env .lake/build/bin/lean-server-worker'"
        ),
    )
    parser.add_argument(
        "--worker-startup-timeout",
        default=180.0,
        type=float,
        help=(
            "seconds to wait for each persistent worker ready handshake "
            "(default: 180)"
        ),
    )
    parser.add_argument(
        "--worker-startup-parallelism",
        default=8,
        type=int,
        help="maximum workers started concurrently (default: 8)",
    )
    args = parser.parse_args()

    worker_command = shlex.split(args.worker_command) if args.worker_command else None
    try:
        report = run_startup_checks()
    except StartupCheckError as exc:
        parser.exit(1, f"Lean server startup check failed: {exc}\n")
    print(
        "Startup checks passed: "
        f"Lean {report.lean_version}, Mathlib {report.mathlib_revision[:12]}, "
        f"{report.elapsed_ms:.0f}ms",
        flush=True,
    )
    server = create_server(
        args.host,
        args.port,
        worker_count=args.workers,
        queue_capacity=args.queue_capacity,
        worker_command=worker_command,
        worker_startup_timeout_seconds=args.worker_startup_timeout,
        worker_startup_parallelism=args.worker_startup_parallelism,
    )
    print(f"Lean server listening on http://{args.host}:{args.port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
