# Definition of Done audit

Audit date: **2026-11-06**. All eight phases have implementation and learning
materials. **Engineering acceptance is complete for the owner's localhost scope.**
Hosted CI/GHCR, real GPU kind, snapshot restore and a 15-minute soak now have
executed evidence. Study materials are complete; human teach-back remains an
owner exercise. Local results do not imply production HA, arbitrary workload
capacity or month-long SLO compliance. Cloud deployment is outside the requested
scope, not a required outstanding task.

## Section 19, item by item

| Requirement | Status | Evidence and practical limit |
|---|---|---|
| All phases, lint/types/tests and CI green | Verified locally and on hosted CI | Ruff lint/format, mypy on 48 source files, 62 CPU tests; additional real-GPU test passed. Hosted checks and all six main-branch GHCR publications passed; [run evidence](results/final-published-ci.json). Hosted tests intentionally skipped two uncached model checks. |
| Gateway→Redis→worker sync, async and streaming | Verified locally | Real Redis integration, Compose smoke, kind sync/async/SSE evidence, actual GPU chat. [Kind paths](results/p7-kind-paths.json), [current Compose real vLLM](results/p8-compose-real-vllm.json). |
| Dynamic batching improves throughput and matches singles | Verified on CPU | E2 real runner throughput and cached DistilBERT padded-batch numerical equality at 1e-5 tolerance; fake-clock timing/fan-out/poison tests pass. Runner-only throughput is not HTTP capacity. |
| Killing a worker mid-batch never loses a request | Verified scenarios; conditional guarantee | Real process chaos tests and E12's selected pending ID recovered elsewhere. Tuned deadlines and persisted Redis are assumptions; default Compose 5 s deadline cannot wait for 30 s reclaim. No unconditional exactly-once or zero-loss claim. |
| Overload returns 503/429 with Retry-After | Verified | Real Redis admission/rate-limit tests, E5 and final runbook depth-two drill (2 accepted, 18 rejected). Rejected requests are not counted as successful fast inference. |
| LLM streaming/cancellation, real GPU or pending | Verified on fake TCP and real GB10 | Chunk ordering/completeness/error/cap/concurrency/disconnect tests. Final real vLLM completion and disconnect abort test passed; active sequence became zero and worker cached CANCELLED. The full GPU overlay subsequently served real inference on local GB10 kind: [GPU evidence](results/final-kind-gpu.json). |
| Dashboards auto-provisioned; latency visible | Verified locally | Four Grafana dashboards, API discovery, Prometheus target/query evidence, E7 diagnosis, request timing fields and final-image spot checks. No screenshots captured; every panel query is documented. Real engine metrics were scraped. |
| Autoscaler decisions simulated and scaling on kind | Verified for configured fake worker | Table-driven/pure-policy/lock/fake-client tests, E8–E10 explicitly simulated, E13 actual 1→4→1 desired/ready history. No actual Torch/LLM deployment scaling is claimed. |
| Rolling update under load, zero failures or documented | Verified at tested load | E11 1200/1200 success; selected process-loss E12 120/120; E13 4200/4200. Initial deletion/signal attempts are preserved and not falsely labeled reclaim. |
| All required ADRs, lessons, concepts, diagrams and experiments | Delivered with executed local commands | Lessons 00–08, ADRs 0001–0010, diagrams 1–8, E1–E13, benchmark/capacity/SLO/runbook/tuning. Model/fake/hardware/source limits are explicit. GPU kind reproduction and measured limits are documented. |
| A new developer can learn from docs/study plan | Materials delivered and checked; human understanding is an owner exercise | Run→Read→Break→Explain study plan, glossary, concepts, code guides, guided failures, quizzes/answers and teach-back. Actual reader comprehension requires the owner's own explanation/review. |

## Final local checks and current artifacts

The default suite passes **62 tests**, with one GPU test excluded by its marker.
The explicit final GPU invocation passes that additional test; its
[separate current record](results/final-gpu-regression.json) preserves warm TTFT
and measured disconnect latency, distinct from the full-kind observation. Cached real CPU
weights were present, so local real-model checks did not skip or download.
Hosted CI passed 60 tests and skipped two uncached real-model checks, avoiding
network weight downloads. Those two passed locally with cached weights.

Ruff lint/format and mypy pass. CPU/CUDA and role images built non-root; Compose
fake+CPU profiles became healthy and served inference/SSE. All kind overlays
rendered; base/CPU and the full GPU overlay actually ran. The real NVIDIA device
plugin registered one GPU and the engine Pod consumed it. promtool accepts the configuration and sustained alert
tests, including before/after five-minute conditions.

The final-image benchmark completed 800 fake and 100 real CPU requests, all 200;
p95 110.42/60.76 ms. It verifies the new timing contract and stays below the local
candidate. The three-repeat baseline and a separate 600-request capacity trace
are preserved rather than silently replaced. Locust steady/ramp/spike/mixed all
reported zero failures; mixed includes 339 synthetic streamed chats. Six isolated
runbook drills passed against real Redis/TCP.

Evidence links: [benchmarks](BENCHMARKS.md), [all experiments](EXPERIMENTS.md),
[runbook](RUNBOOK.md), [final-image records](results/p8-e2e-final.json),
[GPU regression](results/p8-gpu-verification.json), [runbook records](results/p8-runbook.json),
[Locust records](results/p8-locust.json), [GPU verification scope](GPU_VERIFICATION.md).

## Wrap-up evidence

- [GPU kind](results/final-kind-gpu.json): pinned official image, non-root UID 1000,
  one GPU allocated, 1,031-token prompt plus 128 generated tokens, and active
  stream cancellation to zero/CANCELLED in 234.96 ms. Initial kernel warmup remains
  visible in the cold completion; no universal latency promise is inferred.
- [15-minute soak](results/final-soak.json): 18,000 simulated classifier and 1,800
  real CPU sentiment requests, all 200; p95 82.10/69.36 ms. Autoscaler enabled;
  concurrent GPU setup makes this a reliability observation, not isolated capacity.
- [Snapshot restore](results/final-backup.json): independent Redis restored stream
  IDs, pending ownership, group cursor, inbox and cached-result TTL; reclaim passed.
- [Hosted CI/publication](results/final-published-ci.json): checks and six role-image
  publications passed. Rendered namespace/service-link validation was added after
  real GPU testing exposed the missing namespace and VLLM_PORT collision.

Commands, fixes and limits: [final verification](FINAL_VERIFICATION.md).

## Scope exclusions and remaining practical limits

The owner explicitly requested localhost verification, without cloud deployment.
Cloud/WAN infrastructure, production ingress certificates, Redis HA, off-host
backup recovery, durable monitoring retention and external notification routing
are deployment-specific work outside that acceptance boundary. They are not
claimed tested and do not block this local project wrap-up.

The soak is 15 minutes, not a month-long SLO observation or a proof of no memory
leaks. The longer GPU prompt covers 1,031 input tokens, not arbitrary context
lengths, larger models or CUDA graph mode. SLO.md remains a measured local proposal.
A single GPU engine uses Recreate and has update downtime; classifier rolling
updates are the separately verified zero-failure scenario.

Resumable token streams, automatic DLQ replay, exactly-once compute and scale-to-zero
remain explicit non-goals. Nothing in passing checks silently enables them.
The study plan, quizzes, answers and teach-back prompts are complete. Only the
owner can assess their actual understanding by explaining the design.

Temporary GPU testing is stopped after sign-off and the local CPU/fake kind stack
is restored. Persistent model caches and measurement artifacts remain. The final
runtime record is [final-local-state.json](results/final-local-state.json).
