# ADR-0003: Pull-based distribution via consumer groups

## Status
Accepted for Phase 1.

## Context
A model replica can be slower than its peers because of compute, input size or
contention. Gateway routing should not need to predict each worker's availability.

## Decision
Workers use XREADGROUP with a unique consumer name. Phase 1 reads COUNT 1 only
after finishing the preceding request. Gateways name a model stream, never a worker.

## Alternatives considered
- Push/load-balanced HTTP: mature proxies, fewer Redis hops, easy direct streaming;
  a proxy can send to a replica that is already saturated, and retry ownership
  becomes part of the gateway/proxy contract.
- Round-robin worker assignment in the gateway: predictable selection; needs an
  accurate worker registry and gives equal counts even when work costs differ.
- Least-inflight routing: responds to busy replicas; needs current shared state,
  introduces races and still requires bounded worker-side admission.

## Consequences
A busy worker naturally reads less. This is availability-based distribution,
not a fairness or equal-count guarantee. Redis becomes a shared availability and
latency dependency. Pending deliveries remain attached to dead consumers until
reclaimed; consumer groups alone do not implement a failure controller.
Batching will later require bounded prefetch to keep compute busy without hoarding
work. CPU utilization alone will not describe how much work that batcher can accept.
