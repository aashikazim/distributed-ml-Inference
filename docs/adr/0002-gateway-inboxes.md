# ADR-0002: Per-gateway inbox streams

## Status
Accepted for Phase 1.

## Context
Many concurrent HTTP requests wait for independent worker responses. Workers must
reach the accepting gateway without knowing its network address.

## Decision
Create one inbox stream per gateway lifetime and one XREAD task. Dispatch by
request_id into bounded per-request queues. Register before enqueue, start the
reader from a concrete cursor, and ignore unknown responses. Refresh expiry and
trim approximately to 10000 entries. Delete the inbox on clean shutdown.

## Alternatives considered
- Per-request streams: isolate replies naturally; create a key and reader lifecycle
  for every request, increasing bookkeeping and connection pressure.
- Pub/Sub: efficient fan-out and low latency; a reconnect can lose a final response.
- Sticky routing: works with connection-oriented direct replies; couples gateway
  placement and worker routing and complicates horizontal scaling.

## Consequences
A single reader amortizes Redis reads and connections but must be supervised.
/readyz checks its task and connectivity. Cursor-based reads survive transient
disconnects while the stream survives. The stream is temporary, not an indefinite
result store. A gateway crash still loses waiting HTTP connections; Phase 3 adds
a TTL result cache. Reply-before-ACK allows duplicate replies; dispatch tolerates
them but does not provide exactly-once execution.
