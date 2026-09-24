# ADR-0009: Semantic verification and imports

- Status: Accepted
- Date: 2026-09-21
- Related: [issue #11](https://github.com/MechMath/lean-server/issues/11)

## Decision

`POST /verify_proof` checks whether a candidate meets a formal statement's semantic
requirements. This is a defined AXLE-compatible subset, without promising every
historical AXLE restriction or verdict. The old `/api/v1/verify_proof` route is
removed. This resolves input compatibility left open in ADR-0005/0006.

## Lean rules

1. `use_def_eq=true` (default) compares elaborated types and fixed definition values
   by definitional equality. `optParam Nat 60` matches `Nat`; default-argument call
   syntax is outside this contract.
2. `use_def_eq=false` compares expression structure after positional universe
   normalization, without reduction or source-text comparison. Those types do not match.
3. Formal statements may have complete proofs; no `sorry` placeholder is required.
   Candidate theorem proofs may differ, but names, kinds, types and fixed definition
   implementations must pass existing checks.
4. Formal statements must introduce a verifiable declaration. Empty input, imports,
   comments, `#check` or the text `sorry` alone are not targets.
5. Formal compilation errors are preserved and skip the candidate; `candidate_ms`
   and `declarations_ms` are zero. Target-free statements also skip the candidate
   and return `formal_statement contains no verifiable declarations`;
   `candidate_ms=0`, while `declarations_ms` records target discovery.
6. Targets and dependencies still reject `sorry`, unapproved axioms and unsafe
   declarations, even when the formal statement has no placeholders.

Formal and candidate elaborate independently from the same base. Formal declarations,
placeholders, notation, attributes and `set_option` do not enter the candidate.
Requests do not inherit prior elaboration state. This follows the trusted local
model; it does not promise isolation of arbitrary metaprogram IO or process globals.

## Imports

| Endpoint | Behavior |
| --- | --- |
| `/verify_proof` | Use preloaded Mathlib; ignore requested module names in both inputs, without loading them. |
| `/check` | Use the same environment; validate requested modules and reject unknown or old paths. |

Ignoring names does not ignore syntax errors, create old-path aliases or add new
import modes. The same old import may fail `/check` and pass `/verify_proof`.
`/check` is not equivalent to a standalone file with only the listed imports:
all preloaded Mathlib declarations remain available.

## HTTP options and errors

Omitted fields use the defaults below. Invalid types return HTTP 400.

| Field | Supported/default value | HTTP 400 `error` for unsupported modes |
| --- | --- | --- |
| `permitted_sorries` | `[]` | `non-empty permitted_sorries is not supported` |
| `mathlib_options` | `false` | `mathlib_options=true is not supported` |
| `global_options` | `{}` | `non-empty global_options is not supported` |
| `verify_negation` | `false` | `verify_negation=true is not supported` |
| `ignore_imports` | `true` | `ignore_imports=false is not supported` |
| `use_def_eq` | `true` or `false`; default `true` | Non-boolean: `use_def_eq must be a boolean` |

Python rejects unsupported modes before dispatch; they cannot change Lean options,
allowed axioms or resource limits. Source-level `set_option` remains local. Disabling
heartbeats does not disable Python's total queue-and-execution deadline.

`timeout_seconds` must be finite and positive. Deployment sets the default and
maximum; compilation limits are separate. HTTP budgets are not worker fields.

The [HTTP schema](../../protocol/http/verify-proof-request.schema.json) describes
the supported subset. Lean checks compilation and targets; HTTP enforces deployment
timeout limits.

## Internal protocol

HTTP passes only supported execution fields to Lean. Workers reject unknown fields,
including HTTP-only options, so `verify_negation=true` cannot silently run ordinary
verification. Omitted `use_def_eq` defaults to `true`, matching the schema.

Invalid NDJSON requests log to stderr, emit no result and leave the worker reading
the next request. Use HTTP for structured HTTP 400 responses; worker stdin is not
the public API.

## Compatibility validation

- `Goedel-LM/SFT_dataset_v2=1292094`: accepted by definitional equality, rejected structurally.
- `Goedel-Pset-1652807`: accepted with a complete, placeholder-free statement.
- Wrong theorem types and candidate `sorry` remain rejected with complete statements.
- Unknown imports are ignored in verification and rejected in compilation, even on one worker.
- Formal environments/options and request comparison modes do not leak.
- Positive/negative candidate file-write controls confirm skipped execution for invalid or empty targets.
- HTTP schemas, options and invalid worker fields cover acceptance and rejection.

Original samples from commit `9a955b2` are in
[`semantic-compatibility.json`](../../tests/fixtures/semantic-compatibility.json).
Tests: `tests/lean/test_verifier.py`, `tests/lean/test_worker.py`,
`tests/contract/test_verify_api.py` and `tests/test_http.py`.

Validation on 2026-09-21: `lake build lean-server-worker` passed;
`PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -v` passed
**144 tests in 118.195 seconds**, covering real Lean, protocol, HTTP and scheduling.
