# Known issue: large NDJSON worker responses exceed the asyncio stream limit

## Status

- Active follow-up is tracked in [GitHub issue #8](https://github.com/MechMath/lean-server/issues/8).
- Resource-boundary follow-up: responses above 8 MiB now return a typed,
  non-retryable `WorkerMessageTooLarge` error. Bounded draining through the
  newline preserves a healthy worker; truncated or stalled output requires
  replacement. See the [current byte and drain limits](../../../protocol/README.md#response-size-limits).
- Updated on 2026-09-21: stdout now has an explicit 8 MiB limit; stderr is
  drained independently in chunks with a bounded 64 KiB tail. Real replay
  also found 197,802 identical diagnostics at the same location in one result
  (27,494,706 worker-response bytes). The compiler now deduplicates by severity,
  text, filename, and both positions, preserving distinct errors. That record
  returns HTTP 200 with one compile error instead of a transport failure.
  See [219 validation](219-validation-2026-09-21.md) for the replay outcome.
- Confirmed on 2026-09-18.
- Affects the persistent `WorkerProcessBackend`.
- Historically, this returned retryable HTTP `503` and replaced the worker;
  it never classified the submitted Lean program as a compile failure.
- Reproduced at full-dataset scale: 3,057 of 51,336 requests ended with this
  infrastructure failure in the latest local result for their UUID.

## Reproduction context

The server was running Lean 4.30.0 on `127.0.0.1:18000` with:

- 32 workers;
- queue capacity 64;
- the persistent NDJSON worker backend.

A batch of converted `formalmathatepfl/reasoning_data` candidates was submitted
to `POST /api/v1/check` with `allow_sorry: false`, a 120-second Lean timeout,
and client concurrency 16. The server was healthy and the request concurrency
was below both the worker count and queue capacity.

Some requests repeatedly returned HTTP 503 with one of these bodies:

```json
{"error":"Separator is found, but chunk is longer than limit","retryable":true}
```

```json
{"error":"Separator is not found, and chunk exceed the limit","retryable":true}
```

In a 300-record run, 21 records remained unresolved for this reason. Retrying
the same candidate does not help because the failure is deterministic for the
worker response size. The worker pool's `replacements` count also increased
substantially during the run.

The subsequent full run attempted all 51,336 unique records and produced these
two exact error variants:

| Error text | Latest affected UUIDs |
| --- | ---: |
| `Separator is not found, and chunk exceed the limit` | 1,652 |
| `Separator is found, but chunk is longer than limit` | 1,405 |
| **Total** | **3,057** |

Cross-checking the same UUIDs against remote AXLE showed that 487 of these
records passed AXLE and 2,570 failed AXLE. Therefore neither the error itself
nor the large diagnostic that triggers it is a sound pass/fail signal. These
records must remain in an infrastructure-error state until the transport is
fixed and they are rerun.

## Cause

`WorkerProcessBackend.start()` creates the subprocess with
`asyncio.create_subprocess_exec(..., stdout=asyncio.subprocess.PIPE, ...)`.
Python therefore constructs the stdout `StreamReader` with its default limit,
which is normally 64 KiB.

`WorkerProcessBackend._read_message()` then calls:

```python
line = await process.stdout.readline()
```

Protocol v1 represents an entire worker response as one NDJSON line. A large
Lean diagnostic can make the encoded result line exceed the reader limit.
`readline()` then raises a `ValueError` derived from `LimitOverrunError`, with
one of the messages above. The pool treats this as a worker infrastructure
failure, replaces the worker, and returns retryable HTTP 503.

This is not queue overflow: it reproduces with client concurrency below the
number of ready workers and with `queue_depth` at zero.

## Impact

- Large compiler diagnostics cannot be returned through the persistent worker
  protocol.
- Retrying consumes work and repeatedly replaces healthy worker processes.
- A batch validator must not treat these 503 responses as invalid Lean code,
  so affected records remain unresolved indefinitely.
- Raising queue capacity or reducing client concurrency does not fix the
  deterministic response-size failure.

## Suggested fix

Pass an explicit, substantially larger stream limit when creating persistent
worker subprocesses, for example a value aligned with the HTTP request/response
size policy:

```python
self._process = await asyncio.create_subprocess_exec(
    *self.command,
    stdin=asyncio.subprocess.PIPE,
    stdout=asyncio.subprocess.PIPE,
    stderr=asyncio.subprocess.PIPE,
    limit=2 * 1024 * 1024,
    ...,
)
```

The exact limit should be centralized as a named protocol constant. It should
cover both stdout result messages and stderr draining, or the protocol should
move away from unbounded single-line framing (for example, explicit length
framing).

The server should also catch `LimitOverrunError`/the resulting `ValueError` and
return a stable infrastructure error rather than leaking the Python exception
text.

## Regression tests

Add tests that exercise the real persistent subprocess transport:

1. A worker result whose serialized NDJSON line is slightly larger than
   64 KiB is decoded successfully.
2. A result up to the selected protocol maximum succeeds.
3. A result above the selected maximum returns a deliberate protocol-size
   error without an uncontrolled replacement loop.
4. Repeating a large Lean diagnostic does not increase the replacement count.
5. Concurrent requests below `worker_count` do not return queue-overflow 503s.

HTTP 429/5xx and transport exceptions are not proof verdicts. Clients should
respect `retryable`; a known deterministic Lean panic is nonterminal but must
not be retried unchanged automatically. An HTTP 200 response with `okay: false`
is a validation rejection (inspect diagnostics to distinguish a bad reference,
candidate error, or verifier policy).
