# Lean 4.30 batch-validation feedback — 2026-09-18

This directory contains all feedback and all status disagreements collected by
checking the same 51,336 candidate files with remote AXLE and the local Lean
server.

## Contents

- [`followup-2026-09-21.md`](followup-2026-09-21.md): concise results from the
  fixes, test suite, classification, and historical replay.
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
| `Nat.pow` panic, responses above 8 MiB, and very slow verification | Open | [#8](https://github.com/MechMath/lean-server/issues/8) |
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

Only final reports and compact summaries are versioned. Run logs, deployment
snapshots, per-attempt metadata, detailed replay responses, and one-off work
scripts are deliberately omitted.

```text
4f69b848efed8554f837d55016a16e7917258d7f3cb12050c17b89ae7132f02f  data/summary.json
```

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
