# ADR-0005: At-least-once delivery, reclaim and quarantine

## Status
Accepted in Phase 3.

## Context
A delivered Redis Streams entry remains pending until acknowledged. A process
can die during inference or between network operations. HTTP connections and
worker processes cannot form an exactly-once transaction.

## Decision
Use consumer group workers and unique consumer names. After warmup, register
worker:{model}:{worker_id} with a TTL and refresh it independently of compute.
One consumer loop alternates bounded fresh XREADGROUP reads and XAUTOCLAIM scans.
This single producer shares one capacity budget, preventing a concurrent reclaim
reader from overfilling the batcher. Keep XAUTOCLAIM's cursor and use extended
XPENDING for the actual delivery count after ownership transfer.

Reclaim only entries idle beyond reclaim_idle_ms (default 30000). That threshold
must exceed the slowest expected buffered inference and reply time; it is not a
heartbeat timeout. A live but slow consumer can otherwise be reclaimed. Do not
schedule the same entry twice inside one worker while it is already active.

Publish in this order: XADD reply, refresh inbox expiry, SET result cache with
TTL, then a MULTI/EXEC transaction containing XACK and XDEL. The transaction
prevents an ACKed-but-retained entry from permanently consuming queue admission
capacity. It does not make inference or HTTP delivery exactly once.

Before compute, check Redis age, cancel flag, schema and character limits.
After compute, check deadline/cancel again so a late CPU result is discarded.
CPU executor threads cannot be interrupted safely. A cached terminal outcome
can be replayed without inference. Conditional RUNNING updates never overwrite
a final cache record; concurrent executions can still race to publish finals.

Within one delivery, the batcher retries failed items individually once. If
inference still fails, record last_error and leave the stream entry pending.
On a claim whose delivery_count > max_deliveries, append the original envelope,
last error and delivery count to dlq:{model}, publish a DEAD_LETTERED outcome,
then ACK/delete. Malformed envelopes follow the same bounded delivery policy;
send an error only when request_id and an inbox reply address can be salvaged.
An already-expired valid request may finish as EXPIRED before its retry limit.

## Alternatives considered
- ACK on receipt: less pending bookkeeping; permanently loses work if a worker dies.
- Never retry: simple bounded work, but transient worker failures become client errors.
- Exactly-once inference: requires stronger transactional execution semantics than
  model execution, Redis and HTTP provide. Idempotent inference is a better fit.
- Immediate retry loops: fast recovery for short failures; can amplify overload and
  repeatedly burn compute on poison. Reclaim introduces a bounded retry interval.
- One shared consumer name: fewer names, but hides ownership and makes diagnosis
  and graceful lifecycle tracking harder.
- Reclaim in a separate reader task: easy scheduling, but needs capacity reservation
  shared with the fresh reader. One producer avoids that race.

## Consequences
SIGKILL leaves pending ownership; another worker can recover before the request
deadline. Recovery time includes idle age, scan interval, local capacity and
inference. Heartbeats describe live capacity; they do not transfer work.

SIGTERM stops reads, drains locally accepted work and responses, removes the
heartbeat and exits. Compose gives workers 15 seconds. Forced termination or
Redis failure can still leave work pending for recovery. Kubernetes termination
grace and probes are Phase 7 work.

Results and pending status share result:{request_id}, a JSON string with TTL.
Polling requires the same hashed API-key identity. Gateway timeouts/disconnects
set TTL cancellation flags best effort. Async acceptance intentionally survives
the caller disconnecting after the 202 response.

Duplicates remain possible, including duplicate replies or DLQ entries around
a crash. No external side effects belong inside inference. DLQ payloads contain
original inputs for diagnosis, so Redis access and retention must be controlled;
application logs still exclude inputs. DLQ MAXLEN ~10000 bounds retention and
can discard older quarantine evidence. There is no automatic DLQ replay API.
Redis AOF on one machine is not Redis HA or a guarantee against all lost writes.
