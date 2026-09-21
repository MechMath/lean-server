# Batch validation findings: local Lean server versus AXLE

## Scope

On 2026-09-18, 51,336 unique SFT candidates from
`formalmathatepfl/reasoning_data` were checked with:

- remote AXLE `verify_proof` in `lean-4.30.0`; and
- local Lean server `POST /api/v1/check` using Lean 4.30.0, 32 persistent
  workers, queue capacity 64, client concurrency 8, and `allow_sorry: false`.

Both runs used the same immutable JSONL snapshot and were joined by UUID. The
local endpoint checks whether the submitted candidate compiles and contains no
`sorry`; AXLE additionally compares it with the original formal statement and
applies stricter declaration/dependency policy. Their results are therefore
informative but not semantically identical.

Generated analysis artifacts:

```text
data/summary.json
data/disagreements.jsonl
```

The disagreement JSONL retains the formal statement, complete candidate,
compact AXLE result, and compact local result for reproduction.

## Result matrix

| AXLE status | Local status | Records | Interpretation |
| --- | --- | ---: | --- |
| passed | passed | 35,271 | Agreement |
| failed | failed | 11,139 | Agreement on rejection |
| failed | passed | 1,789 | Usually stricter AXLE policy or declaration matching |
| passed | failed | 79 | Local environment/compiler discrepancy to investigate |
| failed | error | 2,571 | Local infrastructure error; not a semantic comparison |
| passed | error | 487 | Local infrastructure false negative; high-priority regression set |
| **Total** |  | **51,336** |  |

Latest aggregate status:

| Validator | Passed | Failed | Infrastructure error |
| --- | ---: | ---: | ---: |
| AXLE | 35,837 | 15,499 | 0 |
| Local | 37,060 | 11,218 | 3,058 |

There are 46,410 records with the same terminal passed/failed status and 4,926
records whose status differs or is unresolved locally.

## Local infrastructure errors

| Category | Count | Tracking |
| --- | ---: | --- |
| NDJSON response exceeds asyncio stream limit | 3,057 | [GitHub #4](https://github.com/MechMath/lean-server/issues/4) |
| Worker panic: `Nat.pow exponent is too big` | 1 | [GitHub #8](https://github.com/MechMath/lean-server/issues/8) |

These 3,058 records must not be counted as Lean failures. A requested retry was
started after the first run, but the local server was no longer accepting
connections on port 18000; the retry stopped during health preflight with
`Connection refused` and did not modify the latest per-UUID outcomes.

## Terminal disagreements

### AXLE failed, local passed: 1,789

Many examples are expected differences in verification strength. AXLE rejects
some candidates because of declaration mismatch or dependency policy, including
standard-axiom restrictions such as dependencies introduced by
`native_decide`. A successful local `/check` only establishes that the complete
file elaborates under the local environment and does not contain a reported
`sorry`; it does not establish AXLE-equivalent proof validity.

Action: implement and test the planned local `/api/v1/verify_proof` endpoint
before treating this category as a local false positive.

### AXLE passed, local failed: 79

This is the most useful terminal regression category. Representative failures
include imports unavailable in the local pinned checkout, such as:

```lean
import Mathlib.MeasureTheory.Integral.SetIntegral
```

Action: group all 79 records by first local diagnostic, distinguish missing
module/environment drift from elaboration differences, and add representative
fixtures to environment preflight and integration tests.

## Prioritized improvements

This is the original 2026-09-18 priority list. Current status is tracked in
[GitHub #4](https://github.com/MechMath/lean-server/issues/4),
[#8](https://github.com/MechMath/lean-server/issues/8),
[#9](https://github.com/MechMath/lean-server/issues/9), and
[#11](https://github.com/MechMath/lean-server/issues/11).

1. Fix the NDJSON stream limit and rerun the 3,057 affected UUIDs.
2. Isolate and minimize the `Nat.pow` panic; prevent deterministic crash retry
   loops.
3. Investigate the 79 AXLE-pass/local-fail records as local compatibility
   regressions.
4. Implement AXLE-compatible `/verify_proof`; `/check` cannot resolve the 1,789
   AXLE-fail/local-pass policy differences.
5. Add batch observability counters for compile failures, timeouts, worker
   crashes, protocol-size failures, replacements, overload responses, and
   retry exhaustion.
6. Preserve UUID-addressable audit results so repaired server versions can rerun
   only infrastructure errors and changed disagreement classes.

## Acceptance criteria for the next comparison

- All 51,336 UUIDs receive a terminal local semantic result; zero infrastructure
  errors remain.
- The 487 AXLE-pass/local-error records are all resolved.
- Worker replacements do not scale with invalid-input count.
- Every AXLE-pass/local-fail case is assigned a stable error taxonomy.
- Strict local `/verify_proof` results are compared separately from permissive
  local `/check` results.
