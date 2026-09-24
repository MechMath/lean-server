# Lean worker protocol

Python and Lean exchange UTF-8 NDJSON, one message per line. All messages use
`protocol_version: 2`; `type` selects the operation. stdout carries only protocol
messages; logs go to stderr.

## Lifecycle

1. Python starts a worker with Lean/Mathlib 4.30.0.
2. The worker loads the environment and emits `ready`.
3. Python sends `compile` or `verify` and awaits a matching `request_id`.
4. Each worker handles one request at a time; operations may alternate.

Python owns deadlines, process termination, restarts, queues and overload control.
EOF, invalid JSON, wrong versions or request IDs are worker failures. Lean handles
elaboration, structured diagnostics, verification and environment isolation.

## Messages

| Purpose | Request type | Response type | Schema |
| --- | --- | --- | --- |
| Startup | — | `ready` | [ready](ready.schema.json) |
| Compile | `compile` | `result` | [request](compile-request.schema.json), [result](compile-result.schema.json) |
| Verify | `verify` | `verify_result` | [request](verify-request.schema.json), [result](verify-result.schema.json) |

Ready:

```json
{"protocol_version":2,"type":"ready","lean_version":"4.30.0"}
```

Compile:

```json
{"protocol_version":2,"type":"compile","request_id":"req-1","code":"def answer : Nat := 42"}
{"protocol_version":2,"type":"result","request_id":"req-1","status":"ok","compile_ms":12.5,"warnings":[],"errors":[]}
```

Verify:

```json
{"protocol_version":2,"type":"verify","request_id":"verify-1","formal_statement":"theorem t : True := by sorry","content":"theorem t : True := True.intro","use_def_eq":true}
{"protocol_version":2,"type":"verify_result","request_id":"verify-1","status":"ok","compile_ms":4.0,"formal_statement_ms":1.0,"candidate_ms":2.0,"declarations_ms":1.0,"warnings":[],"errors":[],"tool_errors":[],"failed_declarations":[]}
```

Parser/elaborator errors use `compile_error`; worker failures use `internal_error`.
Policy rejections such as `sorry` or nonstandard axioms use `status: "ok"` with
`tool_errors` and `failed_declarations`. Full fixtures are in [examples/](examples/).

### Phase timings and comparison errors

Workers also emit these additive v2 fields (decoders accept their absence from older v2 results):

- Compile `timings`: `header_ms`, `elaboration_ms`, `diagnostics_ms`, `profiling_ms`.
- Verify `timings.formal_statement` and `timings.candidate`: the same four fields per input.
- Verify `comparison_errors`: objects containing `declaration`, `phase` (`type` or `value`),
  `kind` (`resource_limit`, `interrupted`, or `internal_error`), and the original `message`.

Header timing covers input preparation, header parsing and import validation. Elaboration timing
covers command parsing/elaboration and waiting for frontend results. Diagnostics timing covers
message formatting and deduplication. Profiling timing covers optional profile export and is zero
when no export is attempted. Existing aggregate times remain inclusive; they also include small
overheads such as releasing frontend state. Skipped candidate timings are all zero.

A comparison exception means verification could not complete, not that the expressions differ.
It produces a structured `comparison_errors` entry and a human-readable `tool_errors` entry.
It does not by itself add a declaration to `failed_declarations`; independent semantic failures
still do. The result retains `status: "ok"` because elaboration completed and the worker remains
usable. Consumers must check errors, not status alone, when deciding whether verification passed.

HTTP exposes compile detail under `timings.compilation`, and verification detail under
`timings.formal_statement` / `timings.candidate`. `/verify_proof` forwards `comparison_errors`
and returns `okay: false` whenever it is nonempty. These errors are distinct from ordinary
signature/value mismatch messages and from process-level `internal_error` responses.

Detailed declaration/tactic profiles are opt-in; see
[profiling configuration](../doc/worker-pool.md#lean-phase-timings-and-slow-profiles).

## Verification contract and HTTP fields

[ADR-0009](../doc/adr/0009-semantic-verification-contract.md) defines the accepted
semantics. Complete statements need no `sorry`. `use_def_eq` defaults to `true`.
Invalid statements or statements without targets skip candidate compilation.

The [HTTP schema](http/verify-proof-request.schema.json) lists supported fields.
HTTP rejects unsupported modes. Worker schemas contain only execution fields;
extra fields are rejected. Invalid worker requests are logged to stderr and
skipped; HTTP returns structured request errors.

## Response size limits

The limit is 8 MiB (8,388,608 bytes), including UTF-8 JSON and its trailing newline.
Exactly 8 MiB is valid. Larger messages are not parsed and return HTTP 503:

```json
{"error":"worker message exceeds 8388608 bytes","error_type":"WorkerMessageTooLarge","retryable":false}
```

Reads use roughly 64 KiB chunks and retain at most 8 MiB of unparsed data. On
oversize, Python discards buffered data and drains through the newline, then
reuses the worker. Do not retry identical input automatically.

Draining stays within the request deadline, with separate limits of 2 seconds and
64 MiB of additional data. EOF or a drain limit prevents frame recovery: return
the same size error and replace the worker. If the request deadline expires first,
return the usual timeout and replace it.

Oversize is an infrastructure error, without `okay: false`. The limit bounds raw
bytes retained by Python, not Lean's diagnostic-generation memory.

## Versions and upgrades

One version covers the whole protocol. Update Python and Lean together; old
messages, version negotiation and mixed versions are unsupported. Rebuild the
worker and restart the service and workers. Handshakes reject version mismatches.

HTTP uses `/check` and `/verify_proof`, without `/api/v1`. HTTP paths are independent
of worker versions; HTTP clients do not send `protocol_version`.
