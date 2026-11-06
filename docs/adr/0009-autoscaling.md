# ADR-0009: explicit learning controller with replaceable actuators

## Status
Accepted in Phase 6. Real Kubernetes actuation is verified in Phase 7; the adapter
is unit-tested here against a fake official-client-shaped API.

## Context
CPU is not a reliable representation of batched/GPU service saturation. Queue
pressure, worker busy time and waiting age describe the serving boundary more
directly. We want to learn stabilization and delayed feedback without hiding the
policy inside an infrastructure controller.

## Decision
Keep decide(state, policy, history, now) pure. Use the maximum of backlog-per-replica,
busy-ratio and queue-age signals. Clamp to min/max, limit growth to at most doubling
and max_up_step, apply cooldowns, and retain the maximum raw recommendation across
a downscale stabilization window. Store raw recommendations, not stabilized
outputs: retaining outputs would keep a high recommendation alive forever.
Old demand can prevent a downscale but cannot itself trigger a new scale-up.

Use injectable Clock, Scaler and Redis client. DryRunScaler logs proposals without
pretending Compose changed. KubernetesScaler reads/patches deployments/scale via
the official client in executor threads with explicit network timeouts. Deployment
mapping is explicit, avoiding accidental writes to unrelated workloads.

A Redis NX lease with unique token is renewed independently and checked before
each actuation. Lua renew/release checks token ownership. Leadership loss stops
the controller. On election, seed history with current replicas to avoid immediate
downscale without a demand history. Lease ownership is not Kubernetes fencing:
a delayed already-issued API call can still complete after lease loss. A robust
production controller may require a stronger leader-election/fencing design.

## Alternatives considered
- HPA with custom/external metrics: mature Kubernetes reconciliation, policies and
  stabilization, but requires exposing the queue signal through an adapter.
- KEDA Redis Streams scaler: ready-made queue integration and authentication,
  reducing maintenance; its chosen queue signal must match delivery semantics.
- Fixed replicas: easiest to operate for predictable load, but reserves peak
  capacity continuously and does not teach feedback behavior.
- CPU HPA: available on most clusters, but CPU can be low with GPU saturation or
  high during warmup rather than sustained inference pressure.

## Consequences
We own controller correctness and its operational failure modes. Bounds and slow
downscale trade spare capacity for fewer reversals. Simulations isolate policy
behavior but simplify service time and cold starts; they cannot predict actual
GPU throughput. KEDA/HPA remain sensible production defaults when their signals
and lifecycle fit. See the [verified comparison](../concepts/autoscaling.md).
