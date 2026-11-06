# Progress

## Phase 7 — implementation and local verification complete

Built role-specific multi-stage non-root image targets, locked mutually exclusive
CPU/CUDA extras, worker warmup/drain HTTP probes, complete Compose health/restart
policies, Kustomize base/kind/CPU/GPU layouts, Redis AOF PVC, model caches,
resources, rolling policies, PDBs, Secret-based keys and named scale-only RBAC.
Added dedicated-context kind-up.sh and GitHub Actions checks/image builds with
conditional main-branch GHCR publication. Hosted CI/publication remain pending;
no remote push or hosted success is claimed.

Ruff, mypy (48 source files) and all 62 CPU tests passed. All role/CPU/CUDA images
built; real DistilBERT warmup/prediction passed in CPU and GB10 CUDA containers.
Actual kind 0.30.0 / Kubernetes 1.34.0 cluster ran the stack: E11 rolling update
1200/1200 succeeded; E12 process SIGKILL plus Pod deletion 120/120 succeeded,
including the selected pending ID recovered elsewhere; E13 4200/4200 succeeded,
replicas rose 1→4 and returned to one 28.67 s after load. Compute in these traffic
drills was simulated; processes, networking, Redis and Kubernetes were real.
Two earlier deletion/signal attempts are preserved and explicitly not counted
as reclaim proof. The final helper kills only the selected dedicated kind node's
container PID from its ancestor namespace.

Optional CPU sentiment component served real inference on kind. Sync/async/SSE
paths passed; Grafana provisioned four dashboards; Prometheus scraped both gateway
Pod IPs and worker Pod IPs. RBAC allowed named scale writes and denied general
Deployment writes. GPU overlay rendered but is **untested on a GPU cluster**.
Initial dependency-startup restarts were observed; stable readiness preceded runs.
The kind script was rerun for idempotency. Lesson 07, deployment concept,
ADR-0010, diagram 8, experiment records and image/path evidence are delivered.

## Phase 6 — historical completed phase

Built strict-typed pure decide with bounded backlog/utilization/head-age signals,
step limits, cooldowns and raw-demand downscale stabilization. Added deterministic
steady/ramp/spike/sawtooth traces, JSON/PNG simulation harness, token-owned Redis
leadership renewal/release, fail-closed controller, dry-run and official-client
Kubernetes scale adapters with explicit mapping and network timeouts in threads.
Compose defaults to dry-run; actual Kubernetes actuation remains Phase 7.

Ruff, mypy (47 source files) and all 60 CPU tests passed. Coverage includes
table-driven signals/bounds, cooldown/history decay, no history-driven scale-up,
sawtooth stability, fake Kubernetes subresource calls, and real Redis ownership
loss. Standard existing Redis/chaos/model/stream tests remain green.

E8 simulated queue recovered 42 s after spike onset; E9 stabilization reduced
direction reversals 29→1 at higher replica-seconds. E10 targets 5/20/80 were
identical because other signals dominated; target 500 reduced replica-seconds
1775→1755 but increased modeled mean wait 2.025→3.017 s. Simulation capacity and
cold starts are assumptions, not GPU measurements. Real Compose dry-run probe
sends 1600 real HTTP requests and records proposed/current metrics without
changing replicas. Current KEDA 2.21 parameters were verified against its docs;
HPA/KEDA are compared honestly, neither is claimed deployed.

Delivered lesson 06, autoscaling concept, ADR-0009, diagram 7, E8–E10, trace
artifacts/plot and refreshed config/study plan. A Redis lease is not API fencing;
already-issued writes may outlive ownership after a long pause.

## Phase 5 — historical completed phase

Implemented bounded-label gateway/worker metrics, invocation and per-item timing
histograms, structured request/trace logging context, Redis queue/heartbeat/DLQ
exporter with freshness, DNS discovery for worker replicas, Prometheus 3.5.0 and
Grafana 12.1.0 with four automatically provisioned dashboards and five alert rules.
All active Compose scrape targets were healthy. promtool config/rules checks passed.
Ruff, mypy (40 source files) and all 48 CPU tests passed; one opt-in GPU test
remains excluded by default and its Phase 4 real-GPU evidence is retained.

E7 offered 6000 requests per scenario: healthy batching completed all; batch cap
one completed 404 and timed out 5596. Dashboard queue wait rose 0.00106→4.93652 s
while invocation time stayed similar and actual batch fell 2.29→1. Diagnosis used
only saved dashboard expressions/series. Compute was simulated, transport real.
The coarse histogram overestimated timeout p95; EXPERIMENTS.md explains it.

Delivered lesson 05, latency concept, ADR-0008, diagram 6, dashboards.md, E7 and
provisioning artifacts. No screenshots were captured; every panel query is
documented. SLO alert has no target until measured Phase 8 proposal. Alert
notification routing and production auth/retention remain deployment-specific.

## Phase 4 — historical completed phase

Built the separate httpx LLMAdapter and semaphore-bounded worker, streaming and
nonstream chat endpoint, per-request SSE routing, coalesced Redis chunk relay,
independent cancellation polling, deadline handling, bounded event/output/client
buffers, partial-stream reclaim protection and final-text integrity checking.
The fake OpenAI TCP server provides configurable simulated TTFT/token delays,
error/stall injection, diagnostics and synthetic usage. No custom LLM batching
was added: vLLM owns continuous batching, KV cache and GPU scheduling.

The official immutable vllm/vllm-openai image runs as a non-root process in the
gpu profile. The fake-llm profile needs no GPU and produces simulated text.
Both profiles were built, started and exercised through their Compose gateway
with ordered SSE completion and final usage; real vLLM also returned a normal
nonstream completion. They are alternatives, sharing localhost port 8100.

Validation on 2026-11-06: Ruff lint/format passed; mypy passed for 35 source files.
All 46 default CPU tests passed, including cached real sentiment checks, real
Redis integration and process chaos tests. One additional opt-in real-GPU test
passed. CPU tests include chunk order/completeness, upstream failure/status and
stall handling, token cap, concurrency/backlog, normal/stalled disconnect,
slow-client overflow, missing chunk detection and partial-stream reclaim.
Async context managers use AsyncGenerator annotations.

**Real GPU verification completed:** NVIDIA GB10, driver 580.95.05, vLLM 0.33.0,
CUDA 13.0, Qwen/Qwen2.5-0.5B-Instruct. Completion and client disconnect abort were
actually run through HTTP and Redis. The warm verification saw an active engine
sequence, then zero and cached CANCELLED in 41.22 ms using test polling of 20 ms
(default catalog polling is 200 ms). Its 10-token completion had worker TTFT
21.73 ms; the preserved first cold request had TTFT 17289 ms. Full environment,
exact image/model revision and pending checks: [GPU_VERIFICATION.md](GPU_VERIFICATION.md).

E6a real GB10 throughput increased from 139.15 to 1750.81 completion tokens/s
as concurrency rose 1→16 for a 16-request, 32-token burst. Client TTFT p50 fell
1791.50→112.05 ms as queueing fell; worker TTFT rose 18.01→33.30 ms.
E6b's 20 ms window reduced real chunk writes 1040→256 (75.4%) and simulated
writes 1056→125 (88.2%), but slightly reduced throughput and increased client TTFT.
All four experiment artifacts contain three trials per setting and per-request
records. These short warmed runs are not capacity claims or SLOs; see
[EXPERIMENTS.md](EXPERIMENTS.md) for method and caveats.

Delivered lesson 04, LLM concept guide, ADR-0007, diagram 5, GPU verification,
updated architecture/config/glossary/reading plan and measured E6a/E6b evidence.
LLM TTFT/TPOT/token/in-flight hooks and /metrics exist; full platform metrics,
Prometheus scraping and Grafana provisioning remain Phase 5.

## Phase 3 — historical completed phase

Built worker TTL registry and busy/in-flight heartbeats, live worker counts,
capacity-bounded XAUTOCLAIM with extended XPENDING delivery counts, persistent
poison retries and DLQ quarantine, cached duplicate replay, deadlines and
cancellation before/after compute, and tested graceful SIGTERM draining.

Gateway now has Redis Lua token buckets, atomic retained-depth admission,
no-worker rejection, body-byte limits, async acceptance/result polling with
API-key ownership, cancellation on timeout/disconnect, and a bounded blocking
Redis connection pool. Response and cache publication precede transactional
XACK/XDEL cleanup. Compose workers have a 15-second stop grace.

Validation on 2026-11-06: 32 tests passed, including two real-process chaos tests,
real Redis, fake-clock batch/heartbeat tests and cached real CPU model correctness.
Ruff lint/format and strict-common mypy passed (26 source files). Redis 7.4.12,
redis-py 8.1.0, Python 3.12.3 on the ARM aarch64 host. No model downloads in tests.

Three Compose replicas served 120 real HTTP requests across all three worker IDs;
the distribution drill also verified async 202 acceptance and 200 result polling.
E1 compatibility was rerun while retaining its historical Phase 1 measurements.
The CPU sentiment integration and final container curl remain green.

E4 median SIGKILL recovery: 723.87/1239.40/2242.55 ms for 500/1000/2000 ms idle
thresholds, with all 36 requests recovered and pending/retained work cleared.
E5 with admission held depth at eight and completed all eight accepted requests
per trial; without admission, depth reached 120 and 100–104 requests timed out.
Compute is simulated; Redis, processes, signals and HTTP handlers are real.
The initial pool-exhaustion run and confirming probe are preserved separately.

Delivered lesson 03 with command-by-command SIGKILL walkthrough, reliability
concept guide, ADRs 0005–0006, diagram 4, updated architecture/config/glossary,
reading plan, measured experiments and raw artifacts. Details and caveats are
in EXPERIMENTS.md. GPU/vLLM verification was pending at the end of Phase 3;
Phase 4 results above supersede that historical status.

## Phase 2 — historical completed phase

Built ModelRunner, TorchClassifierRunner and DynamicBatcher. DistilBERT SST-2
loads from trusted safetensors, selects the configured device, uses eval and
inference_mode, dynamically pads, and truncates at 256 tokens. CPU thread count
is configurable. Load and warmup finish on the executor before consumption.

The consumer reads only free capacity; total local active/staged/queued work is
bounded, including results awaiting delivery. Supervised result fan-out does
not delay an available runner. The batcher collects while compute is busy, honors the first arrival
deadline, checks per-item validation/expiry/cancel, fans out by request ID, and
isolates failed batches with one individual retry per input. Workers honor
externally supplied cancel flags; gateways do not create them yet.

Delivered the CPU torch Compose profile and persistent non-root model cache,
runner-only E2 sweep, in-memory E3 sweep, explicit host model preparation command,
lesson 02, batching concept guide, ADR-0004, diagram 3 and refreshed documentation.

Verification: 19 tests passed (11 foundation/batcher unit, 1 cached real-model
correctness, 6 ordinary real-Redis integration, 1 cached real-model integration).
Ruff lint/format and mypy passed. The model correctness test checks mixed-length
padded batches against singles, including 256-token truncation, at 1e-5 tolerance.
Real HTTP-to-Redis sentiment inference passed both integration and container curl.
Compose builds and startup, fake smoke, real sentiment curl, and clean shutdown
were executed. Executor tests require local socket access in the agent sandbox.

E2/E3 ran on CPU on 2026-11-06: 20-logical-CPU ARM Cortex-X925/Cortex-A725 host,
PyTorch 2.15.0+cpu, Transformers 4.57.6, one intra-op thread. E2 real throughput
increased from 38.77 items/s at batch 1 to 238.11 at 16 and 303.41 at 64, while
batch latency rose. E3 low-load longer waits added latency without batching;
high-load zero wait already formed nearly full batches. See EXPERIMENTS.md and
raw results for limitations. These are short controlled measurements, not SLOs.
GPU inference remains unverified.

## Phase 1 — historical implementation and verification

Built the Python 3.12 uv project and lockfile, typed settings and JSON schemas,
Redis key helpers and client factory, injectable monotonic Clock, JSON logging,
API-key hashing and comparison, FastAPI lifespan, synchronous inference endpoint,
single inbox reader, bounded waiters, timeouts, health/readiness, and basic models
listing. The fake worker pulls one item at a time, executes simulated compute in
a dedicated thread, writes its response before ACK, then deletes completed work.

Delivered a multi-stage non-root application image, Redis 7 with AOF storage,
gateway and worker Compose services, and curl smoke script. Redis is not exposed
on a host port. No inputs or API keys are logged by application code.

Architecture diagrams 1–2, conceptual request states, failure table, configuration
reference, glossary, two lessons, study plan, Redis concept guide, ADRs 0001–0003,
and E1 with raw measurements are present.

Verification was executed on 2026-11-06 using CPU-only simulated inference.
Owner reports Ubuntu 24 LTS, Docker and an NVIDIA GPU (“DGX 10G”); GPU identity,
memory and inference have not been verified. The runtime host is Linux aarch64.
Docker required execution-environment escalation, which was granted.

## Phase 1 verification (historical)

- Ruff lint and format check passed; mypy src passed for all 16 source files
  (strict common). Unit tests: 3 passed. Real-Redis integration tests: 5 passed.
- Real Redis 7.4.12 via Testcontainers; no fakeredis.
- Compose image build and healthy startup verified; curl smoke round trip passed.
- E1: success leaves pending=0 and XLEN=0; worker SIGKILL leaves pending=1 and
  XLEN=1, with HTTP 504. See EXPERIMENTS.md and e1-results.json.
- All executable command blocks in the delivered docs were run. Command signatures
  in the study cheat sheet are reference notation; their actual uses are in code
  and the experiment. No GPU result is claimed.

## Deferred — according to the phase plan

Phase 8: load testing, runbook and measured SLO proposal.
Hosted GitHub Actions/registry publication and GPU Kubernetes verification pending.

## Current limitations

The default classifier deadline is 5 seconds versus 30 seconds for reclaim idle.
A killed request therefore times out before default reclaim and is expired later.
Successful synchronous recovery requires a tuned deadline/idle/compute budget;
E4 records its controlled, shorter idle thresholds rather than hiding this gap.

Cancellation flags are best effort and cannot interrupt CPU executor code. LLM
cancellation closes upstream HTTP independently of token arrivals. SSE failures
after headers cannot change HTTP status; clients must read error frames. Partial
streams cannot resume, and reclaimed partial generation fails explicitly. Inbox
trimming/content loss is detected at final text comparison, after any partial
text has already been shown. Tokens without final usage are not counted as if
reported. TPOT from HTTP chunks is an estimate, not an engine kernel timing.
SIGTERM drains accepted work within available grace; SIGKILL or Redis failure
can leave pending entries. Stale heartbeats and worker shutdown race with admission.
There is no exactly-once guarantee: duplicate compute, replies or DLQ evidence are
possible around crashes. Conditional RUNNING cache updates do not erase finals,
but concurrent final writers can still race.

Retained-depth admission is conservative and includes pending entries. Local
Redis AOF is not HA or an assurance of zero lost writes. Result and inbox storage
expire; DLQ retention is approximate and contains original input data. There is
no DLQ replay endpoint or automatic pruning of all old consumer metadata.
HTTP body bytes are capped; slow-client/ingress concurrency policies remain a
deployment concern. Model readiness HTTP probes and Kubernetes termination grace
are implemented in Phase 7. GPU Kubernetes, other models/hardware and long-context
saturation remain pending; local GB10 inference and abort are verified.

Owner .env remains untouched and excluded from Git/Docker. The owner's staged
.gitignore modification was preserved and excluded from agent commits.

## Next

Owner authorized continuation through Phase 8. Next: Locust scenarios and real
load benchmarks, executed runbook drills, Little's Law prediction/measurement,
measured local SLO proposal and the final Definition of Done audit.
