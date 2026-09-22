# Worker pool and HTTP scheduling

## Current implementation

HTTP handlers submit work through:

```text
ThreadingHTTPServer
  -> CompilerPoolRuntime (dedicated asyncio event loop)
  -> CompilerPool (bounded normal and long-verification queues)
  -> CompilerBackend
      `- WorkerProcessBackend: persistent NDJSON worker by default
```

Defaults: 2 normal slots, 1 extra long-verification slot and 8 queued requests per
lane. Each slot runs one request. A full queue returns HTTP 503 immediately:

```json
{"error":"compiler queue is full","retryable":true}
```

## Startup

Before binding the HTTP port, startup checks require:

- `lean-toolchain` pinned to `leanprover/lean4:v4.30.0`.
- Both the pinned toolchain and `lake env lean` reporting 4.30.0.
- Mathlib v4.30.0 pinned in `lakefile.toml` and `lake-manifest.json`.
- `.lake/packages/mathlib` at the manifest's Git revision.
- A successful `import Mathlib` smoke compile.

Failure exits with status 1 without serving HTTP. Success prints the Mathlib
revision and elapsed time.

Start the built worker:

```bash
uv run lean-server --workers 4 --queue-capacity 16
```

Or override its command:

```bash
uv run lean-server \
  --workers 4 \
  --queue-capacity 16 \
  --worker-startup-timeout 180 \
  --worker-startup-parallelism 8 \
  --worker-command '.lake/build/bin/lean-server-worker'
```

`--worker-command` uses shell-style parsing but launches without a shell. Mathlib
preloading gets a 180-second ready-handshake budget per worker. At most 8 workers
start concurrently to balance disk contention and startup time. Both limits are
configurable with the flags above.

## Long verification and deployment budgets

`--workers` and `--queue-capacity` control normal capacity. `--long-workers`
(default 1) and `--long-queue-capacity` (default 8) add separate long-job capacity.
Neither lane borrows slots or queue space; long-worker replacement also stays in
its lane. Both share input fingerprints and panic quarantine, so changing budgets
cannot bypass quarantine.

Verification enters the long queue when its **original total budget** exceeds
`--long-request-threshold` (default 120 seconds). All compilation and shorter
verification use the normal queue. Routing does not predict duration or migrate
running jobs. Use explicit budgets of at most 120 seconds for short verification;
default-budget verification uses the long queue. A short duplicate waits for its
identical long-running input; unrelated normal requests can pass it.

`--verify-default-timeout` and `--verify-max-timeout` default to 600 seconds and
must be finite with `0 < default <= maximum`. Compilation retains its separate
120-second cap. Two historical slow proofs took about 419 and 597 seconds; use:

```bash
uv run lean-server --workers 2 --queue-capacity 8 \
  --long-workers 2 --long-queue-capacity 2 \
  --verify-default-timeout 1800 --verify-max-timeout 1800
```

This starts four Mathlib-loaded processes. Reserve enough CPU and memory: queue
isolation is not an OS resource quota. Long budgets include queueing; keep batch
concurrency near the long-worker count. Client and proxy timeouts must exceed the
request budget; allow at least 15 extra seconds on clients. An explicit 600-second
request remains 600 seconds despite a larger server default.

Embedded `CompilerPool` adds no long workers by default; `create_runtime`,
`create_server` and the CLI add one. `--long-workers 0` restores a shared queue
without long-job isolation.

Health/readiness reports `long_worker_count`, `long_active_workers`,
`long_queue_depth` and `long_queue_capacity`. `worker_count`, `ready_workers` and
`active_workers` include both lanes; `queue_depth` and `queue_capacity` describe
only the normal queue.

## Lifecycle and failures

- Warm all backends with bounded startup concurrency before serving HTTP.
- Replace workers after timeout, exit, EOF, invalid protocol, wrong request ID or `internal_error`.
- Parser, elaborator and type errors return `compile_error` without replacement.
- Oversized responses return non-retryable `WorkerMessageTooLarge`. Reuse the worker
  after bounded draining; replace it if draining fails. See [byte limits](../protocol/README.md#response-size-limits).
- Failed replacements back off exponentially from 50 ms to 2 seconds.
- Shutdown cancels active/queued work and closes backends and process groups.
- The pool also owns starting replacements; cancelling a handshake terminates and reaps them.
- Drain stderr continuously in chunks, retaining the last 64 KiB and exposing at
  most 100 lines. Long lines do not stop draining. Log cleanup errors and continue recovery.

`timeout_seconds` covers queueing and execution from pool entry, including waits
for replacement. Queued expiry or cancellation frees the slot immediately without
execution. Running tasks receive only the remaining budget; timeout replaces their
worker. Restarting slots are not counted as `active_workers`.

Inspect pool state through `GET /healthz` and `GET /readyz`:

```json
{
  "state": "running",
  "worker_count": 4,
  "ready_workers": 4,
  "active_workers": 1,
  "queue_depth": 2,
  "queue_capacity": 16,
  "replacements": 0,
  "quarantined_inputs": 0,
  "quarantine_hits": 0,
  "quarantine_evictions": 0
}
```

## Panic quarantine

The known `INTERNAL PANIC: Nat.pow exponent is too big` returns HTTP 503,
`error_type: "LeanPanic"`, `retryable: false`. Verification did not finish; this is
not an `okay: false` verdict. The first crash replaces the worker and records the
input's SHA-256 fingerprint. Later identical requests are rejected before queueing;
already queued duplicates receive the same error:

```json
{
  "error": "worker previously panicked for this input; input is quarantined",
  "retryable": false,
  "error_type": "LeanPanic"
}
```

Fingerprints include protocol version, operation and every worker input: code, or
statement/content/`use_def_eq`. They omit request IDs, HTTP timeout and `allow_sorry`,
which do not change worker computation. Store only hashes and error types, not
proof text. Only explicitly input-related panics enter quarantine; ordinary errors,
timeouts, protocol faults and other exits do not mark input bad.

Only one worker runs a fingerprint at a time. Duplicates stay in the bounded queue
with their own deadlines and cancellation. Dispatch picks the earliest runnable
request, skipping blocked duplicates. Successful duplicates run separately with
their own request IDs; results are not shared.

Quarantine is local to one pool/process with a fixed Lean/Mathlib/worker environment.
Restart after upgrades. The default capacity is 4096, configurable through
`CompilerPool(quarantine_capacity=...)`. Entries use LRU eviction, have no expiry,
clear on restart and are not shared across replicas. Protection lasts only while
the exact input remains recorded; edits, eviction, restart or another replica may
allow execution again. Health/readiness exposes `quarantine_hits` and
`quarantine_evictions`.

Healthy workers remain usable during replacement. With one worker, normal requests
wait within their total budget. Quarantined requests neither wait for replacement
nor occupy a worker.

## HTTP timings

`POST /check` retains `time_ms` and adds:

```json
{
  "timings": {
    "total_ms": 925.0,
    "queue_ms": 1.0,
    "compile_ms": 924.0
  }
}
```

`queue_ms = total_ms - compile_ms` includes small transport/scheduling costs.
Timeout responses use actual dequeue time; `compile_ms` is zero if execution never began.

Control acceptance of incomplete code with:

```json
{
  "code": "theorem unfinished : True := by sorry",
  "allow_sorry": false
}
```

`allow_sorry` is a JSON boolean, default `false`. False preserves the original
warning, adds a policy error and returns `okay: false`. True keeps only the warning
and permits `okay: true` if no other errors exist. This HTTP policy does not change
the worker protocol.

## Test coverage

- `tests/service/test_pool.py`: concurrency, FIFO, bounds and shutdown.
- `tests/service/test_recovery.py`: timeout, crash, protocol errors and recovery.
- `tests/service/test_quarantine.py`: duplicate/concurrent panics, healthy work,
  cancellation, deadlines, fingerprints, eviction and scheduling during replacement.
- `tests/workers/test_process.py`: NDJSON subprocess transport.
- `tests/workers/test_cli.py`: real Lean 4.30 CLI backend.
- `tests/test_http.py`: success, errors, warnings, timeout, overload, crash and readiness.

Scheduling tests use `tests/fixtures/fake_worker.py`, independent of task A.

## Lean phase timings and slow profiles

Both endpoints expose header parsing, command elaboration, diagnostic formatting/deduplication,
and optional profile-export times. `/check` uses `timings.compilation`; `/verify_proof` uses
`timings.formal_statement` and `timings.candidate`. Existing aggregate timings remain unchanged.
Comparison failures also expose the declaration, type/value phase, exception category and original
Lean message in `comparison_errors`; they are inconclusive checks rather than signature mismatches.

To capture detailed profiles, set these environment variables before starting the service; workers
inherit them:

```bash
export LEAN_SERVER_PROFILE_DIR=/tmp/lean-server-profiles
export LEAN_SERVER_PROFILE_SLOW_MS=1000
export LEAN_SERVER_PROFILE_THRESHOLD_MS=10
```

With `LEAN_SERVER_PROFILE_DIR` unset or empty, tracing and export are disabled. When enabled, Lean
collects its nested profiler traces during elaboration. Each compile/formal/candidate phase whose
elaboration takes at least `SLOW_MS` is exported as `lean-<pid>-<monotonic-nanoseconds>.json`.
`THRESHOLD_MS` controls the minimum trace-node duration retained by Lean. Both thresholds accept
non-negative integers; their defaults are 1000 ms and 10 ms. Set the trace threshold to zero for
short diagnostic reproductions.

Open the JSON file in Firefox Profiler to inspect declaration/tactic stacks and elapsed times.
The profile name includes the request ID and input phase. A `lean_profile` JSON record on worker
stderr also links the request ID, phase and file path; the service retains bounded stderr rather
than forwarding a continuous log. Request IDs never become file-name components. Failed compilations
can produce profiles too. A profile write failure logs to stderr without changing the proof verdict.

Profiling adds collection overhead even for phases below the export threshold, and pretty-printing
and export can be costly; use it for selected diagnostic runs. `profiling_ms` measures export cost,
while trace collection is included in `elaboration_ms`. Hard-killed/time-limited workers cannot
export their unfinished phase. Files are not automatically rotated; choose a suitable directory
and retention policy for the diagnostic run.
