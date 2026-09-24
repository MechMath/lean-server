# ADR-0004: Worker isolation and lifecycle

- Status: Proposed
- Date: 2026-09-18

## Context

Generated Lean can hang, exhaust memory, crash or cause side effects. Persistent
workers can also accumulate memory or state.

## Decision

Use worker processes rather than per-request sandboxes:

- Never execute Lean inside the API/controller process.
- Start workers in separate process groups; kill the whole group on timeout.
- Enforce RSS/cgroup memory limits.
- Mark crashes, EOF, protocol errors and timeouts unhealthy; replace asynchronously.
- Recycle after the current request when `max_uses` or RSS thresholds are reached.
- Retry infrastructure failures at most once on a new worker.
- Do not retry compilation or verification failures.

Deployment containers disable external networking and mount Mathlib, toolchains
and application code read-only. Only a private tmpfs is writable; do not expose
host workspaces. Listen on loopback.

## State cleanup tests

- Request B cannot access declarations added by A.
- B uses default options after A changes them.
- B does not inherit A's attributes or scoped notation.
- B succeeds on a replacement worker after A times out.
- Verdicts remain stable across restarts.

## Consequences

Warm processes improve throughput and latency. Cleanup tests and periodic recycling
are release requirements: contamination can affect later requests on that worker.
If clean environments cannot be restored reliably, set `max_uses=1` or propose
stronger isolation in a new ADR.
