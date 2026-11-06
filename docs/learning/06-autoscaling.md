# Lesson 06: teach the controller to wait before it shrinks

## 1. What you'll understand

- Read backlog, head age and busy fraction as different serving signals.
- Explain the pure decision function without Redis or Kubernetes knowledge.
- Predict the effects of cooldown, stabilization and model warmup.
- Distinguish simulated policy evidence from actual replica changes.

## 2. The problem

Fixed capacity must either reserve enough workers for peak traffic or let bursts
wait. Adding a worker after every slow request can create a second problem:
workers start slowly, traffic changes before they become ready, and the controller
removes capacity just before another burst. Repeated expansion and contraction
is called flapping. It wastes startup work and makes latency harder to predict.

A thermostat that ignores the time needed to heat a room will overshoot. Our
controller similarly needs feedback plus memory, rather than reacting to one
instantaneous sample. We build that memory explicitly so you can inspect it.

## 3. How it works

The policy converts backlog, busy fraction and waiting age into separate replica
recommendations, then chooses the largest. A maximum bound prevents unbounded
resource requests; a minimum keeps one worker alive. Growth is limited per cycle.
A **cooldown** delays repeated changes; a **stabilization window** remembers high
recent demand before allowing contraction.

```mermaid
flowchart LR
    R[Redis queue and heartbeats] --> S[Observed state]
    K[Deployment replica spec] --> S
    S --> D[Pure decide with history]
    D --> B[Bounds, cooldown, stabilization]
    B --> A[Dry-run or Kubernetes scale adapter]
    A --> W[Workers load and warm up]
    W --> R
```

History contains raw demand recommendations, not already stabilized outputs.
Otherwise a high value would continually renew itself and never expire. Old high
demand can delay a downscale, but must not independently cause a new scale-up.

A Redis ownership token prevents ordinary simultaneous controllers from acting.
Its lease refreshes independently and is checked before writes. This is useful
coordination, not a mathematical guarantee against a paused process completing
an already-issued API operation after its lease expires.

## 4. How it works in this codebase

Read these files in order:

1. [policy.py](../../src/infer_platform/autoscaler/policy.py), `decide`: pure state,
   policy and history values; no network or wall-clock lookup.
2. [test_autoscaler.py](../../tests/unit/test_autoscaler.py): table-driven examples,
   cooldown, history decay and a fake Kubernetes API.
3. [autoscale_sim.py](../../tools/autoscale_sim.py), `simulate`: synthetic arrivals,
   delayed ready capacity, queue service and replica-second accounting.
4. [loop.py](../../src/infer_platform/autoscaler/loop.py), `Controller`: real Redis
   snapshots, leadership renewal, decisions and metrics.
5. [scalers](../../src/infer_platform/autoscaler/scalers): dry-run logs and official
   Kubernetes scale-subresource calls in threads with timeouts.

The Kubernetes deployment map explicitly identifies managed workloads. Unmapped
models are excluded from actual Kubernetes actuation. Dry-run reports observed
workers as current and never pretends its proposals changed Compose.

## 5. Try it

Run the policy checks and generate all four traces plus E8–E10 summaries:

```bash
export UV_CACHE_DIR=/tmp/infer-uv-cache
uv run --extra ml pytest tests/unit/test_autoscaler.py tests/sim tests/integration/test_autoscaler_lock.py -q
uv run python -m tools.autoscale_sim --output docs/results/autoscale-sim.json
```

The JSON includes assumptions, time series, prediction and measured simulation
summaries; its PNG plots backlog and desired versus ready replicas. All inference
capacity there is simulated, not benchmarked. The steady trace should keep one
replica, while the spike creates a temporary queue and delayed added capacity.

For the real Redis dry-run, start the monitored stack and send a short burst:

```bash
export INFER_API_KEYS='["phase1-verification-key"]'
docker compose -f deploy/docker/docker-compose.yml --profile fake-llm up --build -d --wait
uv run python -m tools.autoscale_dryrun_probe
```

The Autoscaler dashboard now shows current, desired and decisions alongside
backlog. A recommendation above current is expected; Compose remains unchanged.
The saved probe identifies actual_scaling=false explicitly.

## 6. Break it

The first command above contains three controlled breaks. The sawtooth test
removes stabilization and cooldowns to expose repeated direction reversals;
compare E9's retained-capacity cost before calling the stable policy free.
The lock test installs another owner's token and verifies that the old controller
cannot renew or act. The fake Kubernetes test checks the scale subresource and
network timeout arguments without requiring cluster credentials.

For a third prediction exercise, inspect E10's backlog-target sweep before reading
its results. Will every target change the outcome? Other signals and step limits
can dominate it. This is why changing one knob need not improve a dashboard.

## 7. Trade-offs and alternatives

A slow downscale reserves capacity while traffic is quiet. Fast scale-up can still
arrive too late for a short deadline because weights and warmup take time.
Busy fraction can also mislead while some desired replicas are not ready.
These are reasons to keep admission and deadlines, not remove them after adding
a controller.

HPA provides established Kubernetes scaling behavior when custom metrics are
available. KEDA provides existing Redis Streams integration. We implement a
custom loop for learning and signal transparency, not because it is automatically
better in production. The [comparison](../concepts/autoscaling.md) cites the current
KEDA docs and explains pending entries versus lag versus retained length.

## 8. Check your understanding

1. Why not use only CPU utilization?
<details><summary>Answer</summary>GPU activity, batching and warmup can decouple CPU use from serving saturation.</details>

2. What happens if stabilized output is saved as new raw history?
<details><summary>Answer</summary>A high recommendation can refresh itself forever, preventing downscale.</details>

3. Does desired=4 mean four workers can accept work now?
<details><summary>Answer</summary>No. Startup, scheduling and warmup delay accepting heartbeats.</details>

4. Why do E10 targets sometimes give the same curve?
<details><summary>Answer</summary>The maximum of other signals or step limits may dominate the backlog recommendation.</details>

5. Does dry-run demonstrate Kubernetes scaling?
<details><summary>Answer</summary>No. It demonstrates decisions against real Redis; Phase 7 separately verifies actual Deployment changes.</details>

## 9. Teach-back

- Explain why slow contraction and fast expansion have different costs.
- Walk from a Redis snapshot through decide to a bounded scale operation.
- Explain which simulation assumptions prevent treating its results as GPU capacity.

## 10. Further reading

Read [autoscaling concepts](../concepts/autoscaling.md), [ADR-0009](../adr/0009-autoscaling.md),
[HPA documentation](https://kubernetes.io/docs/concepts/workloads/autoscaling/horizontal-pod-autoscale/)
and [KEDA 2.21 Redis Streams](https://keda.sh/docs/2.21/scalers/redis-streams/).
