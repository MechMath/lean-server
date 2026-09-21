# Lean 4.30 batch-validation feedback — 2026-09-18

This directory contains all feedback and all status disagreements collected by
checking the same 51,336 candidate files with remote AXLE and the local Lean
server.

## Contents

- [`219-validation-2026-09-21.md`](219-validation-2026-09-21.md): follow-up
  service fixes, remote tests, historical HTTP replay, and deployment status.
- [`axle-failed-local-passed-review.md`](axle-failed-local-passed-review.md):
  2026-09-21 diagnosis of all 1,789 AXLE-failed/local-passed records and replay
  against the current strict verifier.
- [`analyze_disagreements.py`](analyze_disagreements.py): reproducible diagnostic
  classification and optional local strict-verifier replay.
- [`batch-validation-findings.md`](batch-validation-findings.md): experiment
  scope, complete result matrix, interpretation, and prioritized improvements.
- [`data/summary.json`](data/summary.json): machine-readable aggregate counts
  and infrastructure-error taxonomy.
- `data/disagreements.jsonl`: all 4,926 records whose latest AXLE and local
  statuses differ. It is intentionally not linked inline because it is 43 MiB.

## GitHub tracking

Active work and closure state now live in GitHub rather than separate issue
documents in this archive.

| Topic | Status | GitHub issue |
| --- | --- | --- |
| Strict verification, NDJSON repair, pool hardening, and historical replay | Completed by PR #3; closes on merge | [#4](https://github.com/MechMath/lean-server/issues/4) |
| `Nat.pow` panic, responses above 8 MiB, and very slow verification | Open | [#8](https://github.com/MechMath/lean-server/issues/8) |
| Batch and worker-pool observability | Open | [#9](https://github.com/MechMath/lean-server/issues/9) |
| AXLE compatibility and `/check` import semantics | Needs decision | [#11](https://github.com/MechMath/lean-server/issues/11) |

Detailed counts and deployment evidence are in
[`219-validation-2026-09-21.md`](219-validation-2026-09-21.md). The 698 malformed
formal statements remain excluded because their extraction implementation is
outside this repository.

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

The disagreement JSONL contains one object per mismatched UUID with:

- dataset UUID, original line, and source;
- disagreement category;
- complete formal statement and candidate;
- compact AXLE result;
- compact local result and diagnostics.

This makes each issue independently reproducible without requiring the original
363 MiB SFT JSONL.

## Integrity

Generated per-record classification/replay JSONL, compressed HTTP responses,
and intermediate logs are local artifacts ignored by Git. Original archives,
replay scripts, reports, summaries, metadata, and final test evidence are kept.
Run the replay scripts to regenerate detailed outputs when needed.

```text
4f69b848efed8554f837d55016a16e7917258d7f3cb12050c17b89ae7132f02f  data/summary.json
f22b9737513e46ce8aa8909c36797945ea58932584ba12ebc8732a1faf83f026  data/disagreements.jsonl
```

`data/disagreements.jsonl` contains exactly 4,926 lines.

## Important interpretation boundary

The local `/check` endpoint and AXLE `verify_proof` do not enforce identical
semantics. Local success means that the complete candidate elaborated in the
pinned local environment without a reported `sorry`. AXLE additionally checks
the candidate against the original declaration and applies stricter dependency
policy. The implemented `/api/v1/verify_proof` endpoint supplies those additional
checks; see the follow-up report for its deployment and validation. Do not
interpret every historical AXLE-failed/local-passed record as a local compiler
defect.

Conversely, AXLE-passed/local-failed and AXLE-passed/local-error records are
direct local compatibility or infrastructure regression sets and should be
prioritized.
