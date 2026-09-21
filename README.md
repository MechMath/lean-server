# Lean Server

A local HTTP service for Lean 4.30.0 and Mathlib 4.30.0, with persistent workers
for compilation checks and strict proof verification (an AXLE-compatible subset).

## Quick start

Requires Python 3.12+, [uv](https://docs.astral.sh/uv/) and
[elan](https://github.com/leanprover/elan). Run from the repository root:

```bash
elan toolchain install leanprover/lean4:v4.30.0
lake update
lake exe cache get
lake build lean-server-worker
uv sync
uv run lean-server --host 127.0.0.1 --port 8000
```

The server checks the pinned environment and preloads Mathlib before listening.
Use `--workers 4 --queue-capacity 8` to configure concurrency.

## Usage

Check readiness:

```bash
curl http://127.0.0.1:8000/readyz
```

Compile Lean code:

```bash
curl http://127.0.0.1:8000/api/v1/check \
  -H 'Content-Type: application/json' \
  -d '{"code":"import Mathlib\nexample : 1 + 1 = 2 := by norm_num"}'
```

The response includes `okay`, `warnings`, `errors` and timing information.
Lean errors return HTTP 200 with `okay: false`. `sorry` is rejected unless
`allow_sorry: true` is supplied. Optional `timeout_seconds` defaults to 30 (maximum 120)
and covers both queue wait and execution. Expired queued requests release their queue slot.
Worker failures return HTTP 503; respect `retryable` (known deterministic Lean
panics return `error_type: "LeanPanic"`, `retryable: false`).

Verify a proof against a statement:

```bash
curl http://127.0.0.1:8000/api/v1/verify_proof \
  -H 'Content-Type: application/json' \
  -d '{"formal_statement":"theorem answer : True := by sorry","content":"theorem answer : True := True.intro","environment":"lean-4.30.0"}'
```

Strict verification checks declaration names and types, preserves fixed definitions,
and rejects `sorry`, unsafe dependencies and nonstandard axioms. The default and
maximum timeout are 600 seconds including queue wait; set `timeout_seconds`
to use a shorter budget. Unsupported AXLE options return HTTP 400.
Fixed definitions also include their generated implementation dependencies, such as
recursive helpers. These must match; theorem proof bodies may differ.

## Local notebook

With the server running, open the local `demo.ipynb`:

```bash
uv run --with jupyterlab --with ipykernel jupyter lab demo.ipynb
```

Run the cells in order to explore checks, diagnostics and proof verification.
Notebooks and their checkpoints are ignored by Git and are not included in clones.

See [doc/](doc/README.md) for design details. Run Python tests with
`uv run python -m unittest discover -s tests -v`.
