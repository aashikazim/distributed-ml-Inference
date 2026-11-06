# Definition of Done audit

Audit date: **2026-11-06**. All eight phases have implementation and learning
materials. The whole-project checklist is **not fully signed off**: hosted CI,
GPU Kubernetes and human teach-back are not verified. Local passing results do
not imply production HA, arbitrary workload capacity or month-long SLO compliance.

## Section 19, item by item

| Requirement | Status | Evidence and practical limit |
|---|---|---|
| All phases, lint/types/tests and CI green | Local implementation/checks verified; hosted CI pending | Ruff lint/format, mypy on 48 source files, 62 CPU tests; additional real-GPU test passed. CI workflow/image builds exist; no remote push/run or GHCR publication claimed. |
| Gateway→Redis→worker sync, async and streaming | Verified locally | Real Redis integration, Compose smoke, kind sync/async/SSE evidence, actual GPU chat. [Kind paths](results/p7-kind-paths.json), [current Compose real vLLM](results/p8-compose-real-vllm.json). |
| Dynamic batching improves throughput and matches singles | Verified on CPU | E2 real runner throughput and cached DistilBERT padded-batch numerical equality at 1e-5 tolerance; fake-clock timing/fan-out/poison tests pass. Runner-only throughput is not HTTP capacity. |
| Killing a worker mid-batch never loses a request | Verified scenarios; conditional guarantee | Real process chaos tests and E12's selected pending ID recovered elsewhere. Tuned deadlines and persisted Redis are assumptions; default Compose 5 s deadline cannot wait for 30 s reclaim. No unconditional exactly-once or zero-loss claim. |
| Overload returns 503/429 with Retry-After | Verified | Real Redis admission/rate-limit tests, E5 and final runbook depth-two drill (2 accepted, 18 rejected). Rejected requests are not counted as successful fast inference. |
| LLM streaming/cancellation, real GPU or pending | Verified on fake TCP and real GB10 | Chunk ordering/completeness/error/cap/concurrency/disconnect tests. Final real vLLM completion and disconnect abort test passed; active sequence became zero and worker cached CANCELLED. GPU cluster remains separate and untested. |
| Dashboards auto-provisioned; latency visible | Verified locally | Four Grafana dashboards, API discovery, Prometheus target/query evidence, E7 diagnosis, request timing fields and final-image spot checks. No screenshots captured; every panel query is documented. Real engine metrics were scraped. |
| Autoscaler decisions simulated and scaling on kind | Verified for configured fake worker | Table-driven/pure-policy/lock/fake-client tests, E8–E10 explicitly simulated, E13 actual 1→4→1 desired/ready history. No actual Torch/LLM deployment scaling is claimed. |
| Rolling update under load, zero failures or documented | Verified at tested load | E11 1200/1200 success; selected process-loss E12 120/120; E13 4200/4200. Initial deletion/signal attempts are preserved and not falsely labeled reclaim. |
| All required ADRs, lessons, concepts, diagrams and experiments | Delivered with executed local commands | Lessons 00–08, ADRs 0001–0010, diagrams 1–8, E1–E13, benchmark/capacity/SLO/runbook/tuning. Model/fake/hardware/source limits are explicit. Untested GPU instructions are labeled rather than claimed run. |
| A new developer can learn from docs/study plan | Materials verified present; human outcome pending | Run→Read→Break→Explain study plan, glossary, concepts, code guides, guided failures, quizzes/answers and teach-back. Actual reader comprehension requires the owner's own explanation/review. |

## Final local checks and current artifacts

The default suite passes **62 tests**, with one GPU test excluded by its marker.
The explicit final GPU invocation passes that additional test. Cached real CPU
weights were present, so local real-model checks did not skip or download.
Hosted CI intentionally uses no model download and may skip uncached real-model
checks; those have separate local evidence.

Ruff lint/format and mypy pass. CPU/CUDA and role images built non-root; Compose
fake+CPU profiles became healthy and served inference/SSE. All kind overlays
rendered; the base/CPU stack actually ran. Kustomize GPU rendering is not GPU
scheduling verification. promtool accepts the configuration and sustained alert
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

## Still unverified or deliberately outside this build

- Hosted GitHub Actions execution and GHCR publication: workflow exists; no push
  or hosted green result was obtained.
- GPU Kubernetes overlay: rendered only; no GPU cluster/device-plugin scheduling run.
- Production Redis HA/failover, backup restoration under node/disk loss, and
  zero-loss durability. The local AOF stop/start preserved one final, not all failures.
- Long soak, WAN/cloud placement, representative long prompts/contexts, larger
  models, and month-long SLO compliance. [SLO.md](SLO.md) is a measured local proposal.
- Production ingress/auth/TLS, durable monitoring retention and external alert
  notification delivery. Local Grafana viewing is anonymous/read-only on localhost.
- Resumable token streams, automatic DLQ replay, exactly-once compute and scale-to-zero.
  These are not silently implemented or implied by successful short tests.
- Human teach-back. The capstone supplies questions; the agent cannot certify the
  owner's understanding without hearing the explanations.

The GPU server and temporary Compose stacks used for testing were stopped
afterward. The dedicated kind stack remains available: gateway localhost:8001,
Grafana localhost:13000 and Prometheus localhost:19090 through local forwards.
Its final record has 11 ready Pods and healthy scrape targets; see
[p8-final-state.json](results/p8-final-state.json). Persistent model caches and
result artifacts remain. Phase 8 is the stopping point; stretch features require
a new instruction.
