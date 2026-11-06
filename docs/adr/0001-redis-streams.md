# ADR-0001: Redis Streams as the work queue

## Status
Accepted for Phase 1.

## Context
HTTP handlers and model execution have different lifetimes and speeds. We need
to inspect undelivered and in-flight work, distribute requests, and later recover
requests from dead workers. Redis is the specified infrastructure dependency.

## Decision
Use one req:{model} stream and a workers consumer group. Create the group at 0,
so requests submitted before worker startup are eligible. Use JSON fields with
Pydantic validation. Write the response before XACK, then XDEL completed work.

## Alternatives considered
- Lists: simple and cheap; reliable processing requires a separate processing list
  and ownership bookkeeping that Streams already provides.
- Pub/Sub: low overhead notifications; disconnected subscribers lose messages and
  there is no pending-entry ledger.
- Kafka: durable partitioned log and replay; significantly more infrastructure,
  with partition ownership and retention semantics beyond this learning scope.
- RabbitMQ: mature acknowledgements and routing; adds a forbidden dependency.
- Direct gRPC: fewer queue hops and typed RPC; gateway must discover replicas,
  handle busy destinations, and own retries when a connection dies.

## Consequences
The pending-entry list makes failure visible, but is not automatic recovery.
Phase 1 deliberately demonstrates that gap; Phase 3 will reclaim idle deliveries.
Redis durability depends on persistence and availability configuration. AOF on a
local volume is not a high-availability guarantee. Stream length includes delivered
entries until deletion, so it is not the same as undelivered backlog.
