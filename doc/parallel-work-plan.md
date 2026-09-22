# Lean Server parallel work plan

## Shared boundary

Both tasks use the NDJSON protocol in `protocol/`. Shared interfaces:

- `protocol/**`
- `src/lean_server/protocol.py`
- `src/lean_server/backend.py`
- `tests/contract/**`

Protocol changes must update Lean, Python decoding, schemas and contract tests
together. One `protocol_version` covers all operations; `type` selects compilation
or verification. Deploy Python and Lean together; see `protocol/README.md`.

## Task A: Lean worker

### Owned files

- `lean/**`
- `tests/lean/**`
- Required Lake executable target configuration.

Do not change Python HTTP, queues, pools, supervisors or root Python configuration.

### Deliverables

1. A persistent `lean-server-worker` executable.
2. Preload Lean/Mathlib 4.30.0 and emit `ready`.
3. Read `compile` and emit matching `result`, one message per line.
4. Start each request from a clean base; prevent declaration, option, attribute and notation leaks.
5. Produce diagnostics and source positions from Lean's structured messages.
6. Keep logs on stderr and stdout protocol-only.
7. Add protocol golden tests, isolation tests and at least 100 sequential requests.
8. Record cold-start, first-request and warm-request timings.

### Acceptance

- Correct `import Mathlib` code returns `ok`.
- Parser, elaborator and type errors return `compile_error` without exiting.
- Warnings do not fail compilation.
- Requests cannot access prior declarations.
- Output passes the decoder in `tests/contract`.

## Task B: Scheduling and HTTP

### Owned files

- `src/lean_server/service/**`
- `src/lean_server/workers/**`
- `src/lean_server/http.py`, `src/lean_server/__main__.py`
- `tests/service/**`

Do not change `lean/**`. Use a protocol-compatible fake worker for development
and CI, independent of task A.

### Deliverables

1. NDJSON subprocess transport implementing `CompilerBackend`.
2. A fixed pool with one in-flight request per worker.
3. A bounded FIFO queue with immediate overload responses.
4. Separate queue, compile and total timings.
5. Kill the process group on timeout and restore capacity.
6. Replace on EOF, invalid JSON or wrong request ID; do not retry Lean errors.
7. Graceful shutdown: stop serving, drain or cancel work and reap processes.
8. `/check` and `/verify_proof` routes with unchanged request/response formats.

### Acceptance

- Fake-worker tests cover concurrency, FIFO, overload, timeout, crash and replacement.
- One crash does not affect other workers' active requests.
- Capacity returns to its configured value after stress tests.
- HTTP handlers do not manage Lean subprocesses directly.

## Merge order

1. Merge the shared protocol and task split.
2. Branch A and B from that commit.
3. Merge either first; B only needs the executable path after A lands.
4. Add real worker × pool × HTTP integration tests in a separate final commit.

## Files requiring coordination

- A may add a target to `lakefile.toml`, without reformatting; B must not edit it.
- Update root `README.md` only in the final integration commit.
- B owns dependency changes in `pyproject.toml` and `uv.lock`; A must not edit them.
