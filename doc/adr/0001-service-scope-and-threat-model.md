# ADR-0001: Scope and threat model

- Status: Proposed
- Date: 2026-09-18

## Context

Public AXLE limits concurrency; evaluation needs frequent local verification.
One team controls callers and hosts, but model-generated Lean may contain errors,
infinite computation, excessive memory use or metaprogram side effects.

## Decision

Run a single-machine, single-tenant service for trusted callers. Treat generated
Lean as unreliable: enforce timeouts, memory limits, crash recovery and state cleanup.

The first phase uses separate API and worker processes, without per-request
sandboxes. Each failed worker can be killed and rebuilt independently.

Listen on `127.0.0.1` by default. Trusted-network access requires explicit
configuration and a separate ADR for authentication and network policy.

## Consequences

- Warm REPL workers avoid Mathlib cold starts.
- Crafted kernel bypasses, host attacks and malicious local tenants are outside scope.
- Worker isolation remains necessary for reliability.
- Public access would require reassessing sandboxes, seccomp, filesystem and network isolation.
