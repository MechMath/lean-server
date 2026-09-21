# Lean 4.30 batch-validation feedback — 2026-09-18

This directory contains reports and summaries from checking the same 51,336
candidate files with remote AXLE and the local Lean server.

## Contents

- [`resource-boundaries-2026-09-21.md`](resource-boundaries-2026-09-21.md):
  bounded oversized-response draining, isolated long verification, configurable
  budgets and reproducible slow-proof profiling.
- [`219-validation-2026-09-21.md`](219-validation-2026-09-21.md): follow-up
  service fixes, remote tests, historical HTTP replay, and deployment status.
- [`axle-failed-local-passed-review.md`](axle-failed-local-passed-review.md):
  2026-09-21 diagnosis of all 1,789 AXLE-failed/local-passed records and replay
  against the current strict verifier.
- [`analyze_disagreements.py`](analyze_disagreements.py): reproducible diagnostic
  classification and optional local strict-verifier replay.
- [`followup-2026-09-21.md`](followup-2026-09-21.md): concise results from the
  fixes, test suite, classification, and historical replay.
- [`nat-pow-worker-panic.md`](nat-pow-worker-panic.md): historical panic evidence
  and subsequent local input-quarantine validation.
- [`ndjson-stream-limit.md`](ndjson-stream-limit.md): historical transport failure
  and diagnostic-deduplication evidence.
- [`batch-validation-findings.md`](batch-validation-findings.md): experiment
  scope, complete result matrix, interpretation, and prioritized improvements.
- [`data/summary.json`](data/summary.json): machine-readable aggregate counts
  and infrastructure-error taxonomy.
- [`data/followup-summary.json`](data/followup-summary.json): machine-readable
  final follow-up and replay counts.

## GitHub tracking

Active work and closure state now live in GitHub rather than separate issue
documents in this archive.

| Topic | Status | GitHub issue |
| --- | --- | --- |
| Strict verification, NDJSON repair, pool hardening, and historical replay | Completed by PR #3; closes on merge | [#4](https://github.com/MechMath/lean-server/issues/4) |
| `Nat.pow` panic, responses above 8 MiB, and very slow verification | Resource-boundary follow-up implemented; upstream panic diagnosis open | [#8](https://github.com/MechMath/lean-server/issues/8) |
| Batch and worker-pool observability | Open | [#9](https://github.com/MechMath/lean-server/issues/9) |
| AXLE compatibility and `/check` import semantics | Needs decision | [#11](https://github.com/MechMath/lean-server/issues/11) |

Final follow-up counts are in
[`followup-2026-09-21.md`](followup-2026-09-21.md). The 698 malformed formal
statements remain excluded because their extraction implementation is outside
this repository.

## Result matrix

| AXLE | Local | Records |
| --- | --- | ---: |
| passed | passed | 35,271 |
| failed | failed | 11,139 |
| failed | passed | 1,789 |
| passed | failed | 79 |
| failed | infrastructure error | 2,571 |
| passed | infrastructure error | 487 |
| **Total** |  | **51,336** |

## Integrity

Generated per-record classification/replay JSONL, compressed HTTP responses,
and intermediate logs are local artifacts ignored by Git. Existing replay
scripts, reports, summaries, metadata, and final test evidence are retained.
The large `data/disagreements.jsonl` source archive is no longer versioned;
restore it from external validation data or Git history before running the
analysis and replay scripts. The transport regression test keeps its exact
candidate separately in `tests/fixtures/repeated-diagnostic.lean`.

```text
88a72cd5e232faa0934025dc3035dedca3a115bc5674d6d0b0aa3e99c03160ab  data/summary.json
```

## Important interpretation boundary

The local `/check` endpoint and AXLE `verify_proof` do not enforce identical
semantics. Local success means that the complete candidate elaborated in the
pinned local environment without a reported `sorry`. AXLE additionally checks
the candidate against the original declaration and applies stricter dependency
policy. The implemented `/verify_proof` endpoint supplies those additional
checks; see the follow-up report for its deployment and validation. Do not
interpret every historical AXLE-failed/local-passed record as a local compiler
defect.

Conversely, AXLE-passed/local-failed and AXLE-passed/local-error records are
direct local compatibility or infrastructure regression sets and should be
prioritized.
