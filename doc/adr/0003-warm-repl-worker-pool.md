# ADR-0003: Warm Lean REPL worker pool

- Status: Proposed
- Date: 2026-09-18

## Context

Starting Lean and loading Mathlib per request makes cold starts dominant. AXLE's
experiments report roughly half the throughput without preloading. Kimina shows
that persistent REPLs, parallel workers and import caches are practical.

## Decision

Maintain a fixed pool of persistent Lean REPL processes:

- Load the pinned environment and verifier at startup.
- Run one request per worker; dispatch only to ready workers.
- Size the pool by CPU and memory, normally no more than available physical cores.
- Warm workers before readiness; require minimum capacity for `readyz`.
- Reuse Kimina's pool/supervisor ideas with a project-specific Lean verifier.

Each request creates separate formal and candidate environments from an immutable
base. Discard derived state after responding; never reuse candidate changes.

## Alternatives

- **`lake env lean` per request:** simple and strongly isolated, but too costly to use by default.
- **A separate warm sandbox per request:** close to AXLE, but cheap environment cloning lacks public implementation details and costs too much for phase one.
- **One shared REPL:** cannot use multiple cores; one hang or crash blocks the service.

## Consequences

- Low latency and throughput that scales roughly with cores.
- Correct environment cleanup and worker recycling are required.
- Benchmark resident memory to set concurrency.
- Weaker request isolation than AXLE, within the local single-tenant threat model.
