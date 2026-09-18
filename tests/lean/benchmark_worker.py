from __future__ import annotations

import statistics
import subprocess

from tests.lean.worker_harness import PROJECT_ROOT, Worker


def main() -> None:
    subprocess.run(["lake", "build", "lean-server-worker"], cwd=PROJECT_ROOT, check=True)
    with Worker() as worker:
        first = worker.compile("first", "import Mathlib\nexample : 1 + 1 = 2 := by norm_num")
        warm = [
            worker.compile(f"warm-{index}", "example : 2 + 2 = 4 := by norm_num").compile_ms
            for index in range(20)
        ]
        print(f"cold_start_ms={worker.ready_ms:.1f}")
        print(f"first_request_ms={first.compile_ms:.1f}")
        print(f"warm_request_median_ms={statistics.median(warm):.1f}")
        print(f"warm_request_min_ms={min(warm):.1f}")


if __name__ == "__main__":
    main()
