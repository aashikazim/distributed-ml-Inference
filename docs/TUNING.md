# Tuning from evidence

Start with [benchmarks](BENCHMARKS.md) and [dashboard queries](dashboards.md).
Keep the workload, hardware, source/image and arrival method fixed while changing
one control. Record failures separately from successful latency. A faster mean
with more rejected requests is not automatically an improvement.

| Symptom | Evidence to compare | Candidate control | Observed trade-off |
|---|---|---|---|
| Batch near one, queue age grows | Batch/inference means and busy fraction | Batch cap/wait or more replicas | E7 lost amortization with similar compute time |
| Low-load latency rises without bigger batches | Batch size versus collection wait | Lower max_wait_ms | E3 longer waits added delay without useful batches |
| GPU/client TTFT disagreement | Queue age, gateway and worker TTFT | Concurrency cap or queue budget | E6a less queueing but increased engine TTFT at higher concurrency |
| High Redis chunk write cost | Chunk writes, client TTFT, tokens/s | Coalescing window | E6b reduced writes but delayed text and slightly reduced throughput |
| Repeated replica reversals | Desired/ready, decisions, cold-start delay | Stabilization/cooldown | E9 retained more replica-seconds to reduce flapping |
| Changing backlog target has little effect | Each raw signal and step limits | Check dominant recommendation | E10 targets 5–80 were identical; 500 changed waiting/cost |
| Accepted work expires after a kill | Idle threshold, deadline, local execution bound | Tune the entire recovery budget | Default 5 s classifier deadline cannot survive 30 s reclaim |

Do not set reclaim idle below the slowest locally buffered work plus compute and
reply margin. Prefetch consumes deadline budget before inference starts. A short
idle value that succeeds for simulated 50 ms batches can steal valid slow GPU
work. Kubernetes termination grace must exceed the drain budget too.

Use CPU thread count together with requests/limits and replica count. The tested
CPU sentiment worker configures one intra-op thread. Increasing every replica's
threads toward the host core count can oversubscribe the same node. Model caches
and warmup affect startup, not only steady throughput; health alone did not prevent
the real vLLM first-request initialization cost.

Bound queue depth from a measured waiting budget and then test bursts. Little's
Law estimates means within a stable boundary; it does not certify a p95 admission
threshold. Keep Redis depth, local prefetch and LLM semaphore limits consistent.
For vLLM, align adapter concurrency with max-num-seqs, then measure context/memory
and output-length effects instead of copying the small Qwen results to a larger
model. Local GPU kind also passed a 1,031-token prompt with 128 generated
tokens; this is not exhaustive long-context tuning or another cluster's capacity.

The [local SLO proposal](SLO.md) applies only to its measured workload. Before
production adoption, rerun longer tests with representative inputs, failures,
networking and resources. Measure the chosen success population and validate that
alert buckets can resolve the target; coarse buckets distorted E7's timeout p95.
