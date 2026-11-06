# Progress

## Phase 3 — complete

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
in EXPERIMENTS.md. GPU/vLLM verification remains pending.

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

Phase 4: LLM adapter, SSE, fake OpenAI server, chunk coalescing, concurrency caps,
cancellation of upstream generation, GPU verification instructions.
**Real-vLLM verification pending.**

Phase 5: metrics/exporter, provisioned dashboards, alerts and richer logging.
Phases 6–8: autoscaling, Kubernetes/CI, load testing, runbook and measured SLOs.

## Current limitations

The default classifier deadline is 5 seconds versus 30 seconds for reclaim idle.
A killed request therefore times out before default reclaim and is expired later.
Successful synchronous recovery requires a tuned deadline/idle/compute budget;
E4 records its controlled, shorter idle thresholds rather than hiding this gap.

Cancellation flags are best effort and cannot interrupt CPU executor code.
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
deployment concern. Model HTTP probes and Kubernetes termination grace arrive
in Phase 7. No GPU inference result is claimed.

Owner .env remains untouched and excluded from Git/Docker. The owner's staged
.gitignore modification was preserved and excluded from agent commits.

## Next

Stop for owner review. Phase 4 will add the LLM worker adapter, SSE gateway,
fake OpenAI-compatible server, bounded upstream concurrency, streaming
cancellation and coalescing experiments. Do not begin it automatically.
