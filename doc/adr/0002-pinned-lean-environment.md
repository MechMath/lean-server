# ADR-0002: Pinned Lean/Mathlib environment

- Status: Proposed
- Date: 2026-09-18

## Context

Proofs depend on exact Lean, Mathlib and dependency versions. Runtime changes
break reproducibility and force workers to rebuild or reload.

## Decision

Environment `lean-4.30.0` pins:

- Lean toolchain `v4.30.0`.
- Mathlib tag/revision `v4.30.0`.
- A compatible Lean REPL revision.
- The verifier metaprogram commit.
- Default header `import Mathlib`.

Download dependencies, run `lake update`, fetch Mathlib caches and build the
verifier when building the image. Running services do not access the network or
change dependencies.

A manifest records versions, Git revisions, build time and artifact hashes.
The API requires an explicit environment and rejects unknown values without fallback.

Candidate imports default to the fixed header, following AXLE's
`ignore_imports=true`. Support for `ignore_imports=false` is deferred.

## Consequences

- Benchmarks and regressions are reproducible.
- External imports such as `MiniF2F.ProblemImports` are not resolved at runtime;
  use `import Mathlib` or register another environment.
- A new Lean version requires a new prebuilt environment.
- Larger images and caches simplify the request path.
