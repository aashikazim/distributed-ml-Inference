# Runbook: observe, isolate, recover, verify

Use this with [dashboards](dashboards.md), [configuration](CONFIG.md) and
[architecture](ARCHITECTURE.md). Identify the environment first: Compose uses
localhost:8000; the dedicated kind context uses localhost:8001. Do not confuse an
empty panel with healthy zero load: check scrape health and exporter freshness.

## Executed, isolated failure drills

All six drills below were executed on 2026-11-06 through a real TCP gateway and
Redis 7.4.12. Compute is **simulated**. The helper creates its own Redis container,
models, worker and API keys, and removes them afterward. It does not stop your
Compose Redis or delete arbitrary cluster Pods. Its Redis endpoint is fixed on
loopback so container restart does not change the service address.

```bash
export UV_CACHE_DIR=/tmp/infer-uv-cache
uv run --extra ml python -m tools.runbook_drills
```

Evidence: [p8-runbook.json](results/p8-runbook.json). Each scenario asserts its
outcome and persists partial diagnostics if it fails. A worker restart in the
helper creates a fresh connection pool, as a real process restart would.

| Drill | Actual observation | Recovery verified |
|---|---|---|
| No workers | Valid inference returns 503 with Retry-After | Start/warm/register a worker before accepting work |
| Slow model | Six requests succeed, compute 201.29 ms and last queue wait over one second | Restored delay measured 11.37 ms; healthy request succeeds |
| Overload | Depth-two policy admits 2 and rejects 18 with 503; sampled retained depth never exceeds 2 | Queue drains and accepted requests complete |
| Poison input | One controlled runner failure reaches DLQ at delivery 4, HTTP 500 DEAD_LETTERED | Pending work is acknowledged/deleted after terminal publication |
| DLQ growth | Three poison requests produce 3 DLQ entries and 9 reclaim attempts | Growth is bounded by the controlled input count, not an infinite retry loop |
| Redis down | /healthz 200, /readyz 503, inference 503 | Restore Redis, restart failed worker, fresh inference 200 and pre-outage cached final 200 |

The slow-runner drill demonstrates diagnosis, not a production latency target.
The no-worker drill starts without capacity, then starts that model's worker and
verifies recovery to 200. Slow compute is restored to its normal delay, and
overload/poison cases verify a subsequent healthy request. A separate poison runner exists only
in this helper; there is no production API option that deliberately crashes a
runner. Invalid normal input returns validation errors rather than forced retries.

## First response to an incident

Start with Overview: request/error rates, queue age, pending deliveries and alive
workers. Move to Batching: actual batch size, local wait and inference time.
For chat compare gateway TTFT with worker TTFT and engine activity. For capacity
compare desired/current replicas with accepting heartbeats; desired is not ready.
Record model, time window, request ID and trace ID from existing logs/response
headers. Avoid copying prompts, API keys or raw DLQ payloads into incident notes.

Use Retry-After and bounded client retries with jitter; immediate retry loops can
turn rejection into a retry storm. Do not raise a timeout merely to hide saturation.
Check the result cache for a known request ID before submitting duplicate work.
It is owned by the accepting API key and expires after its configured TTL.

## Redis unavailable

Liveness must remain independent of Redis. Gateway readiness/inference return
503 while its inbox reconnects. Workers fail closed on transport failures; Compose
restart policy or Kubernetes restarts their processes. Restore the same Redis
service endpoint, then verify readiness, accepting workers, a fresh successful
request and any known cached result before declaring recovery.

The drill uses AOF and a controlled server stop/start. Its preserved final does
not prove zero loss under power failure, disk loss or an unreplicated failover.
Production requires an operated HA/storage/backup strategy, Redis authentication
and TLS. This single local Redis is not that deployment.

The first drill attempt exposed Docker changing a random published port on restart:
[diagnostic probe](results/p8-redis-port-probe.json). That tested an endpoint change,
not reconnection to one stable service. A subsequent attempt reused an old
in-process pool, unlike a real worker restart, and failed during cleanup despite
successful requests: [diagnostic record](results/p8-runbook-stale-pool.json).
The final drill pins the endpoint and creates a fresh worker pool. The initial
trace is retained in [p8-runbook-initial.log](results/p8-runbook-initial.log).

## No accepting workers

A 503 NO_WORKERS means the gateway refused before enqueue, not that it lost an
accepted request. Check worker startup logs, model allowlist/config, upstream model
availability, model cache ownership and startup/readiness state. A heartbeat can
remain briefly after a process dies; it is a TTL liveness signal, not proof that
its pending work will finish. Restore a correctly configured replica and wait for
warmup/readiness before repeating a representative request.

On kind, distinguish unavailable ready capacity from a desired Deployment count.
Do not reduce min replicas to zero to make an empty queue look efficient. The
platform intentionally keeps a nonzero minimum for model cold-start behavior.

## Poison input and DLQ growth

Inspect error code, delivery count, model/worker logs and the original entry's
routing metadata using controlled Redis access. XAUTOCLAIM transfers sufficiently
idle ownership; extended XPENDING reports delivery count. After count exceeds
max_deliveries, DLQ publication and terminal error precede XACK/XDEL. The helper's
max=3 therefore quarantines on delivery 4, with three reclaimed attempts.

Fix a deterministic validation/model/config problem before replay. The project
has no automatic DLQ replay endpoint; inference is idempotent, but requests may
already have emitted a partial stream or cached an outcome. Preserve evidence
and determine ownership/deadline before intentionally resubmitting through the
normal authenticated API. Blindly deleting the DLQ removes diagnostic evidence;
blindly replaying can repeat failures. DLQ entries contain original input data.

## Slow model and queue growth

If inference time rises with a similar batch size, investigate model/device/input
length or CPU throttling. If inference time stays similar but batch size drops
and queue age rises, inspect batching/prefetch policy and worker count; E7 shows
that exact case. If gateway TTFT grows while worker TTFT is steady, look at queueing,
coalescing and transport before blaming vLLM decode speed.

CPU threads per replica must fit node resources. More threads or replicas can
create contention. Use the measured [tuning guide](TUNING.md), change one control
at a time and rerun the same workload. Do not treat simulated cost as GPU capacity.

## Overload

429 is the shared per-key token budget; 503 is admission/availability; 504 means
the request's deadline expired. Rejections are explicit and carry Retry-After
where specified. Inspect retained depth (undelivered plus pending), local prefetch,
worker capacity and head age. Count rejected valid submissions as failures in the
availability proposal, even when their quick responses make latency look low.

Recover by reducing offered load or restoring measured capacity, then check that
backlog drains and accepted work completes. Tune depth with the waiting budget,
not free memory alone. The kind depth 1000 is a demonstration guard, not an
admission bound proven to meet the 250 ms local target under arbitrary bursts.

## Worker death and rollout

The dedicated kind E11–E13 helper was executed; see [lesson 07](learning/07-kubernetes-deployment.md).
It restores its temporary settings. E11 preserves availability during rolling
replacement; E12 verifies a selected pending request is completed by a different
worker after actual SIGKILL; E13 observes real scaling and delayed contraction.
A Pod object disappearing alone is insufficient evidence of process death.

For streaming, a reclaimed partial answer fails explicitly as STREAM_INTERRUPTED;
it is not spliced together with a new generation. Clients must inspect SSE errors
after HTTP 200. A known full result may still be retrievable by X-Request-ID, but
resumable streaming is not supported. Default classifier deadlines are shorter
than default reclaim idle in Compose; successful recovery needs a tuned budget.

## Limits of this runbook

These drills cover a local single-node environment, not Redis HA failover, a node
power failure, cloud networking, long-duration soak or a GPU Kubernetes cluster.
Alert timing is verified with promtool's synthetic time-series tests; the drill
does not pretend to hold every outage for five minutes. Notification receivers,
production TLS/auth and durable monitoring history require deployment-specific
configuration. See the final [verification audit](VERIFICATION.md).
