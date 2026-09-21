# Validation evidence archive

This directory stores reproducible feedback collected from large-scale Lean
server validation runs. Each run has its own dated directory containing the
analysis and compact summary data. Large per-record archives and run logs are
kept outside the repository.

Active work is tracked in [GitHub Issues](https://github.com/MechMath/lean-server/issues),
not in standalone issue documents here. The current tracking set is intentionally
small:

- [#4](https://github.com/MechMath/lean-server/issues/4): strict verification,
  worker hardening, and validation replay completed by PR #3;
- [#8](https://github.com/MechMath/lean-server/issues/8): remaining worker
  reliability and resource-boundary work;
- [#9](https://github.com/MechMath/lean-server/issues/9): batch and worker-pool
  observability;
- [#11](https://github.com/MechMath/lean-server/issues/11): AXLE compatibility
  and import-handling decisions.

## Runs

- [`validation-2026-09-18`](validation-2026-09-18/README.md): comparison of
  51,336 SFT candidates under remote AXLE `verify_proof` and the local Lean
  4.30.0 `/api/v1/check` service.

Do not classify records with local `status: error` as Lean failures. They are
infrastructure outcomes and must be rerun after the corresponding server issue
is fixed.
