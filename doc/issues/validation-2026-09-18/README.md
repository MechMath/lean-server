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
- [`ndjson-stream-limit.md`](ndjson-stream-limit.md): 3,057 deterministic
  persistent-worker transport failures caused by the NDJSON stream limit.
- [`nat-pow-worker-panic.md`](nat-pow-worker-panic.md): one worker crash caused
  by `INTERNAL PANIC: Nat.pow exponent is too big`.
- [`data/summary.json`](data/summary.json): machine-readable aggregate counts
  and infrastructure-error taxonomy.
- `data/disagreements.jsonl`: all 4,926 records whose latest AXLE and local
  statuses differ. It is intentionally not linked inline because it is 43 MiB.

## Follow-up status (2026-09-21)

| Finding from the 2026-09-18 run | Status | Evidence / remaining work |
| --- | --- | --- |
| 3,057 NDJSON stream-limit failures | **Fixed** | The transport now accepts 8 MiB messages and duplicate diagnostics are collapsed. All 2,950 replayed non-excluded cases returned semantic results with no transport error; 107 malformed-reference cases were deliberately excluded. |
| One `Nat.pow exponent is too big` worker panic | **Mitigated** | The server identifies it as non-retryable `LeanPanic` and restores pool capacity. The Lean 4.30 panic itself still occurs and remains an infrastructure outcome. |
| 79 AXLE-pass/local-fail records | **Resolved in replay** | All 79 passed strict verification after import handling and the two long-running cases were rerun with a 600-second budget. |
| 1,789 AXLE-fail/local-pass records lacked strict local comparison | **Resolved for the supported API contract** | `/api/v1/verify_proof` rejected 1,785 and accepted four documented timeout/compatibility cases. Exact AXLE input-contract parity is not claimed. |
| Batch observability counters requested in the findings | **Open** | The branch improves typed errors and timing breakdowns but does not add the requested aggregate compile/timeout/crash/protocol/replacement/retry counters. |
| Invalid formal-statement extraction | **Out of scope** | 698 malformed-reference records were excluded by request; the extraction implementation is not in this repository. |

Detailed counts and deployment evidence are in
[`219-validation-2026-09-21.md`](219-validation-2026-09-21.md). The remaining
server-side work is therefore the underlying `Nat.pow` panic and batch
observability; exact AXLE compatibility requires a separate product decision.

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
