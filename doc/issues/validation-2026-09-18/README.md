# Lean 4.30 batch-validation feedback — 2026-09-18

This directory contains all feedback and all status disagreements collected by
checking the same 51,336 candidate files with remote AXLE and the local Lean
server.

## Contents

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
policy. Implement the planned local `/verify_proof` endpoint before treating
every AXLE-failed/local-passed record as a local verifier defect.

Conversely, AXLE-passed/local-failed and AXLE-passed/local-error records are
direct local compatibility or infrastructure regression sets and should be
prioritized.
