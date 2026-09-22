# Local Lean verification architecture

## 1. Scope

Listen on loopback or a trusted network, mainly for local `lean-eval-toolkit` use:

- `GET /healthz`
- `GET /readyz`
- `POST /check`
- `POST /verify_proof`

The first environment is `lean-4.30.0`: pinned Lean, Mathlib, REPL and verifier
metaprogram. Requests cannot change dependencies.

## 2. Components

```text
lean-eval-toolkit
        |
        | POST /verify_proof
        v
+------------------------------+
| Python API / compatibility   |
| - request validation         |
| - bounded queue              |
| - timeout and retry policy   |
| - AXLE response mapping      |
+------------------------------+
        |
        v
+------------------------------+
| Worker supervisor            |
| - one in-flight / worker     |
| - crash detection            |
| - kill and respawn           |
| - max-uses/RSS recycling     |
+------------------------------+
        |
        v
+------------------------------+
| N warm Lean REPL processes   |
| - Mathlib preloaded          |
| - clean base Environment     |
| - custom verifyProof command |
+------------------------------+
```

Reuse Kimina's pool, header-cache and supervisor ideas where useful. A custom Lean
command handles semantics; Python does not interpret proof source.

## 3. Request flow

1. Validate JSON size, environment, timeout and required fields.
2. Enqueue within a fixed bound; reject overload immediately as retryable.
3. Dispatch to an idle worker, one request per worker.
4. Elaborate formal and candidate independently from the Mathlib base environment.
5. Discover new formal targets.
6. Match candidate declaration names, kinds, types and required definition values.
7. Check `sorry`, axioms, `unsafe` and banned commands.
8. Return structured Lean/tool messages, failed declarations and timings.
9. Reuse or restart workers based on use count, RSS or failure state.

Both inputs must branch from the same immutable base. Formal placeholders must not
leak into the candidate; declarations must not leak between requests.

## 4. Proposed defaults

```yaml
server:
  host: 127.0.0.1
  port: 8000
  request_body_max_bytes: 2097152

environment:
  id: lean-4.30.0
  lean_version: v4.30.0
  mathlib_revision: v4.30.0
  default_header: |-
    import Mathlib

workers:
  count: auto
  init_count: auto
  max_uses: 500
  max_rss_bytes: 6442450944
  startup_timeout_seconds: 180

requests:
  default_timeout_seconds: 120
  maximum_timeout_seconds: 300
  queue_capacity_factor: 4
  infrastructure_retries: 1

verification:
  use_def_eq: true
  permitted_sorries: []
  allowed_axioms:
    - propext
    - Quot.sound
    - Classical.choice
```

Set workers to the smaller of available physical cores and memory-supported
capacity, not logical threads. AXLE's single-machine throughput saturates near
physical core count.

## 5. Error classes

| Class | Example | Internal retry | Restart worker |
| --- | --- | --- | --- |
| Verification failure | Type mismatch, `sorry`, unapproved axiom | No | No |
| Lean source error | Parser/elaborator error | No | Usually no |
| Bad request | Unknown environment, oversized JSON | No | No |
| Timeout | Nonterminating tactic | No | Yes |
| Worker failure | EOF, crash, corrupt protocol | At most once | Yes |
| Overload | Full queue, queue timeout | Client backoff | No |

Only infrastructure failures qualify for automatic retries. Invalid Lean must not
consume extra capacity through retries.

## 6. Phases

### Phase 0: Reproducible environment

- Pin Lean/Mathlib 4.30.0 and cache Mathlib/REPL artifacts.
- Record source revision, toolchain and image digest.
- Smoke-test `import Mathlib` on miniF2F/Putnam samples.

### Phase 1: Concurrent compilation

- Add health checks, environment listing and a basic `/verify_proof` route.
- Build warm pools, bounded queues, timeout termination and replacement.
- Return compilation results to measure startup, latency, RSS and throughput;
  do not use this phase for strict grading.

### Phase 2: Strict verification

- Implement Lean's `verifyProof` command.
- Discover declarations; compare types/values and check axioms, `sorry` and `unsafe`.
- Match AXLE response fields and error classes.

### Phase 3: Toolkit integration

- Run end to end by changing only `axle.api_url` in `lean-eval-toolkit`.
- Compare AXLE-verified miniF2F/Putnam samples.
- Apply existing client retries to local service errors.

### Phase 4: Stability and capacity

- Run soak, timeout-storm, crash and OOM tests.
- Tune worker count, RSS, max uses and queue length from measurements.
- Evaluate read-only containers, network restrictions and stronger request isolation.

## 7. Acceptance

- Structured, deterministic outcomes for valid proofs, invalid proofs and timeouts.
- Reject changed statements, `sorry`, custom axioms and unsafe dependencies.
- Restore capacity after timeout/crash without disrupting other active requests.
- No cross-request declaration, option or attribute leaks.
- Match all selected AXLE golden verdicts; document differences in ADRs or known limits.
- Scale throughput toward physical core count; do not default beyond saturation.

## 8. Open measurements

- Resident memory of Mathlib-loaded Lean 4.30.0 workers.
- Whether the standard REPL suffices or needs a small fork.
- Clean-environment restoration cost and process-global state leaks.
- `use_def_eq=true` overhead and differences from AXLE.
- Suitable `max_uses` and RSS recycling thresholds.
