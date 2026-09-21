"""Replay the two archived slow verifications while probing the normal HTTP lane.

Run manually with PYTHONPATH=src python tests/lean/benchmark_long_jobs.py
--output /path/to/report.json. The optional cases file is a
JSON array of archived records containing uuid, formal_statement and candidate.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
from http.client import HTTPConnection
import json
from pathlib import Path
import platform
import statistics
import threading
import time

from lean_server.http import create_server


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path,
                        default=Path(__file__).parents[1] / "fixtures" / "slow-verification.json")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--timeout", type=float, default=1800)
    args = parser.parse_args()
    cases = json.loads(args.cases.read_text())
    assert {row["uuid"] for row in cases} == {"Goedel-Pset-1539351", "Goedel-Pset-101720"}
    server = create_server(
        "127.0.0.1", 0, worker_count=2, queue_capacity=8,
        long_worker_count=2, long_queue_capacity=2,
        verify_default_timeout_seconds=args.timeout, verify_max_timeout_seconds=args.timeout,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def request(path, payload):
        began = time.monotonic()
        connection = HTTPConnection("127.0.0.1", server.server_address[1], timeout=args.timeout + 15)
        try:
            connection.request("POST", path, json.dumps(payload), {"Content-Type": "application/json"})
            response = connection.getresponse()
            body = json.loads(response.read())
            return {"http_status": response.status, "elapsed_seconds": time.monotonic() - began,
                    "body": body}
        finally:
            connection.close()

    def verify(row):
        result = request("/verify_proof", {
            "environment": "lean-4.30.0", "formal_statement": row["formal_statement"],
            "content": row["candidate"],
        })
        body = result.pop("body")
        result.update(uuid=row["uuid"], okay=body.get("okay"), timings=body.get("timings"),
                      error_type=body.get("error_type"), error=body.get("error"),
                      candidate_sha256=hashlib.sha256(row["candidate"].encode()).hexdigest())
        return result

    probes = []
    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(verify, row) for row in cases]
            while not all(future.done() for future in futures):
                active = server.runtime.snapshot().long_active_workers
                probe = request("/check", {"code": "theorem healthy : True := by trivial",
                                          "timeout_seconds": 30})
                probes.append({"long_active_workers": active, "okay": probe["body"].get("okay"),
                               "elapsed_seconds": probe["elapsed_seconds"]})
                time.sleep(2)
            results = [future.result() for future in futures]
        latencies = sorted(probe["elapsed_seconds"] for probe in probes)
        snapshot = server.runtime.snapshot()
        report = {
            "platform": platform.platform(), "python": platform.python_version(),
            "configuration": {"normal_workers": 2, "long_workers": 2, "long_queue_capacity": 2,
                              "verify_default_timeout_seconds": args.timeout,
                              "verify_max_timeout_seconds": args.timeout,
                              "long_request_threshold_seconds": 120},
            "results": results,
            "normal_probes": {"count": len(probes), "all_passed": all(p["okay"] for p in probes),
                              "while_both_long_workers_busy": sum(p["long_active_workers"] == 2 for p in probes),
                              "median_seconds": statistics.median(latencies),
                              "p95_seconds": latencies[min(len(latencies) - 1, int(len(latencies) * .95))],
                              "max_seconds": max(latencies)},
            "replacements": snapshot.replacements,
        }
        args.output.write_text(json.dumps(report, indent=2) + "\n")
        print(json.dumps(report, indent=2), flush=True)
        assert all(row["http_status"] == 200 and row["okay"] for row in results), results
        assert all(probe["okay"] for probe in probes), probes
        assert any(probe["long_active_workers"] == 2 for probe in probes), probes
        assert snapshot.replacements == 0, snapshot
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


if __name__ == "__main__":
    main()
