# Progress

## Phase 2 — complete

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

Phase 3: heartbeat registry/live worker counts, admission using max_queue_depth,
rate limiting, reclaim, retry counts/DLQ, result cache/polling, gateway-driven cancellation,
and tested failure recovery. A graceful stop flag already lets the current fake
accepted batches finish, but the full shutdown contract and chaos suite remain Phase 3.
Malformed stream envelopes can stop a worker; poison-message recovery is not
implemented. Runner invocation failures are now isolated with bounded individual retries;
response/transport failures can still leave pending work.

Phase 4: LLM/SSE adapter, fake OpenAI server, GPU verification instructions and
real-vLLM verification. **Real-vLLM verification pending.**

Phase 5: metrics endpoint/exporter, dashboards and structured logging enhancements.
Current JSON application logs carry correlation fields and actual batch sizes
for completed requests.

Phases 6–8: autoscaling, Kubernetes/CI, load tests, operational runbook and measured
SLOs. No production readiness or automatic recovery claim.

## Known limitations

HTTP timeout does not cancel queued or running work. New workers read only new
deliveries and cannot recover a dead consumer's pending entry. Reply/ACK/delete
are separate operations, so failures can leave duplicates or retained entries.
Queue depth policy is not enforced yet; HTTP bodies are not byte-capped before
JSON parsing. /v1/models reports retained stream depth, not live worker counts.
A gateway crash loses its local waiters; there is no durable client result lookup.
Inbox trimming is approximate. Local Redis AOF is not HA.

Existing owner .env was left untouched and excluded from Git and Docker context.
AGENTS.md was read as the source of truth and left as an owner-provided file.

## Next

Stop here for owner review. Phase 3 will add heartbeats, worker registry,
reclaim/DLQ, admission control, rate limiting, async result lookup, gateway-driven
cancellation, and reliability experiments E4/E5. Do not begin it automatically.
