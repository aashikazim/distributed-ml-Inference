# Experiments

## E1 — Delivery is not completion

**Question:** What does Redis retain when a worker dies during inference?

**Prediction, made before running:** A completed request will leave neither a
retained request entry nor pending ownership. SIGKILL after XREADGROUP but before
reply/ACK will leave one retained, pending entry. The gateway will time out.
Restarting a reader of > alone will not recover that entry.

**Setup:** 2026-11-06; source commit
aeb0f406f2f59d31cbaf6d8e105b943b1a40a225. Executed on Linux aarch64, Ubuntu
glibc 2.39, kernel 6.11.0-1016-nvidia; CPU inventory reports 20 logical CPUs with
Cortex-X925 and Cortex-A725 cores. Python 3.12.3, Redis server 7.4.12 in Docker,
redis-py 8.1.0. CPU execution only. Owner reports an NVIDIA GPU, but this experiment
does not access or verify it.

**Simulation label:** The worker runs FakeRunner; inference compute and outputs
are **simulated**. Redis delivery, HTTP handling, process startup, SIGKILL and
pending-entry inspection are real operations.

**Configuration:** Disposable unique model/stream. Successful compute uses
base_ms=20, per_item_ms=0, jitter_ms=0. The killed worker uses base_ms=2000, with
a gateway request_timeout_ms=1000. Batch size is one. The script starts a fresh
Redis container, so existing queues are not involved.

**Method:** Run from the repository root after uv sync, with Docker available:

```bash
uv run python scripts/experiment_e1.py
```

The [script](../scripts/experiment_e1.py) first sends a successful HTTP request
through an in-process ASGI gateway and a separate worker process. It polls until
the completed request stream is empty, then inspects XINFO STREAM, XINFO GROUPS,
XPENDING and XLEN through redis-py. It tries a wrong key and unknown model. Next,
it replaces the worker with a slower simulated runner, sends another request,
waits until XPENDING reports delivery, sends SIGKILL, awaits the HTTP deadline,
and repeats every inspection. It removes only its own keys and container.

| Observation | After successful request | Before SIGKILL | After SIGKILL and HTTP deadline |
|---|---:|---:|---:|
| XLEN / XINFO STREAM length | 0 | 1 | 1 |
| XPENDING / group pending | 0 | 1 | 1 |
| Group undelivered lag | 0 | 0 | 0 |
| HTTP status | 200 | waiting | 504 |

Successful request queue_ms was 105.434 and inference_ms was 20.446; batch_wait_ms
was zero. These are single-run observations, not performance benchmarks. Worker
startup contributed to the queue time because the request and worker were started
close together. The invalid key returned 401 and the unknown model returned 404.
Full stream metadata and IDs are in [e1-results.json](e1-results.json).

**Conclusion:** The prediction matched. Redis retains evidence of delivered,
unfinished work after a worker dies. The Phase 1 reader does not reclaim pending
entries; 504 releases only the HTTP waiter. This experiment does not establish
automatic recovery, exactly-once execution, or production durability.

**What surprised us:** Undelivered lag was zero even while the request was stuck.
It had been delivered, so backlog alone would miss the failure. Also, XINFO GROUPS
retained two consumer identities after replacing the worker; consumer metadata
is not a live-worker registry. Heartbeats and reclaim arrive in Phase 3.

## Later experiments

E2–E3 measure batching in Phase 2. E4–E5 cover recovery and overload in Phase 3.
E6 covers LLM serving; E7 observability; E8–E10 autoscaling; E11–E13 deployment.
No measurements for those experiments are claimed yet.
