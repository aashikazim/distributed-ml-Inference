# Distributed ML Inference Platform

Learn how a model-serving platform distributes work, batches inference, survives
worker loss, streams generated text and adjusts capacity from serving signals.
Python 3.12, FastAPI, Redis Streams, PyTorch, vLLM, Prometheus/Grafana and Kubernetes.

Phases 1–8 are implemented with code, tests, diagrams and beginner lessons.
**Verified locally:** real CPU DistilBERT, real GB10 vLLM generation/cancellation,
Compose monitoring, real kind scaling/rollout/process-loss recovery, load tests
and operational drills. **Still unverified:** hosted CI/GHCR publication, GPU
Kubernetes, production Redis HA, cloud/WAN and long-duration SLOs. The
[Definition of Done audit](docs/VERIFICATION.md) separates these claims.

## Start on CPU

Prerequisites: uv, Python 3.12 and Docker Engine/Compose. Run from the repository
root. The key below is a public demonstration value; provide your own for use
beyond a local learning session. Your existing .env is not loaded by the app.

```bash
export UV_CACHE_DIR=/tmp/infer-uv-cache
uv sync --frozen --extra ml
export INFER_API_KEYS='["phase1-verification-key"]'
export INFER_SMOKE_API_KEY=phase1-verification-key
docker compose -f deploy/docker/docker-compose.yml --profile fake-llm up --build -d --wait
./scripts/smoke.sh
curl --fail --silent --show-error -N -H 'X-API-Key: phase1-verification-key' -H 'Content-Type: application/json' -d '{"model":"chat","messages":[{"role":"user","content":"Explain queues."}],"max_tokens":32,"stream":true}' http://localhost:8000/v1/chat/completions
```

Expect a SIMULATED classifier output and synthetic token0 through token31,
followed by usage/meta and data: [DONE]. Fake model/token timings are **simulated**;
networking, Redis, cancellation and monitoring are real.

API: http://localhost:8000. Read-only development Grafana: http://localhost:3000.
Prometheus: http://localhost:9090. Redis has no published Compose port. Four
Grafana dashboards provision automatically; workers/engine are discovered for
scraping. The default autoscaler logs recommendations without scaling Compose.
See [dashboard queries](docs/dashboards.md) and [configuration](docs/CONFIG.md).

## Add the real CPU classifier

The torch profile installs CPU wheels, caches trusted safetensors in a persistent
model volume and warms before readiness. This combined CPU setup was verified:

```bash
docker compose -f deploy/docker/docker-compose.yml --profile fake-llm --profile torch up --build -d --wait
curl --fail --silent --show-error -H "X-API-Key: $INFER_SMOKE_API_KEY" -H 'Content-Type: application/json' -d '{"input":"I loved this movie."}' http://localhost:8000/v1/infer/sentiment
```

Expect POSITIVE plus scores and timing metadata. Inference is real; the fake chat
server remains simulated. API /v1/models reports live workers and retained depth.
The ML extra is optional for fake-only app execution, but needed for local real
model tests. Tests never download weights; prepare the host cache explicitly with
tools.bench.prepare_model as described in the [benchmark guide](docs/BENCHMARKS.md).

## Real GPU chat

The mutually exclusive gpu profile uses the pinned official vLLM server. Stop
fake-llm first: both profiles share chat and localhost:8100. Real Qwen generation,
upstream cancellation and Prometheus engine scraping were verified on NVIDIA GB10.
The first cold completion took seconds; engine health alone did not establish
warmed latency. Exact flags, image/model revision, commands and pending checks:
[GPU_VERIFICATION.md](docs/GPU_VERIFICATION.md). CUDA classifier images were also
built and exercised; that is separate from an untested GPU Kubernetes overlay.

## API behavior to learn

- POST /v1/infer/{model}: authenticated sync inference; ?async=true returns 202.
- GET /v1/results/{request_id}: owner-authenticated pending/final lookup with TTL.
- POST /v1/chat/completions: text chat subset, stream true for SSE, false for JSON.
- GET /v1/models: allowed models, live worker counts and retained depth.
- /healthz is process liveness; /readyz checks serving dependencies; /metrics is internal telemetry.

401 rejects auth, 422 rejects invalid limits, 429 is per-key rate limiting, 503
is capacity/availability rejection, and 504 is deadline expiry. Retry-After is
provided where specified. After SSE headers, failures arrive as error events
followed by DONE; HTTP 200 alone does not establish generation success.
X-Request-ID supports late cache lookup; partially emitted streams cannot resume.

Success meta separates Redis queue age, local collection wait and full shared
inference duration. Live responses add gateway total/residual timing; cached
polls retain canonical worker timings without inventing the original gateway's
elapsed time. [Architecture](docs/ARCHITECTURE.md) defines each boundary.

Defaults matter: Compose classifier deadline 5 s is shorter than reclaim idle
30 s. The kind drills use documented 30 s/2 s budgets for small known batches.
Do not infer successful default synchronous crash recovery from those tuned tests.
Queues, prefetch, streams and concurrency are bounded; there is no exactly-once
compute or Redis HA claim.

## Kubernetes and verification

With kind/kubectl installed, scripts/kind-up.sh creates the dedicated
kind-infer-platform context using /tmp/infer-kind-kubeconfig, loads the local
image and waits for readiness. The gateway is http://localhost:8001. Follow
[lesson 07](docs/learning/07-kubernetes-deployment.md) for executed setup and
[benchmarks](docs/BENCHMARKS.md) for the optional real CPU component.

E11 rolled workers under load with 1200/1200 successes. E12 recovered a selected
pending request elsewhere after actual process SIGKILL and Pod deletion; 120/120
succeeded. E13 grew 1→4 workers and returned to one; 4200/4200 succeeded.
These reliability/scaling loads use simulated compute on real Kubernetes.

```bash
uv run --extra ml ruff check .
uv run --extra ml ruff format --check .
uv run --extra ml mypy src
uv run --extra ml pytest
```

The default suite passed 62 tests, including real Redis/Testcontainers, process
chaos and cached real-model checks; the GPU marker is excluded by default.
An additional opt-in real-vLLM test passed. CI defines equivalent checks and role
image builds with conditional GHCR publication; no remote run/push is claimed.

## Learn, measure and operate

Start with [the learning index](docs/README.md), use the **Run → Read → Break → Explain**
[study plan](docs/learning/README.md), and keep the [glossary](docs/GLOSSARY.md) nearby.
Lessons 00–08 connect code to decisions, diagrams, controlled failures and quizzes.

- [Experiments E1–E13](docs/EXPERIMENTS.md): predictions, measured results and surprises.
- [Benchmarks](docs/BENCHMARKS.md): hardware, source, method and actual numbers.
- [Capacity worksheet](docs/CAPACITY_PLANNING.md): predict occupancy, then sample it independently.
- [Local SLO proposal](docs/SLO.md): measured candidates, not a production promise.
- [Runbook](docs/RUNBOOK.md): six executed failure drills and recovery steps.
- [Tuning](docs/TUNING.md): diagnose one boundary before changing a control.
- [Capstone](docs/learning/08-capstone.md) and [verification audit](docs/VERIFICATION.md).

The final fixed-replica check measured p95 110.4 ms for simulated classification
at 40 req/s and 60.8 ms for real CPU sentiment at 5 req/s. The proposed 250 ms
target applies only to its measured scope. Locust steady/ramp/spike/mixed scenarios
had zero reported failures; their closed-loop results are not arbitrary-arrival
capacity guarantees.

Stop the CPU demonstration while retaining Redis/model volumes:

```bash
docker compose -f deploy/docker/docker-compose.yml --profile fake-llm --profile torch down
```

Production TLS/auth, Redis HA/backups, durable telemetry, alert notification
routing and representative soak/cloud testing remain deployment work. No web UI,
training, custom GPU kernels, alternate broker or service mesh was added.
