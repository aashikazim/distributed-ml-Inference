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
