# Lean worker benchmark

Measured on 2026-09-18 with Lean 4.30.0 and Mathlib 4.30.0 after fetching the Mathlib cache.

- CPU: 2 × AMD EPYC 7302, 32 physical cores / 64 logical CPUs
- Memory: 251 GiB
- Worker build: release defaults from `lake build lean-server-worker`
- Command: `PYTHONPATH=src python3 -m tests.lean.benchmark_worker`

One representative run:

| Measurement | Time |
|---|---:|
| Cold start through `ready` | 6753.3 ms |
| First request | 235.5 ms |
| Warm request median (20 requests) | 10.4 ms |
| Warm request minimum (20 requests) | 9.9 ms |

These numbers describe this host and are not performance thresholds. Run the command again on the
deployment machine before choosing worker counts or startup timeouts.
