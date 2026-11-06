# Autoscaling as delayed feedback

A controller repeatedly observes a system, chooses a desired state and asks an
actuator to move toward it. A thermostat is a useful analogy, but worker replicas
have much longer startup delays: container pull, weight download, model load,
warmup and readiness. A scale instruction is not immediately usable capacity.

## Signals and policy

Our input is undelivered backlog, age of its oldest entry, accepting workers and
mean worker busy fraction. The Kubernetes deployment spec provides current desired
replicas; heartbeats separately show accepting capacity. The maximum of three
recommendations protects against disagreement: backlog divided by its target,
current replicas multiplied by busy/target, and one extra replica when head age
exceeds its budget. Clamp to allowed bounds before applying bounded step changes.

CPU is a poor proxy for batched inference demand: a GPU can be occupied while the
host waits on device work; large batches increase throughput without proportionate
CPU utilization; model loading creates CPU spikes unrelated to steady demand.
Busy time is also imperfect: LLM HTTP wait is upstream activity, not GPU occupancy.
Always compare ready workers, queue age, service throughput and engine metrics.

**Hysteresis** means avoiding reversal at every small signal movement. Our
cooldowns enforce minimum spacing between changes. **Stabilization** retains
recent high demand before downscaling. This costs replica-seconds but avoids
repeated cold starts. Saving the stabilized output as history would be a bug:
it would refresh its own high value and prevent downscaling indefinitely.

A new leader lacks prior history, so it temporarily retains current capacity.
Missing Redis observations fail closed rather than issuing a downscale from
fabricated zero values. The lease is token-owned and renewed independently;
a long pause or API operation can still defeat lease-only mutual exclusion.
This learning controller is not an exactly-once distributed actuator.

## HPA and KEDA comparison, verified 2026-11-06

| Choice | Strength | Cost or mismatch |
|---|---|---|
| Custom controller here | Transparent pure policy combining serving signals; dry-run and deterministic traces | We maintain leadership, history, failures and Kubernetes integration |
| Kubernetes HPA with custom/external metrics | Standard reconciliation and configurable scaling behavior | Needs a metrics adapter and correct queue/busy metric semantics |
| KEDA Redis Streams | Existing event-source integration and authentication | Must choose pending, retained length or consumer-group lag deliberately |

HPA relates observed metric values to their targets and current replicas, and
supports scale policies and stabilization. Prefer it when a trustworthy custom
metric is already available through the Kubernetes metrics APIs. We do not deploy
HPA alongside this controller because competing writers would fight over replicas.
Official reference: [Horizontal Pod Autoscaling](https://kubernetes.io/docs/concepts/workloads/autoscaling/horizontal-pod-autoscale/).

KEDA's current **2.21** Redis Streams reference describes three signal modes:
`pendingEntriesCount` concerns a group's pending deliveries, `streamLength`
uses retained length, and `lagCount` uses consumer-group lag. Lag mode requires
Redis 7+; its example includes `consumerGroup` and `activationLagCount`.
These are different populations in our ACK/delete design. Pending excludes
undelivered waiting; retained length includes active work. Choose intentionally,
not because one parameter sounds like queue depth. Verified against
[KEDA 2.21 Redis Streams](https://keda.sh/docs/2.21/scalers/redis-streams/).
We keep minimum replicas at one regardless of other tools' scale-to-zero support.
Prefer KEDA in production when its established scaler covers your requirements
and reduces custom control-plane maintenance. No KEDA installation is claimed.

## Little's Law and cold starts

For a stable queue, average backlog = arrival throughput × average queue wait.
This helps choose a starting backlog budget from a measured wait budget, but
is not a proof that bursts or cold starts will meet it. A target per replica is
a policy knob; it is not a measured model capacity. Too loose a target can wait
for deadlines before adding capacity; too tight a target can reserve many idle
workers. Queue-age and utilization signals may dominate that knob entirely.

## Simulations and actual actuation

The harness uses a fluid queue, one-second steps, five-second control cycles,
20 simulated requests/s per ready replica and a five-second warmup. No Redis,
HTTP, real model, deadlines or admission exist inside that model. It answers
whether a policy oscillates and how assumed delay changes waiting, not how fast
a GPU runs. E8–E10 are explicitly **simulated** measurements.

Dry-run in Compose reads real Redis but never changes replicas. The official-client
adapter is fake-client-tested in Phase 6. Phase 7's kind experiments are the
separate evidence for real Deployment changes and ready-pod delays. See
[lesson 06](../learning/06-autoscaling.md), [ADR-0009](../adr/0009-autoscaling.md)
and [experiment evidence](../EXPERIMENTS.md).
