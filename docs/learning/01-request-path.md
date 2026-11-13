# 01 — Follow one request

## What you'll understand

- Follow validation, enqueue, delivery, inference and reply in order.
- Explain why a waiter must exist before the request enters Redis.
- Distinguish acknowledgement from deletion.
- Explain timeout cleanup and the boundary of Phase 1 failure handling.

## The problem

A synchronous HTTP request looks like one operation to its caller. Inside this
platform, independent processes cooperate over several network operations.
Any process can pause or die between those operations. Correctness depends on
the order of those small steps, not just on the happy-path output.

Consider a worker that finishes almost immediately. If the gateway enqueues first
and registers its response destination second, the reply can arrive in between.
The inbox reader sees no waiting handler and ignores it. The caller then waits
for a response that already passed through the system. This race is possible even
when every network call succeeds.

A second ordering problem occurs at completion. If a worker acknowledges before
writing a reply, then dies, Redis considers the work handled while the caller has
nothing to receive. Understanding those windows is more useful than memorizing
a list of Redis commands.

## How it works

The gateway validates the request's shape and model allowlist, checks its API key,
and generates request and trace IDs. A request ID correlates one response with one
handler; a trace ID will support cross-component investigation. Neither contains
the input or API key.

```mermaid
sequenceDiagram
    participant H as HTTP handler
    participant I as Inbox reader
    participant R as Redis
    participant W as Worker
    H->>I: Register request waiter
    H->>R: XADD request with reply_to
    W->>R: XREADGROUP >
    W->>W: Simulated inference
    W->>R: Write final reply
    W->>R: XACK then XDEL
    R-->>I: XREAD reply
    I-->>H: Put response in waiter
    H->>H: Return JSON, then remove waiter
```

The request's reply_to field names the accepting gateway's inbox. The worker needs
no gateway network address. One inbox reader processes entries using a cursor,
the last entry ID it has seen. It puts a recognized response into a bounded queue.
Unknown IDs and already-full queues are ignored so late or duplicate replies
cannot create additional HTTP responses.

In Phase 1 the worker read one request at a time. The current worker reads
bounded free capacity and stages a dynamic batch, as lesson 02 explains. Delivery enters it in the pending-entry
list, Redis's record of unacknowledged ownership. Inference runs in a dedicated
executor. The worker writes the final reply, calls XACK to clear pending ownership,
then XDEL to remove retained work. Acknowledgement and deletion are separate:
ACK alone would let stream storage grow with completed work.

### See it concretely: the race that "register first" prevents

Both orders work almost always. Only one works always:

```mermaid
sequenceDiagram
    participant H as HTTP handler
    participant R as Redis
    participant W as Fast worker
    participant I as Inbox reader
    Note over H,I: WRONG order: enqueue, then register
    H->>R: XADD request
    R->>W: delivered at once
    W->>R: XADD reply to inbox
    R->>I: reply arrives
    I->>I: waiters has no entry for this id, so drop it
    H->>H: register waiter (too late)
    H->>H: waits until 504, although the work succeeded
```

The fix is just two lines in the right order
([app.py:212-224](../../src/infer_platform/gateway/app.py#L212)):

```python
queue = None if async_mode else router.register(request_id)  # mailbox exists first
...
outcome = await enqueue(state.redis, model, message, ...)  # then work becomes visible
```

`finally: router.unregister(request_id)` (line 254) removes the mailbox on *every*
exit path (success, timeout, disconnect, error), so the waiters dict can't leak.

### See it concretely: the actual bytes

From a captured run (full trace in the [walkthrough](WALKTHROUGH.md)). This is the stream
entry the worker receives:

```text
XADD req:fake-classifier *
  request_id  29b9aa2ab4654712b7d4002351a49d48
  reply_to    inbox:4c62a7e5bfc34b52aaeba2b746ff3167
  kind        infer
  payload     {"input":"Learning Redis Streams","options":{}}
  timeout_ms  4998
  stream      0
  key_id      4ea86e3706913256d830ebbdc45c4460938f41f17329526fb7a6fd89b24f32f3
  trace_id    ff768af660654b3196ea1fd2fb0fb93c
→ entry ID 1794594405603-0   (= Redis clock in ms, then a sequence number)
```

and this is what the worker writes back to the gateway's inbox:

```text
XADD inbox:4c62a7e5bfc34b52aaeba2b746ff3167 MAXLEN ~ 10000 *
  request_id  29b9aa2ab4654712b7d4002351a49d48
  type        final
  data        {"label": "SIMULATED", "input_chars": 22}
  meta        {"worker_id":"fd1f769d8eb8-feac25f1","batch_size":1,"queue_ms":1.12,
               "batch_wait_ms":11.21,"inference_ms":24.04,...}
```

Every field is a plain string. JSON lives *inside* `payload`, `data` and `meta`.
Nothing is pickled, and the API key itself never appears, only its SHA-256 (`key_id`).

Queue age uses Redis TIME and the request entry's timestamp. Compute duration
uses a monotonic clock, which measures elapsed time without wall-clock adjustments.
Comparing clocks in different containers would make queue measurements vulnerable
to clock skew. The shared Redis time avoids that particular error.

## How it works in this codebase

Read these functions in order:

1. [Authenticator.authenticate](../../src/infer_platform/gateway/auth.py): hash the
   supplied key and compare against every stored hash with constant-time comparison.
2. [RequestMessage.fields](../../src/infer_platform/common/schemas.py): serialize
   typed message fields as Redis strings, with JSON inside payload.
3. [create_app / infer](../../src/infer_platform/gateway/app.py): register, enqueue,
   wait with a deadline, then remove the waiter in finally.
4. [ResponseRouter.start/read](../../src/infer_platform/gateway/response_router.py):
   establish a concrete starting cursor before requests can arrive and route replies.
5. [Worker.prepare/finish](../../src/infer_platform/worker/main.py): compute queue age,
   skip expired work, run inference and write the reply before acknowledgement.

Finally read [the integration tests](../../tests/integration/test_request_path.py).
They use real Redis rather than a mock implementation of stream ownership.

## Try it

After the quickstart in [lesson 00](00-big-picture.md), run:

```bash
uv run pytest -m integration
curl --fail --silent --show-error -H "X-API-Key: $INFER_SMOKE_API_KEY" -H 'Content-Type: application/json' -d '{"input":"Learning Redis Streams"}' http://localhost:8000/v1/infer/fake-classifier
```

The tests check a successful round trip, validation and auth errors, deadline
cleanup, reply dispatch, stream removal, and expired requests. The curl response
includes a worker ID and actual batch_size and batch_wait_ms. Phase 1 used size one and zero wait;
Phase 2 uses the model batch policy, even for the fake backend.

## Break it

Run the E1 drill from lesson 00. Before each disruption, write a prediction:

- For an invalid key, should XLEN increase? It should not: no accepted request
  reaches XADD.
- For an unknown model, should a new work stream appear? The allowlist rejects it,
  so no worker is asked to load an arbitrary name.
- For a worker killed after delivery, should XPENDING return zero? It should not:
  delivery without completion leaves ownership behind.

The HTTP deadline releases the gateway waiter and returns 504. It does not
cancel the running model or remove pending work in Phase 1. Expiry at dequeue
helps avoid starting old queued work; it cannot stop compute already underway.
These distinctions explain why a timeout alone is not a complete reliability policy.

## Trade-offs and alternatives

Per-request response streams would make each reply address unique, but require
more keys and reader lifecycles. Pub/Sub could relay responses quickly, but a
disconnected subscriber can miss them. A gateway inbox reduces reader overhead
while keeping replies temporarily readable after a transient connection failure.

Response-before-ACK is still not an exactly-once transaction. A crash after the
response write but before acknowledgement leaves pending work even if the caller
has succeeded. Future reclaim can repeat inference. Avoid external side effects
inside inference and plan for duplicate responses rather than claiming exactly-once
execution. Phase 3 will add result caching, reclaim and cancellation.

## Check your understanding

1. Why register before XADD?
<details><summary>Answer</summary>A fast reply must find its waiter already present.</details>

2. What would happen if ACK came before the reply?
<details><summary>Answer</summary>A crash between the two could clear ownership without delivering any response.</details>

3. Does XACK delete an entry?
<details><summary>Answer</summary>No. XDEL removes stream storage; XACK removes group pending ownership.</details>

4. Why not compare gateway and worker wall clocks?
<details><summary>Answer</summary>Clock skew can distort cross-process durations; Redis provides the shared timestamp source.</details>

5. Does 504 mean inference never ran?
<details><summary>Answer</summary>No. It means the gateway stopped waiting. Work can still exist or finish later.</details>

## Teach-back

- Explain the fast-response race using a timeline.
- Describe the crash window between response write and ACK.
- Explain how a pending entry differs from undelivered backlog.

## Further reading

Read [ADR-0002](../adr/0002-gateway-inboxes.md),
[ADR-0003](../adr/0003-pull-distribution.md), and the measured
[E1 record](../EXPERIMENTS.md). Redis command behavior is grounded in the
[official Streams documentation](https://redis.io/docs/latest/develop/data-types/streams/),
checked during Phase 1; the experiment records the actual runtime version.
