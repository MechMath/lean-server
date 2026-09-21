# Known issue: `Nat.pow exponent is too big` terminates a persistent worker

## Status

- Active follow-up is tracked in [GitHub issue #8](https://github.com/MechMath/lean-server/issues/8).
- Local follow-up: the pool now quarantines exact inputs after this typed panic
  and serializes concurrent identical inputs, preventing repeated replacement
  while the fingerprint is retained. Healthy inputs can use other workers during
  replacement. The bounded, process-local quarantine and its restart/eviction
  limits are documented in [worker-pool.md](../../worker-pool.md#崩溃输入隔离).
  This does not repair the underlying panic or establish a minimized reproducer.
- Mitigated on 2026-09-21: this known panic returns HTTP 503 with
  `error_type: "LeanPanic"` and `retryable: false`; the pool replaces the crashed
  worker and remains usable. The underlying Lean 4.30 panic is not repaired.
  Clients should preserve this infrastructure outcome and avoid retrying the
  identical input automatically. It is not an `okay: false` proof verdict.
- Confirmed on 2026-09-18 with Lean 4.30.0.
- Observed once in a full batch of 51,336 candidate files.
- This is an infrastructure failure, not a normal Lean compile result.
- The server returned retryable HTTP 503 and replaced the worker.

## Historical response (before mitigation)

```json
{
  "error": "worker exited with status 1: INTERNAL PANIC: Nat.pow exponent is too big",
  "retryable": true
}
```

## Local quarantine validation — 2026-09-21

- Full Python/Lean/HTTP suite: **119 tests passed in 104.283 seconds** outside
  the local sandbox. The sandbox prevents the HTTP listener from binding and
  the initial sandboxed archive check exceeded its 30-second budget.
- Replayed the unchanged archived candidate through the real persistent worker
  with two pool slots, three concurrent identical requests and a 120-second
  total budget outside the sandbox. One request produced `LeanPanic`; the other
  two were quarantined without execution. All three errors were non-retryable.
- A following `theorem healthy : True := by trivial` compiled successfully.
  Final pool state: two ready workers, zero active/queued requests, exactly one
  replacement, one quarantined input and two quarantine hits.
- Subprocess regressions also cover cancellation during process creation and
  panic classification when the worker exits before the response is read.
  These are local validation results; they do not record a production rollout.

Affected record:

| Field | Value |
| --- | --- |
| UUID | `Goedel-Pset-153949` |
| Dataset line | 47,553 |
| Source | `formalmathatepfl/solved_problems_finetuning_iter1` |
| Candidate size | 775 characters |

## Minimal reproduction direction

The candidate imports Mathlib and Aesop, disables the heartbeat limit, and
applies `norm_num` to hypotheses instantiated with increasingly precise decimal
literals near 2:

```lean
have h₄ := h₀ 1.999999999999999 (by norm_num)
have h₅ := h₀ 1.9999999999999999 (by norm_num)
-- additional literals with still more decimal digits
norm_num at h₁ h₂ h₃ h₄ h₅ h₆ h₇ h₈ h₉ h₁₀
```

The exact minimal triggering literal or tactic expansion has not yet been
isolated. A likely direction is decimal elaboration or `norm_num` constructing
an excessively large natural-power exponent. This is a hypothesis, not a
confirmed root cause.

The complete reproducer is in the external source archive (available in Git
history before the archive cleanup):

```text
data/disagreements.jsonl
```

under UUID `Goedel-Pset-153949`.

## Historical impact

- The Lean worker exits instead of returning a structured compile error.
- The pool reports a retryable 503 and replaces the worker.
- Blind retry may repeatedly crash replacement workers for deterministic input.
- A batch validator cannot classify the candidate as passed or failed from this
  response alone.

## Suggested improvements

1. Reproduce the candidate directly with the worker binary and capture a full
   native/Lean stack trace.
2. Reduce the decimal-literal sequence until the smallest crashing source is
   found.
3. Check whether the panic occurs in plain `lake env lean`, only in the custom
   worker, or both.
4. Convert this panic into an isolated worker failure with a stable error code;
   do not expose raw stderr as a generic retryable 503.
5. Add a per-input crash fingerprint or retry budget so deterministic panics do
   not cause an unlimited replacement loop.
6. Keep the affected record classified as infrastructure error until it can be
   rerun after the underlying Lean/worker behavior is understood.

## Regression tests

1. The full candidate no longer kills the worker process.
2. The minimized reproducer returns either a normal compile result or a stable,
   explicitly typed resource/unsupported-input error.
3. A worker crash does not reduce pool capacity permanently.
4. Repeating the same crashing input does not create an unbounded replacement
   loop.
5. A healthy request succeeds immediately after the failure.
