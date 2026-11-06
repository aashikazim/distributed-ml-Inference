# Progress

## Phase 1 — implementation and verification

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

## Verification

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

Phase 2: real DistilBERT model, ModelRunner interface, dynamic batching, numerical
correctness and throughput/wait experiments.

Phase 3: heartbeat registry/live worker counts, admission using max_queue_depth,
rate limiting, reclaim, retry counts/DLQ, result cache/polling, cancellation flags,
and tested failure recovery. A graceful stop flag already lets the current fake
request finish, but the full shutdown contract and chaos suite remain Phase 3.
Malformed stream envelopes can stop a worker; poison-message recovery is not
implemented. Arbitrary runner errors can also leave pending work.

Phase 4: LLM/SSE adapter, fake OpenAI server, GPU verification instructions and
real-vLLM verification. **Real-vLLM verification pending.**

Phase 5: metrics endpoint/exporter, dashboards and structured logging enhancements.
Current JSON application logs carry correlation fields for completed requests.

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

Stop here for owner review. Phase 2 will add the real sentiment runner and dynamic
batching, its deterministic tests, lesson, ADR-0004 and measured E2/E3 results.
