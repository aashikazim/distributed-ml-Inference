# ADR-0004: Work-conserving dynamic batching

## Status
Accepted in Phase 2.

## Context
A fixed batch requires clients to coordinate inputs, while immediate single-input
execution wastes opportunities to share model overhead. Waiting indefinitely to
fill a batch gives poor latency at low load. Closing a partial batch at a fixed
timer while the runner is busy wastes capacity: later arrivals could still join it.

## Decision
Start the deadline with the first locally accepted item, not with an empty queue.
Stage up to max_batch_size. Dispatch when full or when the deadline has passed
and the runner is available. While the runner is busy, keep filling the next
batch even after that deadline. Stage at most one next batch and execute at most
one model call at a time in a dedicated executor.

Bound active, staged and queued work together by prefetch_bound (default twice
max_batch_size). This is stricter than bounding only the queue and prevents
hoarding work across a collector and an executor. Redis reads use free capacity.
An executor task includes eligibility checks and response fan-out; slow Redis
reply writes can therefore delay the next compute batch. We choose simple,
bounded ownership now rather than another response-output buffer.

Before compute, validate each input and check expiry and cancellation. Fail
rejected items independently. If a batch invocation fails, retry each surviving
input individually exactly once. A persistent failure becomes INFERENCE_FAILED.
This is poison isolation, not transport redelivery or a DLQ.

## Alternatives considered
- Static client-supplied batches: lower scheduling complexity, but requires clients
  to coordinate and does not combine independent HTTP requests.
- Immediate singles: minimizes intentional wait at low load, but increases per-item
  execution overhead; at high load natural aggregation may still occur.
- Strict timer closure while busy: simple time windows, but freezes empty seats
  without getting results any sooner.
- Always wait for full: efficient at sustained load, but can strand isolated work.
- Recursive bisection: may isolate large poison batches with fewer calls when
  failures are rare; has more retry bookkeeping. One bounded individual pass is
  simpler and puts a clear upper bound on retries.
- Concurrent model executor threads: possible model parallelism, but introduces
  contention and complicates per-replica capacity and CPU-thread sizing.

## Consequences
max_wait_ms bounds deliberate coalescing while the runner is available. It is
not an end-to-end latency guarantee: compute, local backlog and response writes
can extend waiting. A deadline already elapsed while busy causes immediate
dispatch once the runner becomes available; the timer is not restarted.

Padding grows with the longest tokenized input (up to 256 tokens). Larger batches
can improve throughput but increase batch latency, memory, and wasted padding.
Benchmark tuning must use representative input lengths and the actual hardware.

Batch metadata preserves identity and reports actual surviving batch size.
After poison isolation it reports single-item retry size. inference_ms includes
the failed shared attempt plus that item's retry; preceding retries contribute
to the item's wait. Rejected items report batch_size=0, inference_ms=0.

Shutdown stops reads and drains accepted work; forced cancellation can still
leave pending entries. Heartbeats, reclaim, gateway-driven cancellation, DLQ
and the full graceful-shutdown contract remain Phase 3.
