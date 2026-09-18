from __future__ import annotations

import argparse
import shlex

from .http import create_server


def main() -> None:
    parser = argparse.ArgumentParser(description="Lean 4.30 HTTP compilation server")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=8000, type=int)
    parser.add_argument("--workers", default=2, type=int)
    parser.add_argument("--queue-capacity", default=8, type=int)
    parser.add_argument(
        "--worker-command",
        help="NDJSON worker command; defaults to one Lean CLI process per request",
    )
    args = parser.parse_args()

    worker_command = shlex.split(args.worker_command) if args.worker_command else None
    server = create_server(
        args.host,
        args.port,
        worker_count=args.workers,
        queue_capacity=args.queue_capacity,
        worker_command=worker_command,
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
