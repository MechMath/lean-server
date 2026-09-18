# Validation issue archive

This directory stores reproducible feedback collected from large-scale Lean
server validation runs. Each run has its own dated directory containing the
analysis, issue descriptions, summary data, and complete disagreement records.

## Runs

- [`validation-2026-09-18`](validation-2026-09-18/README.md): comparison of
  51,336 SFT candidates under remote AXLE `verify_proof` and the local Lean
  4.30.0 `/api/v1/check` service.

Do not classify records with local `status: error` as Lean failures. They are
infrastructure outcomes and must be rerun after the corresponding server issue
is fixed.
