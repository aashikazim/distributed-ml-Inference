# ADR-0013: Cost-aware batching and the cached worker directory

## Status
Accepted in Phase D (generic serving).

## Context
**Batching.** `max_batch_size` counts items, but real cost depends on the items.
With dynamic padding, a batch's tensor is `items × longest item`. One 512-token text
makes fifteen 10-token neighbours cost 512 tokens each. Images resized to a fixed
size cost the same; ONNX tensors cost their element count.

**Admission.** Every request ran `SCAN worker:{model}:*` plus a GET per heartbeat
before the admission script. The cost grows with keyspace and worker count, and it sits on
the hot path of every request.

## Decision
**Cost budget.** Optional per-model `max_batch_cost` plus `batch_cost_mode`:
`padded` (default: `count × max(cost)`) or `sum`. An item's cost is computed once
at accept time, from the wire body, by the runner's optional `cost()`: estimated
tokens for text runners, element count for ONNX, 1 per image. Without one, the cost is
the payload size in bytes. The batcher closes a batch when adding the next item would exceed
the budget; that item leads the next batch. The **first item always fits**, so an
oversized item runs alone instead of blocking forever. Without `max_batch_cost`,
behaviour is exactly as before (ADR-0004).

**Worker directory.** Each gateway caches the list of live heartbeat keys per model for
`INFER_WORKER_CACHE_S` (1 s). The admission script still checks `EXISTS` on those keys
atomically, so a stale cache can only cause a false `NO_WORKERS`, never a false
acceptance. On `NO_WORKERS` from a cached list, the gateway rescans once and retries.

## Alternatives considered
- **Length bucketing** (separate queues per length range): better padding efficiency
  under heavy mixed traffic, but more queues and complex fairness. The cost budget is a
  smaller step in the same direction.
- **A registry set (`workers:{model}` sorted set by expiry):** O(1) discovery without
  SCAN, but adds writes to every heartbeat and a cleanup path. The cache reached the same
  hot-path goal without a key-schema change.
- **Redis Cluster hash tags** (`req:{model}`, `result:{model}:{id}`, ...): needed because
  the admission script touches keys from different slots. Deferred: it renames every key,
  and AGENTS.md requires an explicit decision for key-schema changes. Recorded as
  future work.

## Consequences
- A model that sets `max_batch_cost` too low degenerates to batch size 1. One that sets
  it too high behaves like plain size-based batching. Tune it like `max_batch_size`, by measurement.
- Cost estimates are heuristics (≈ 4 characters per token). Precise token counts
  would need tokenizing twice.
- A worker that starts takes up to `INFER_WORKER_CACHE_S` to be *preferred*, but the
  forced rescan means a model with no cached live workers never waits for the cache to expire.
