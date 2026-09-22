# ADR-0008: AXLE comparison and fault injection

- Status: Proposed
- Date: 2026-09-18

## Context

Key risks are semantic differences from AXLE, cross-request state leaks and lost
capacity after failures.

## Decision

Use four test layers:

### 1. Lean verifier unit tests

Run ADR-0006 cases against the metaprogram. Assert verdicts, failed declarations
and error categories.

### 2. API contract tests

Use a pinned official `axiom-axle` SDK. Cover success, rejection, invalid arguments,
timeouts, overload and internal errors.

### 3. Golden parity tests

Store AXLE-verified miniF2F and Putnam samples plus constructed counterexamples.
Require matching verdicts. Message text may differ; error categories and
`failed_declarations` must be explainable.

Keep remote results as versioned fixtures with source, environment and date.
Do not call remote AXLE in routine CI.

### 4. Stability and performance

- Run at least several thousand consecutive requests.
- Inject worker kills, EOF, timeouts and memory pressure.
- Check automatic capacity recovery and no declaration, option or attribute leaks.
- Measure throughput at concurrency 1, 2, 4, 8, etc. to find saturation.
- Record cold starts, warm latency and p50/p90/p99.

## Logging

Log request ID, environment, timings, worker ID, attempt, verdict and error category.
Do not log full proofs by default; explicit debug mode may save them in a controlled
directory.

## Release gates

- All strict-verification counterexamples pass.
- No unexplained golden-corpus verdict differences.
- Capacity recovers after crashes and timeouts.
- Soak tests show no sustained RSS growth or handle leaks.
- Warm pools measurably outperform starting Lean per request.
- Document hardware, worker count and reproducible benchmark commands.
