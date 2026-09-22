# Lean Server design

A high-throughput Lean verification service for local or trusted networks, pinned
to Lean/Mathlib 4.30.0. It supports `lean-eval-toolkit` batch evaluation through an
AXLE-compatible `verify_proof` API.

The design draws on AXLE's paper and public API, plus Kimina's open-source worker
pool. AXLE's server implementation is not public.

## Goals

- Avoid public AXLE concurrency and rate limits.
- Preload Mathlib in persistent workers for frequent requests.
- Distinguish compilation success from proving the supplied statement.
- Reject `sorry`, unapproved axioms, changed targets and unsafe declarations.
- Recover capacity after worker hangs, crashes or memory failures.
- Let clients switch services mainly by changing `axle.api_url`.

## Non-goals

- All 14 AXLE tools in the first release.
- Uploaded Lake projects or runtime dependency installation.
- Public multitenancy, billing, API-key scheduling or cross-machine autoscaling.
- Per-request containers or sandbox processes in the first phase.
- Protection against crafted kernel-bypass metaprograms; callers are trusted.

## Documents

- [Architecture](architecture.md): components, request flow and milestones.
- [ADR-0001](adr/0001-service-scope-and-threat-model.md): scope and threat model.
- [ADR-0002](adr/0002-pinned-lean-environment.md): pinned environment.
- [ADR-0003](adr/0003-warm-repl-worker-pool.md): warm worker pool.
- [ADR-0004](adr/0004-worker-isolation-and-lifecycle.md): isolation and lifecycle.
- [ADR-0005](adr/0005-axle-compatible-api.md): HTTP compatibility.
- [ADR-0006](adr/0006-strict-proof-verification.md): strict verification.
- [ADR-0007](adr/0007-queueing-retries-and-capacity.md): queues and capacity.
- [ADR-0008](adr/0008-observability-and-validation.md): observability and validation.
- [ADR-0009](adr/0009-semantic-verification-contract.md): accepted semantics, imports and options.
- [References](references.md): papers, APIs and implementations.

## ADR status

- `Proposed`: pending implementation and benchmarks.
- `Accepted`: an implementation requirement.
- `Superseded`: replaced by a later ADR.

ADR-0009 is accepted; other ADRs retain their stated status. Record measurements
before accepting or revising proposals.
