# Worker resource-boundary follow-up — 2026-09-21

Related issue: [#8](https://github.com/MechMath/lean-server/issues/8).
These are local staging results, not a production rollout.

## Changes

- Worker response size is measured in UTF-8 bytes including the trailing newline.
  Exactly 8 MiB is accepted. Larger responses return HTTP 503,
  `error_type: "WorkerMessageTooLarge"`, `retryable: false`, without a proof verdict.
- The reader discards oversized messages through their newline and reuses the
  worker. Draining has a 2-second and additional 64 MiB limit, within the original
  request deadline. Unterminated, stalled or excessive output requires replacement.
- Long verification has dedicated worker slots and independently bounded queue
  capacity. Normal checks and short-budget verification retain their own capacity.
  Input serialization and panic quarantine remain shared across both lanes.
- Verification default and maximum budgets are configurable; defaults remain
  600 seconds. The documented slow-proof configuration uses 1800 seconds, two
  long workers and two normal workers. This adds deadline margin; it does not
  optimize Lean tactics or change proof acceptance.

## Automated validation

`lake build lean-server-worker` passed. The full suite passed **132 tests in
107.489 seconds**, with Python 3.12.13 and the real Lean 4.30.0 worker:

```bash
PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -v
```

The HTTP tests require local socket access outside the sandbox. Coverage includes:

- Actual subprocess frames one byte below, exactly at and one byte above 8 MiB,
  and at 24 MiB, for both compile and verification messages; following requests
  use the same PID after fully drained responses.
- EOF without a newline, stalled output, excessive output, typed error mapping,
  replacement when draining fails, and healthy follow-up requests.
- Long-queue saturation while normal compilation and short verification proceed;
  queued cancellation, queue/execution timeouts, recovery, shutdown and panic
  quarantine across lanes.
- HTTP deployment timeout overrides, maximum enforcement, preserved compilation
  limits and lane-specific overload responses.

## Slow-proof profile under concurrent load

Both exact historical candidates passed strict verification with the documented
1800-second configuration. The full regression suite also ran during this replay.

| UUID | Total seconds | Statement ms | Candidate ms | Declaration checks ms |
| --- | ---: | ---: | ---: | ---: |
| `Goedel-Pset-1539351` | 696.249 | 257.647 | 695978.500 | 7.082 |
| `Goedel-Pset-101720` | 858.094 | 267.169 | 857816.143 | 2.526 |

Candidate compilation accounts for over 99.9% of each request's time, so increasing
the deployment budget and isolating long requests directly addresses these cases
without changing their proofs or weakening verification. Both exceeded the old
600-second maximum on this machine; timings should not be compared as a speed
regression against the earlier remote-machine replay.

All **426** ordinary compilation probes passed, including **345** while both long
workers were busy. Median latency was **16.6 ms**, P95 **17.9 ms**, and maximum
**247.7 ms**. There were **zero worker replacements**. Machine-readable results,
configuration and candidate hashes are in
[`data/resource-boundaries-summary.json`](data/resource-boundaries-summary.json).
This validates the two cases under this workload, not an unconditional guarantee
under arbitrary machine load or unbounded client concurrency.

## Reproduce the slow-proof profile

The exact statement and candidate texts for `Goedel-Pset-1539351` and
`Goedel-Pset-101720` are retained in
[`slow-verification.json`](../../../tests/fixtures/slow-verification.json).
They were extracted unchanged from the archive in commit `9a955b2`.

```bash
PYTHONPATH=src .venv/bin/python tests/lean/benchmark_long_jobs.py \
  --output /tmp/long-jobs-report.json
```

The benchmark starts a temporary local HTTP server with two workers per lane,
submits both historical verifications concurrently with a default 1800-second
budget, and probes ordinary compilation every two seconds. It records verifier
phase timings, normal-request latency, worker replacements and candidate hashes.
Clients submitting an explicit timeout must raise it themselves; a server default
does not override a client request. See [deployment details](../../worker-pool.md#长验证隔离与部署预算).

## Remaining issue scope

The original `Nat.pow` panic is mitigated by bounded input quarantine, but its
minimal reproducer and comparison with plain `lake env lean` remain open. This
follow-up does not claim to fix upstream Lean or provide OS-level CPU/memory
isolation. Queue isolation protects worker slots and queue capacity; deployments
still need sufficient physical resources and bounded client concurrency.
