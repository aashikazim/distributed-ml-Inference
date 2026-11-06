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

## Phase 2 predictions (recorded before measurement)

**E2:** Shared runner overhead should make batches improve items/s over single
execution, most clearly with FakeRunner's simulated fixed cost. DistilBERT on CPU
may peak before the largest batch; all items in a batch experience the entire
batch latency, not the batch latency divided by its size. Use identical sentences
to control padded sequence length and record every timing sample.

**E3:** At low load, larger max_wait_ms should mostly add latency because the next
arrival is too far away to share a batch. At high load, some waiting should build
larger batches and improve service throughput; work-conserving collection while
busy may reduce the difference between wait settings. Measure both FakeRunner
(**simulated compute**) and real DistilBERT using the actual DynamicBatcher.
Use bounded sequential submission and report admission wait: this is a controlled
batcher experiment, not an unbounded open-loop HTTP capacity benchmark.

## E2 — Runner-only batch-size sweep

**Question:** Does batching improve useful throughput on this CPU, and what
happens to the latency each input experiences?

**Setup:** 2026-11-06. Benchmark code commit
54c1eef (full SHA in each JSON file); Linux aarch64, 20 logical CPUs, heterogeneous
Cortex-X925/Cortex-A725 cores, kernel 6.11.0-1016-nvidia. Python 3.12.3,
torch 2.15.0+cpu, transformers 4.57.6. Real model:
distilbert-base-uncased-finetuned-sst-2-english, resolved revision
714eb0fa89d2f80546fda750413ed43d93601a13, CPU float32, one intra-op thread.
No GPU inference was run. Full lscpu inventory is embedded in each result file.

**Method:** Explicitly prepare the trusted host model cache, then benchmark
FakeRunner and DistilBERT separately. For each size, warm up that shape twice and
record five complete invocations, including tokenization, model forward and output
conversion. Every input is the same sentence from tools/bench/shared.py; this
controls padded length, but does not represent a varied production workload.
No Redis or HTTP is involved. Final runs were sequential after Compose shutdown.
An initial exploratory pass overlapped the container build and was replaced.
No CPU affinity was pinned; OS scheduling and frequency variation remain.

```bash
uv run --extra ml python -m tools.bench.prepare_model
uv run --extra ml python -m tools.bench.batch_sweep --backend fake --output docs/results/e2-fake.json
uv run --extra ml python -m tools.bench.batch_sweep --backend torch --threads 1 --output docs/results/e2-torch.json
```

| Batch | Real torch items/s | Real batch latency ms | Real amortized ms/item | Simulated fake items/s | Fake batch latency ms |
|---:|---:|---:|---:|---:|---:|
| 1 | 38.77 | 25.79 | 25.79 | 45.20 | 22.12 |
| 2 | 65.40 | 30.58 | 15.29 | 82.43 | 24.26 |
| 4 | 111.34 | 35.93 | 8.98 | 142.35 | 28.10 |
| 8 | 167.37 | 47.80 | 5.97 | 220.82 | 36.23 |
| 16 | 238.11 | 67.20 | 4.20 | 305.98 | 52.29 |
| 32 | 273.34 | 117.07 | 3.66 | 380.48 | 84.10 |
| 64 | 303.41 | 210.94 | 3.30 | 431.57 | 148.30 |

**Results and conclusion:** The prediction matched: real torch throughput at
batch 16 was 6.14 times batch 1 and at batch 64 was 7.83 times batch 1. But every
input in batch 64 waited for about 210.94 ms of execution, not 3.30 ms.
The latter is amortized service cost. Increasing from batch 16 to 64 bought
diminishing throughput gains at much greater batch latency. This is not an SLO
recommendation or a whole-platform capacity estimate.

**What surprised us:** A single CPU thread still gained substantially from
batching; batching is not exclusively a GPU optimization. The sweep did not
reach a throughput decline by size 64, so a claimed optimum at 16 would be
unsupported. The shipped size 16 is the planned starting policy, not a measured
universal optimum.

Raw samples: [real torch](results/e2-torch.json),
[simulated fake](results/e2-fake.json). Fake results use base_ms=20,
per_item_ms=2 and jitter_ms=0 and describe programmed sleep, not ML performance.

## E3 — Waiting under low and high load

**Question:** Does a longer coalescing window improve batching enough to justify
the added latency?

**Setup:** Same date, hardware, model and software as E2. Final scheduler code
commit 70ddbc4 (full SHA in the raw JSON). E3 was rerun after separating result
delivery from compute; earlier scheduler results were replaced. Use the
actual DynamicBatcher, one executor thread, max_batch_size=16 and
prefetch_bound=32. Compare max_wait_ms 0, 2, 10 and 30. Real torch and simulated
FakeRunner are measured separately.

**Method:** Low load schedules 24 arrivals 100 ms apart. High load schedules
128 arrivals 1 ms apart. The driver submits sequentially and blocks when local
capacity is full. It catches up its schedule after capacity frees; it does not
spawn unbounded tasks or measure an external unbounded arrival queue. Each item
is timestamped just before submit, so reported latency and batch_wait include
any wait for submit capacity. In the Redis worker, capacity is checked before
reading, so that admission wait would instead remain in Redis queue time.

Only close the batcher after queue.join, ensuring the last partial batch still
obeys its normal timer. Each setting is a single short trace; p95 uses nearest
rank. Average batch size is item-weighted: it is the batch size seen by an average
input, not the mean over invocations. Completion rate includes the trace's
arrival interval and drain time. The low-load rate is arrival-limited; it is not
model capacity. A nominal 1 ms schedule is not evidence of sustained 1000 RPS.

```bash
uv run --extra ml python -m tools.bench.wait_sweep --backend fake --output docs/results/e3-fake.json
uv run --extra ml python -m tools.bench.wait_sweep --backend torch --threads 1 --output docs/results/e3-torch.json
```

| Real torch load | Wait ms | Item-weighted batch size | Completion items/s | p50 latency ms | p95 latency ms | Max submit wait ms |
|---|---:|---:|---:|---:|---:|---:|
| Low | 0 | 1.00 | 10.26 | 44.22 | 48.99 | 0.07 |
| Low | 2 | 1.00 | 10.27 | 36.29 | 47.84 | 0.11 |
| Low | 10 | 1.00 | 10.26 | 53.74 | 57.25 | 0.15 |
| Low | 30 | 1.00 | 10.11 | 65.77 | 75.67 | 0.09 |
| High | 0 | 15.77 | 191.29 | 158.81 | 172.69 | 91.39 |
| High | 2 | 15.39 | 197.18 | 150.75 | 182.56 | 85.92 |
| High | 10 | 15.14 | 196.25 | 143.26 | 197.53 | 80.15 |
| High | 30 | 16.00 | 216.59 | 142.02 | 151.73 | 80.12 |

| Simulated fake load | Wait ms | Item-weighted batch size | Completion items/s | p95 latency ms |
|---|---:|---:|---:|---:|
| Low | 0 | 1.00 | 10.33 | 23.41 |
| Low | 2 | 1.00 | 10.31 | 25.49 |
| Low | 10 | 1.00 | 10.28 | 33.93 |
| Low | 30 | 1.00 | 10.19 | 53.77 |
| High | 0 | 15.77 | 288.05 | 111.11 |
| High | 2 | 15.39 | 286.85 | 114.87 |
| High | 10 | 15.14 | 280.59 | 132.16 |
| High | 30 | 16.00 | 290.20 | 107.40 |

**Conclusion:** At low load, every batch remained single-item. A 30 ms window
raised real p95 from 48.99 to 75.67 ms without batching gains, as predicted.
The 2 ms trace was slightly faster than zero wait despite more intentional
waiting: compute and OS scheduling variation exceed such small differences.
Measured mean coalescing wait nevertheless grew from 0.13 ms to 30.54 ms.

At high load the prediction was partly supported: 30 ms produced full batches
and 216.59 completions/s versus 191.29 with zero wait. However zero wait already
formed nearly full batches, intermediate settings were not monotonic, and the
10 ms setting had higher p95 than zero wait. Startup batch shape, final partial
batch, scheduling variation and admission blocking affect these small samples;
do not infer a universal ranking or default change from them.

**What surprised us:** Zero wait averaged 15.77 items per experienced batch at
high load. The work-conserving busy-runner path naturally batches without an
extra deliberate wait. Also, high-load batch waiting exceeded configured
max_wait_ms: the limit is not a promise about congestion or producer admission.

Raw per-item records and full metadata:
[real torch](results/e3-torch.json), [simulated fake](results/e3-fake.json).
These results exclude Redis, HTTP, multi-worker distribution, long-run saturation,
mixed-length production inputs, and GPU execution. No measured SLO is proposed.

## Phase 2 real-model verification

The cached real-model test compared single-input probabilities with a padded
batch, including an input truncated to 256 tokens; labels matched and probabilities
passed relative/absolute tolerance 1e-5. A real-Redis integration test sent four
HTTP requests, received POSITIVE/NEGATIVE/POSITIVE/NEGATIVE in one batch of four,
and verified zero pending entries and stream length after shutdown.

Compose's torch profile was built and run on CPU. A direct sentiment curl
returned POSITIVE, batch_size=1, queue_ms=1.397, batch_wait_ms=13.250 and
inference_ms=29.322. This is a single smoke observation, not an additional
benchmark. The model worker logged readiness after loading and warmup.

## Phase 3 predictions (recorded before measurement)

**E4:** SIGKILL during fake compute should leave all four entries pending under
the dead consumer. A replacement should XAUTOCLAIM after the configured idle age,
increment delivery counts, rerun inference, publish replies/cache, and ACK/delete
all four. Increasing reclaim_idle_ms from 500 to 1000 to 2000 should roughly
increase recovery time correspondingly. Recovery measured from kill includes
replacement startup, remaining idle wait, scan cadence and compute; it will not
equal the threshold exactly. Use three trials per setting, a 50 ms scan interval,
a four-item bound and fake base 200 ms plus 1 ms/item.

**E5:** Under a fixed 120-request burst against a slow fake worker, admission at
retained depth 8 should reject excess work quickly with 503 and Retry-After.
Disabling admission should admit much more work, raise retained depth and produce
deadline timeouts. Record successful-client latency separately from rejection
and timeout latency: fast rejections must not be averaged into “faster inference.”
Compute is simulated; Redis, HTTP handling, subprocesses and signals are real.


## E4 — SIGKILL recovery versus reclaim idle time

**Question:** How quickly does a replacement finish requests whose worker died
during a batch? The prediction appears above and was recorded before running.

**Setup:** 2026-11-06, code commit 5a60604dcca2f2eeffba047859d39459d6f94af2.
Linux aarch64, 20 logical CPUs, Cortex-X925/Cortex-A725 inventory, Python 3.12.3,
Redis server 7.4.12, redis-py 8.1.0. Full hardware inventory is in the raw JSON.
No GPU execution. Model compute/output are **simulated**; Redis TCP operations,
worker subprocesses, signals, HTTP request handling and measured elapsed time
are real. The gateway runs in-process through httpx ASGITransport, so these are
not client-to-gateway network latency measurements.

Use four simultaneous inputs, max_batch_size=4, prefetch_bound=4, max_wait_ms=20,
fake base_ms=200/per_item_ms=1/jitter_ms=0, request_timeout_ms=6000,
reclaim_interval_ms=50. Compare reclaim_idle_ms 500, 1000 and 2000 with three
trials each. Heartbeats refresh every 20 ms with a 1-second TTL for the drill.
These settings differ deliberately from the shipped 30-second reclaim default.

**Method:** Start a disposable Redis and worker A; wait for readiness. Start four
sync requests and wait for a heartbeat showing four locally outstanding items
and active compute. Inspect extended XPENDING, SIGKILL A, verify four pending
entries remain, then start B. Measure monotonic time from kill to all four HTTP
responses. Verify B logged four reclaim events with delivery_count=2 and all
responses identify B. Finally verify zero pending and retained entries. Run:

```bash
uv run --extra ml python -m tools.reliability_experiments E4 --output docs/results/e4-recovery.json
```

| Idle threshold ms | Trial 1 recovery ms | Trial 2 | Trial 3 | Median |
|---:|---:|---:|---:|---:|
| 500 | 734.63 | 719.48 | 723.87 | 723.87 |
| 1000 | 1239.40 | 1275.05 | 1209.47 | 1239.40 |
| 2000 | 2235.62 | 2242.55 | 2267.44 | 2242.55 |

**Measured result:** All 36 requests succeeded. Every trial retained four pending
entries after SIGKILL and ended with pending=0 and XLEN=0. The prediction matched:
longer idle thresholds increased recovery delay. Recovery exceeded the threshold
because it includes replacement startup, scan scheduling and another batch.

**What surprised us:** Reclaim did not need to wait for the dead heartbeat to
expire; pending idle age is independent of registry liveness. A 50 ms configured
scan interval is not a hard scheduling bound: Redis blocking-read granularity,
other work and event-loop scheduling also contribute.

**Caveat:** The default classifier timeout (5 seconds) is shorter than default
reclaim idle (30 seconds), so those defaults yield timeout then EXPIRED, not this
successful recovery. Shortening idle is safe only after measuring the buffered
workload. The drill uses one small known batch and leaves a margin above it.

Raw evidence: [e4-recovery.json](results/e4-recovery.json), including pre-kill PEL
ownership, post-kill pending count, reclaimed delivery counts and response timings.

## E5 — Overload with and without admission

**Question:** How do queue retention, successful latency, rejection and timeouts
change under the same burst? The prediction appears above.

**Setup:** Same date, CPU host, Python and Redis versions as E4. Final code commit
d188dc20c1ab1eed68026ae0c33472fe6a60ebb7. Fake compute is **simulated**;
base_ms=60/per_item_ms=1/jitter_ms=0, max_batch_size=4, prefetch_bound=4,
max_wait_ms=20, max_queue_depth=8, request_timeout_ms=450. One worker, 120
simultaneous HTTP handlers per trial, three trials per policy. Rate burst/refill
are both 100000 to isolate model admission. The final blocking Redis pool is
bounded to 64 connections per process.

**Method:** Warm the worker, create the fixed burst once, measure each handler
from submission through response, and sample XLEN every 5 ms. Compare only the
admission switch; body limits, rate limiting, deadlines and prefetch remain.
Wait for all retained work to drain after responses and verify zero pending.
The gateway uses ASGITransport in-process, with a real separate worker and real
Redis TCP I/O. Sequential trials ran without this task's container builds or
other test workloads. Run:

```bash
uv run --extra ml python -m tools.reliability_experiments E5 --output docs/results/e5-overload.json
```

| Admission | Trial | 200 successes | 503 rejections | 504 timeouts | Sampled max XLEN | Success p95 ms | Rejection p95 ms | Timeout p95 ms |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| On | 1 | 8 | 112 | 0 | 8 | 282.80 | 134.38 | — |
| On | 2 | 8 | 112 | 0 | 8 | 249.02 | 94.77 | — |
| On | 3 | 8 | 112 | 0 | 8 | 241.03 | 85.81 | — |
| Off | 1 | 20 | 0 | 100 | 120 | 420.56 | — | 461.54 |
| Off | 2 | 16 | 0 | 104 | 120 | 407.74 | — | 452.44 |
| Off | 3 | 18 | 0 | 102 | 120 | 447.71 | — | 454.68 |

**Conclusion:** The prediction matched. Admission held retained work at eight
and rejected 112 requests per trial with Retry-After, letting all eight accepted
requests finish without timeout. Disabling it admitted all 120, grew retention
to 120, and produced 100–104 timeouts per trial. Success latency was also worse.

This does not mean admission increased completions in this finite burst:
without admission, 16–20 succeeded instead of eight. It trades immediate
acceptance for bounded waiting and explicit rejection. Queue depth needs tuning
to the actual service budget; eight is an experiment control, not a recommended
production capacity. Fast 503s are never averaged into success latency here.

**What surprised us:** The first run exposed an independent connection-pool
limit. redis-py 8.1.0 defaults to an immediate-failure pool of 100 connections;
one inbox reader left capacity for 99 burst callers, with 21 failures even when
queue admission was disabled. A disposable probe reproduced exactly 99 PONGs
and 21 MaxConnectionsError outcomes when one connection was reserved. The new
bounded blocking pool completed all 120 probe calls. A regression test exercises
120 operations through a four-connection blocking pool.

The initial measurements are preserved in
[e5-initial-pool-limit.json](results/e5-initial-pool-limit.json), the probe in
[p3-pool-probe.json](results/p3-pool-probe.json), and final per-request records in
[e5-overload.json](results/e5-overload.json). The pool correction also moved token
bucket waiting inside the handler's deadline accounting. Initial results are
diagnostic evidence, not the admission-only comparison above.

**Limits:** Short bursts, small success samples, an in-process gateway transport,
simulated compute, no CPU affinity pinning and ordinary OS scheduling. Sampled
XLEN may miss brief peaks; a separate concurrent admission test verifies the
atomic depth bound. End-to-end timeout response can slightly exceed 450 ms due
to scheduling and best-effort cancellation I/O. No SLO or cloud capacity is
inferred. No GPU or real-vLLM measurement was performed.

## Phase 3 additional verification

Three Compose fake replicas accepted 120 real HTTP requests successfully, with
three live registry entries. Request counts are recorded below; pull distribution
does not promise equality. The same real-HTTP drill accepted async submission with
202 and polled the final result with 200.

| Worker | Requests |
|---|---:|
| 2997fda5ec7d-edf0ea0b | 41 |
| 8c76e6361017-151392a3 | 37 |
| cfd354a81af0-e2ee918a | 42 |

Evidence: [p3-compose-distribution.json](results/p3-compose-distribution.json).
The original E1 script was updated to wait for worker registration under admission
and rerun successfully; new evidence is
[e1-phase3-validation.json](results/e1-phase3-validation.json), while the historical
Phase 1 result file was retained. SIGTERM and SIGKILL mid-batch, poison/DLQ,
malformed envelopes, cancellation/disconnect, shared rate limits, polling,
duplicate replay, body limits and connection waits are covered by automated tests.

## Phase 4 predictions (before measurement)

**E6a:** With a fake server's fixed simulated first-token and per-token delays,
increasing bounded concurrency should increase aggregate synthetic tokens/s until
transport/scheduling overhead dominates. Queueing may raise gateway TTFT while
worker TTFT remains nearer the configured delay. Real vLLM results, if obtained,
will be recorded separately with the actual GPU, model, image and settings.

**E6b:** A 20 ms coalescing window should reduce Redis XADD count substantially
for a fast token source, while preserving exact text, event order, finish reason
and usage. It may delay first client-visible content. Compare zero and 20 ms
using identical synthetic output and report worker and gateway observations
separately. All fake-server throughput/latency results are simulated inference.


## E6a — bounded concurrency versus TTFT and aggregate token throughput

**Question:** How does adapter concurrency affect generation throughput and time
to first content for a burst? **Hypothesis:** Recorded above before measurement.

**Setup:** Measured 2026-11-06 on the same Linux aarch64 host: 20 logical CPUs
(Cortex-X925/Cortex-A725). Python 3.12.3, real Redis 7.4.12, real TCP gateway and
HTTP upstream. Runtime source commit 476c46d (full SHA in artifacts). Fake output
is **simulated inference**, with 40 ms initial delay and 2 ms per synthetic token.
Real inference uses NVIDIA GB10, driver 580.95.05, Qwen/Qwen2.5-0.5B-Instruct,
vLLM 0.33.0, CUDA 13.0. Exact image, model revision, memory and flags are in
[GPU verification](GPU_VERIFICATION.md) and
[environment](results/p4-gpu-environment.json). Eager execution, context 2048,
max-num-seqs 32, memory fraction 0.25 and prefix caching enabled. Both scenarios
run locally; this is not a remote-client network measurement.

**Method:** Each setting receives a simultaneous burst of 16 requests, capped at
32 output tokens, temperature zero, the same counting prompt, and 20 ms
coalescing. Sweep max_concurrent 1, 2, 4, 8, 16; repeat each three times. Tables
show the median of the three per-trial summaries, not percentiles pooled over all
trials. Client TTFT begins at HTTP submission and ends at first nonempty content;
worker TTFT begins at dequeue. Tokens/s = total final-usage completion tokens /
burst elapsed time. Every response must finish with usage and DONE; fake text is
checked for exact ordered completeness. No tests/builds ran concurrently during
the measured trials. The vLLM server was warmed before these experiments.

Commands executed from the repository root (UV_CACHE_DIR as in the quickstart):

```bash
uv run --extra ml python -m tools.llm_experiments E6a --backend fake --output docs/results/e6a-fake.json
uv run --extra ml python -m tools.llm_experiments E6a --backend vllm --output docs/results/e6a-vllm.json
```

The real backend requires the running GPU profile. Each command starts its own
disposable Redis, gateway and adapter; the fake command starts its own TCP server.

**Measured results:**

| Backend | Concurrency | Completion tokens/s | Client TTFT p50 ms | Client TTFT p95 ms | Worker TTFT p50 ms |
|---|---:|---:|---:|---:|---:|
| Fake — simulated | 1 | 281.47 | 928.69 | 1769.87 | 43.06 |
| Fake — simulated | 2 | 545.62 | 491.39 | 891.34 | 43.73 |
| Fake — simulated | 4 | 977.68 | 263.60 | 459.33 | 44.15 |
| Fake — simulated | 8 | 1836.78 | 156.76 | 226.90 | 45.03 |
| Fake — simulated | 16 | 2677.80 | 126.57 | 135.53 | 54.82 |
| Real GB10 | 1 | 139.15 | 1791.50 | 3498.99 | 18.01 |
| Real GB10 | 2 | 299.96 | 797.96 | 1536.48 | 18.81 |
| Real GB10 | 4 | 545.85 | 407.11 | 763.81 | 27.98 |
| Real GB10 | 8 | 1035.96 | 187.89 | 320.09 | 27.52 |
| Real GB10 | 16 | 1750.81 | 112.05 | 131.91 | 33.30 |

**Conclusion:** Increasing concurrency increased throughput for this workload and
reduced client waiting. This does not establish that still higher concurrency is
always beneficial: worker TTFT on the GPU increased as more sequences competed
inside the engine. Keep Redis queueing and engine time separate when interpreting
latency. The cap is a control on admitted work, not an optimizer for every model.

**What surprised us:** The original prediction allowed for rising gateway TTFT;
this fixed-size burst showed the opposite. At concurrency one, most requests wait
behind earlier generations; at higher concurrency, reduced queueing dominates.
The first ever real-model request also had 17289 ms TTFT, versus tens of milliseconds
after warmup. That cold observation is retained in
[p4-gpu-first-request.json](results/p4-gpu-first-request.json), not silently mixed
into warmed throughput. Health checks alone did not establish latency readiness.

Raw trials, per-request content/usage/meta, date, CPU inventory and commit:
[e6a-fake.json](results/e6a-fake.json), [e6a-vllm.json](results/e6a-vllm.json).

## E6b — chunk coalescing on and off

**Question:** What does a 20 ms relay window save and what does it cost?
**Hypothesis:** Fewer Redis writes, unchanged text/order/finish/usage, with possible
extra first-content delay; recorded before measurement above.

**Setup:** Same hardware, versions and runtime source as E6a. Fake inference is
**simulated**. **Method:** Burst 16 requests, max_concurrent=4, max_tokens=64,
three trials each with coalesce_ms=0 and 20. Each trial has 1024 completion tokens.
Compare total chunk XADDs (excluding final entries), final usage and client TTFT.
The gateway expands bundled chunks, so downstream SSE event count is not reduced.
Exact synthetic text is asserted. Real output, finish reason and usage are stored
for inspection; numerical performance is specific to this short counting prompt.

```bash
uv run --extra ml python -m tools.llm_experiments E6b --backend fake --output docs/results/e6b-fake.json
uv run --extra ml python -m tools.llm_experiments E6b --backend vllm --output docs/results/e6b-vllm.json
```

**Measured results:** Medians of three trial summaries.

| Backend | Window ms | Chunk XADDs / burst | Completion tokens/s | Client TTFT p50 ms | Client TTFT p95 ms |
|---|---:|---:|---:|---:|---:|
| Fake — simulated | 0 | 1056 | 1346.75 | 341.16 | 614.32 |
| Fake — simulated | 20 | 125 | 1296.91 | 376.08 | 666.27 |
| Real GB10 | 0 | 1040 | 605.05 | 671.63 | 1300.72 |
| Real GB10 | 20 | 256 | 597.71 | 693.24 | 1341.55 |

**Conclusion:** Chunk writes fell by 88.2% for simulated output and
75.4% on the real GPU path. Client first-content latency increased,
and aggregate throughput decreased slightly in both comparisons. The default
20 ms is a bounded trade-off, not a demonstrated universal speedup. A deployment
valuing immediate text more than Redis write cost can set the window to zero.

**What surprised us:** Fewer Redis operations did not improve throughput here.
The measurements support a write-cost saving, not an automatic generation-speed
claim. Client TTFT includes several waves of queueing in this 16-request burst,
so its change is not simply one added 20 ms window.

Raw evidence: [e6b-fake.json](results/e6b-fake.json),
[e6b-vllm.json](results/e6b-vllm.json). Role and finish chunks also count toward
chunk writes; one HTTP event is not necessarily one model token. TPOT values in
raw metadata are estimates using final engine usage, not per-kernel timings.

**Shared limits:** Three short trials per setting; fixed prompt and output cap;
local network; ordinary OS scheduling without CPU pinning; default prefix caching
may reuse the repeated prompt. Sweep order was fixed, not randomized. The engine
was warmed and runs eager mode. No 32-sequence saturation, long-context test,
cloud capacity estimate, production SLO or GPU Kubernetes result is implied.
Fake and real numbers answer different questions and must not be substituted.

## Phase 4 correctness and GPU abort evidence

The final suite passed 46 CPU tests, including real Redis and existing process
chaos drills; the explicit gpu marker passed one additional real-engine test.
The GPU test saw one active sequence before client disconnect, then zero plus
CANCELLED after 41.22 ms in the final warm run (20 ms cancellation polling).
A ten-token warm completion had 21.73 ms worker TTFT and 76.65 ms upstream duration.
These are individual observations, not percentile or default-configuration claims.
[Verification](results/p4-gpu-verification.json) and
[cold first request](results/p4-gpu-first-request.json) preserve both states.

## E7 — diagnose a batching bottleneck using only dashboard queries

**Question:** Can Overview and Batching distinguish queue saturation from slower
model execution without reading logs? **Prediction, recorded before traffic:**
Batch-size-one loses amortization; batch size falls, queue/head age grow, and
invocation time remains similar. Busy fraction should stay high under saturation.

**Setup:** 2026-11-06, Linux aarch64 on the same 20-logical-CPU GB10 host; no GPU
compute in E7. Python 3.12.3, Redis 7.4.12, Prometheus 3.5.0, Grafana 12.1.0.
Real Docker services, TCP HTTP, Redis and scraping; **simulated model compute**
(base 20 ms, per item 2 ms, jitter 3 ms). Phase 5 working tree based on 3644455;
the phase commit preserves the implementation used. Not an isolated production
machine; brief verification checks also ran on the host during the drill.

**Method:** `uv run --extra ml python -m tools.monitoring_experiment` sends
80 requests/s for 75 seconds per scenario, first max_batch_size=32, then 1.
One worker in both scenarios; same 5-second deadline and queue admission policy.
It saves exact expressions from the provisioned dashboards and their real
Prometheus results after each scenario, then restores the worker configuration.
No application logs are used for the diagnosis. Histograms have interpolation
error; raw HTTP success timings are a separate cross-check, not diagnostic input.

| Observation | Baseline batch cap 32 | Bottleneck batch cap 1 |
|---|---:|---:|
| HTTP 200 / 6000 offered | 6000 | 404 |
| HTTP 504 / 6000 offered | 0 | 5596 |
| Dashboard mean actual batch | 2.290 | 1.000 |
| Dashboard mean invocation seconds | 0.02657 | 0.02396 |
| Dashboard Redis queue wait seconds | 0.00106 | 4.93652 |
| Dashboard undelivered backlog | 0 | 318 |
| Dashboard head age seconds | 0 | 4.95486 |
| Dashboard pending | 4 | 2 |
| Dashboard busy fraction | 0.929 | 0.890 |
| Dashboard approximate all-response p95 seconds | 0.09549 | 9.72110 |
| Raw successful HTTP p95 milliseconds | 65.35 | 4926.85 |

**Dashboard-only diagnosis:** Overview shows growing waiting and errors, not just
higher latency. Batching shows single-item execution with a similar/slightly
shorter invocation duration. A high busy fraction in both scenarios means busy
alone cannot identify healthy throughput. Batch throughput estimates explain the
difference: about 2.29/0.02657 versus 1/0.02396 items per second, against 80 offered.
The bottleneck is lost batching capacity, not a new expensive model invocation.
Restoring batching fixes the controlled cause; blindly increasing timeout would
only retain more waiting work.

**Conclusion:** The dashboards locate queue saturation and insufficient service
capacity without request logs. The full experiment is repeatable and restores its
configuration. All four dashboards were discovered through Grafana's API, all
active targets were healthy, and promtool accepted configuration and five rules.
Evidence: [e7-monitoring.json](results/e7-monitoring.json),
[p5-provisioning.json](results/p5-provisioning.json), [panel guide](dashboards.md).

**What surprised us:** Even the healthy worker was about 93% busy; utilization
alone would have misdiagnosed it. The coarse 5-to-10-second histogram bucket also
made p95 look near ten seconds when many timeouts were just over five seconds.
That is interpolation, not evidence of ten-second raw request latency. Raw success
p95 is separately measured and does not include failed requests. Bucket resolution
and the chosen population must accompany any percentile claim. E7 is one ordered
pair of short runs, not a statistical capacity guarantee or SLO measurement.
