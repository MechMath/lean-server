from __future__ import annotations

import argparse

from .http import create_server


def main() -> None:
    parser = argparse.ArgumentParser(description="Lean 4.30 HTTP compilation server")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=8000, type=int)
    args = parser.parse_args()

    server = create_server(args.host, args.port)
    print(f"Lean server listening on http://{args.host}:{args.port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
