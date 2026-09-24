# ADR-0007: Bounded queues, retries and capacity

- Status: Proposed
- Date: 2026-09-18

## Context

Evaluation submits bursts of proofs. Unbounded tasks increase memory use, file
descriptors and tail latency. AXLE's throughput saturates near physical core count.

## Decision

- One in-flight request per worker.
- A bounded FIFO queue, initially `worker_count * 4`.
- Reject full queues immediately; clients use exponential backoff.
- Record queue wait and execution time separately.
- Cap caller timeouts on the server.
- Retry only crashes, EOF and protocol failures, at most once on a new worker.
- Do not retry Lean errors, verification failures, bad requests or deterministic timeouts.

Initial worker count:

```text
min(
  available physical CPU cores,
  floor(memory budget / measured peak RSS per worker)
)
```

Reserve at least one core and enough memory for the API, callers and OS. Tune on
the target machine.

## Metrics

- Queue depth, wait time and rejections.
- Active, ready and restarting workers.
- Total latency and formal/candidate/verification timings.
- Timeouts, crashes, OOMs and internal retries.
- Request count and RSS per worker.
- Counts by verdict.

## Consequences

Bounded queues provide backpressure. Match client concurrency to capacity; extra
concurrency does not raise throughput. Local single-tenant use needs no API-key
fair scheduling.
