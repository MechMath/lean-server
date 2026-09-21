# Review of AXLE-failed / local-passed records — 2026-09-21

All 4,926 archived disagreements were counted, and all 1,789 records in the
AXLE-failed / local-passed category were classified from their saved diagnostics.
**None of these 1,789 records reports an AXLE candidate-compilation error.**
The recorded failures concern proof policy, the original statement, declaration
matching, request validation, or timeouts. This is a statement about the saved
results, not a claim that every candidate would pass a fresh AXLE compilation.

## Full local replay result

All 1,789 UUIDs were replayed exactly once in 584.6 seconds:

| Result | Records |
| --- | ---: |
| Rejected by the strict verifier | 1,785 |
| Accepted | 4 |
| Timeout / infrastructure / internal error | 0 |

The four acceptances consist of both historical AXLE timeouts, one
default-parameter signature comparison, and one reference without a `sorry`
placeholder. The latter two are analyzed below. Both historical timeout cases
passed locally in approximately 224 ms and 297 ms of worker processing time;
their historical AXLE messages reported 900-second worker timeouts. No candidate
compilation errors were reported in this replay.

**Version boundary:** the full batch used the executable built at the start of
this review (SHA-256 `1a10e8ac5d181c5d440936ba804692db668598ebb8ae4ecc3a4ea6e6557abba4`).
During the run, another task changed the verifier, HTTP handler, and process
backend and rebuilt the executable. The four batch workers stayed alive without
restarts, so the batch describes their initially loaded build, not those later
edits. The metadata records both initial and final hashes. Fresh-worker checks
of the two compatibility cases after the rebuild produced the same results.
No server implementation was changed by this review, and the 219 deployment
was not updated.

## Historical failure reasons

These categories are mutually exclusive and sum to 1,789:

| Recorded AXLE reason | Records | Interpretation |
| --- | ---: | --- |
| Disallowed axioms only | 1,115 | Compilation accepts dependencies that strict verification rejects. |
| Original `formal_statement` does not compile | 591 | The reference input is invalid; `/check` receives only the candidate. |
| Required declaration missing | 34 | The candidate does not provide the expected declaration name. |
| Declaration signature mismatch only | 28 | The candidate changes the proposition or its parameters. |
| No theorem or definition found in the reference | 10 | No usable verification target was identified. |
| Input validation failure | 8 | AXLE reports no required `sorry` placeholder in the reference. |
| AXLE worker timeout | 2 | Resource failures recorded as `failed`; not proof rejections. |
| Both disallowed axioms and signature mismatch | 1 | Both strict verification checks fail. |

Including the overlapping record, 1,116 records have axiom-policy errors:
1,113 mention generated `native_decide` axioms, two mention generated
`decide +native` axioms, and one uses custom axioms. Native evaluation is accepted
by the historical compiler, while the archived AXLE policy admits only its
standard axiom set. This policy distinction alone is not evidence that the
mathematical conclusion is false.

Of the 591 reference-compilation failures:

| Diagnostic family | Records |
| --- | ---: |
| Unexpected end of input | 562 |
| Unterminated comment | 16 |
| Missing tactic / tactic sequence | 8 |
| Definition type cannot be inferred | 3 |
| Unexpected token | 2 |

565 of these 591 records come from `Goedel-LM/Goedel-Pset-v1`. Among the 562
unexpected-EOF records, 483 end in a `let` assignment replaced by `:= by sorry`;
the other 79 also end at an assignment, with examples such as an unfinished
default argument `(stock1_buy : ℝ := by sorry`. This suggests that extraction
may have mistaken an assignment inside the theorem type or a parameter for the
start of the proof. This is a hypothesis from the saved strings, not a confirmed
implementation diagnosis. The archive does not include the
statement-extraction implementation, so its exact cause cannot be established
from this archive alone. Inspect how references are extracted before repairing
or discarding them; do not replace them with candidate statements and then
count that as independent verification.

## Representative records

Each UUID below can be looked up in `data/disagreements.jsonl`; `line` is the
original dataset line, not the line number in the disagreement file.

- **Native axiom:** `5d555352-9c40-5b1f-8eca-5658456d5b84` (line 35868).
  The candidate proves `3^1999 % 13 = 3` using `native_decide`. AXLE explicitly
  rejects `number_theory_16689._native.native_decide.ax_1`.
- **Malformed reference:** `Goedel-Pset-1228570` (line 8043). Its reference ends
  with `theorem part_one (a : ℝ) : let f := by sorry`, while its candidate
  supplies a complete `let` expression and theorem. Compiling the candidate
  alone cannot detect that the reference was truncated.
- **Changed meaning despite similar source:** `544406e3-691d-5671-8d96-9f5c90c22691`
  (line 1934). In the reference, bare `π` becomes an implicit real parameter;
  the candidate adds `open Real`, making it refer to `Real.pi`. The elaborated
  theorem types differ even where the theorem text looks the same.
- **Missing target:** `2e67727a-c48e-5d5d-b2d9-5a2a0a38beff` (line 17071).
  The reference requires `algebra_165966`, a set equality. The candidate instead
  declares `reflected_hyperbola_equation`, a pointwise result.
- **Custom axioms:** `Goedel-Pset-609608` (line 14097). The reference and candidate
  introduce axioms about English idioms, then prove the target directly from
  those axioms. These are outside the archived standard-axiom policy.

## Reproducing the analysis

From the repository root:

```bash
PYTHONPATH=src .venv/bin/python \
  doc/issues/validation-2026-09-18/analyze_disagreements.py

lake build lean-server-worker
PYTHONPATH=src .venv/bin/python \
  doc/issues/validation-2026-09-18/analyze_disagreements.py --replay
```

The first command classifies the saved evidence without running Lean. The second
mode checks all 1,789 unchanged reference/candidate pairs against the current
local worker, using protocol v2, `use_def_eq=true`, four persistent workers, and
a 120-second per-request timeout. It uses the same success condition as HTTP
`/api/v1/verify_proof`: successful elaboration, no Lean errors, no tool errors,
and no failed declarations. Internal failures and timeouts remain separate from
semantic rejections. It does not rerun the AXLE service or test HTTP transport.

Generated files (the original archive is preserved):

- `data/axle-failed-local-passed-analysis.json`: reason counts and example UUIDs.
- `data/axle-failed-local-passed-classification.jsonl`: one reason record per UUID.
- `data/strict-replay-metadata.json`: configuration, source hashes, worker hash,
  and the Git base revision; the working tree includes uncommitted changes.
- `data/strict-replay.jsonl`: complete local verifier diagnostics per UUID.
- `data/strict-replay-summary.json`: replay counts and remaining disagreements.

The per-record classification JSONL and complete `strict-replay.jsonl` are
generated local artifacts ignored by Git; rerun the script to regenerate them.
Summaries, metadata, and the targeted follow-up evidence are kept.

## Other archived disagreements

The remaining 3,137 records are separate regression sets:

- 3,058 local infrastructure errors: 3,057 NDJSON stream-limit errors and one
  `Nat.pow` panic. 487 of these candidates passed AXLE.
- 79 AXLE-pass / local-fail results: 77 report an unknown import module and two
  report the local 120-second request timeout.

Neither set establishes that the 1,789 historical local passes were compiler
false positives. They need their own replay and diagnosis; the strict-verifier
replay above does not retest them.

## Remaining compatibility cases

Two non-timeout acceptances were reproduced independently with a fresh local
worker; their complete diagnostics are in `data/strict-replay-followup.json`.

1. `Goedel-LM/SFT_dataset_v2=1292094`: AXLE reported a signature mismatch between
   binders with default arguments (`optParam ℕ 60`, etc.) and ordinary `ℕ`
   binders. The current local verifier accepts this record with
   `use_def_eq=true`, and rejects it with `use_def_eq=false`. In the pinned Lean
   source, `Init/Prelude.lean` defines `@[reducible] def optParam ... := α`, so
   these parameter types are definitionally equal. The historical AXLE request
   options are not preserved in this archive; it cannot establish whether the
   difference was caused by request configuration or AXLE's comparison policy.
   This example does not demonstrate acceptance of a different proposition.
2. `Goedel-Pset-1652807`: AXLE rejected the request because the reference lacked
   a `sorry` placeholder. The saved reference actually contains a complete
   proof of `direction_sign`; both it and the candidate elaborate successfully.
   The local verifier does not require a placeholder and accepts the matching
   theorem. This is an input-contract difference, not a failed Lean proof.

These findings establish that the current implementation is still an
AXLE-compatible subset, not exact reproduction of all historical AXLE outcomes.
Do not silently disable definition equality globally merely to match one saved
outcome, or require placeholder text without deciding the intended API contract.

## Recommended next steps

Follow-up: [219 validation](219-validation-2026-09-21.md) records the subsequent
service fixes, complete remote test suite, and replay/deployment status. The
user has explicitly excluded invalid-reference extraction from further work.
The recommendations below describe the boundary of this earlier review.

1. Use `/api/v1/verify_proof` with both the original reference and candidate when
   the desired decision is "proves this problem." `/check` alone cannot answer
   that question because it never receives the reference. The notebook's saved
   404 from the 219 deployment means that deployment needs updating before it
   can expose this operation; this review did not deploy or restart it.
2. Inspect the reference extraction pipeline. Parse Lean declarations and
   preserve complete theorem types, default parameter values, and comments;
   replacing text after the first `:=` is not a valid general proof extractor.
   The original extraction script was not available in this workspace.
3. Distinguish invalid references, candidate compile failures, policy rejections,
   signature mismatches, timeouts, and infrastructure failures in future result
   files. Save the full verification request options along with the response.
4. Decide whether exact AXLE input-contract parity is required for the two cases
   above, and document the decision before changing verifier behavior.
5. Replay the independent import-compatibility and transport-error sets after
   their fixes; they are not covered by the 1,789-record replay.

## Independent review of the concurrent fixes

After the other session reported completion, the updated implementation and its
regressions were checked independently. The four reported fixes are present:

- `requiredDeclarations` follows generated dependencies of fixed definitions,
  closing the recursive-function auxiliary-definition comparison gap.
- Worker stderr is drained in chunks with a bounded 64 KiB retained tail, and
  logging/cleanup exceptions do not silently remove a worker slot.
- Request deadlines cover both queueing and execution; queued expiry and
  cancellation release capacity before a worker becomes available.
- Replacement workers are tracked before their startup await, and cancelled
  startup cleans up its process.

96 distinct tests passed in this review: 28 real Lean verifier tests, six real
Lean worker tests, ten protocol tests, 16 pool/recovery tests, eight process
backend tests, 22 HTTP tests, and six diagnostics/preflight tests. HTTP tests
used the fake worker over a real local socket; the Lean tests exercised the
built executable. The initial HTTP attempt was blocked by the sandbox socket
restriction, and the successful rerun used the approved permission escalation.

The full discovery run was **not** certified: discovery stalled at
the old CLI backend's `test_reports_sorry_as_a_warning` and was interrupted.
This review confirms the four specific regressions, not all possible verifier
behavior, a fresh full replay of the historical dataset on this later build,
or deployment to 219. Reference extraction and the two compatibility choices
discussed above are outside those four fixes.
