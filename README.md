# Distributed ML Inference Platform

A Python 3.12 learning project: HTTP requests travel through Redis Streams to
pull-based workers and return through per-gateway inboxes.

**Phases 1–5 are implemented:** authenticated sync/async inference, a **simulated**
fake backend, real DistilBERT sentiment inference, dynamic batching, live worker
registry, reclaim/DLQ, deadlines/cancellation, rate limiting and atomic admission.
Streaming chat runs through a bounded LLM adapter against a simulated OpenAI
server or real vLLM. Real Qwen inference and cancellation were verified on NVIDIA
GB10. Prometheus/Grafana dashboards and the queue exporter are provisioned.
Autoscaling and Kubernetes are later phases.

## Run the fake backend

Prerequisites: Python 3.12, uv, Docker Engine and Compose. Run from the repository
root. These are public demonstration keys, not production secrets.

```bash
export UV_CACHE_DIR=/tmp/infer-uv-cache
uv sync --extra ml
export INFER_API_KEYS='["phase1-verification-key"]'
export INFER_SMOKE_API_KEY=phase1-verification-key
docker compose -f deploy/docker/docker-compose.yml up --build -d --wait --scale worker-fake=3
./scripts/smoke.sh
```

The ML extra is needed for the Phase 2 model checks and benchmarks. Fake-only
development can still use uv sync without that extra. The cache override handles
the restricted agent environment; another writable cache is fine.

API keys come from environment variables. The application never loads your
existing .env. Redis has no published host port; the HTTP port binds to localhost.

## Enable CPU sentiment inference

The torch profile builds the CPU PyTorch worker image and downloads trusted
safetensors weights into a persistent model volume. It warms up before reading
requests. Gateway readiness does not yet check model availability; allow the model
worker to finish startup before sending inference requests.

```bash
docker compose -f deploy/docker/docker-compose.yml --profile torch up --build -d --wait --scale worker-fake=3
docker compose -f deploy/docker/docker-compose.yml --profile torch logs --tail 12 worker-sentiment
curl --fail --silent --show-error -H "X-API-Key: $INFER_SMOKE_API_KEY" -H 'Content-Type: application/json' -d '{"input":"I loved this movie."}' http://localhost:8000/v1/infer/sentiment
```

Expect output.label POSITIVE with scores and timing metadata. max_batch_size=16
and max_wait_ms=10 are starting policies, not an SLO. A lone request normally
runs alone; several concurrent requests can share one execution.
Authenticated /v1/models lists retained depth and live_workers. /healthz is
process liveness; /readyz checks Redis and the inbox reader. No live model worker
produces 503 on inference rather than silently accepting work during warmup.

## Distribution, async results and reliability

With three fake replicas running, this real-HTTP drill verifies distribution and
also accepts an async request and polls its final result:

```bash
uv run --extra ml python -m tools.distribution_smoke --output docs/results/p3-compose-distribution.json
```

POST /v1/infer/{model}?async=true returns 202 and a request_id. Authenticated
GET /v1/results/{request_id} returns 202 while pending, the final output/error,
or 404 if unknown/expired or owned by another key. Cache TTL defaults to 300 seconds.

Inference returns 429 with Retry-After for a per-key rate limit, or 503 with
Retry-After for no accepting workers, retained-depth overload or waiter capacity.
Timeout/disconnect sets a cancellation flag; CPU compute already running cannot
be interrupted, but its late output is discarded. Body bytes and input length
are capped. DLQ entries contain original inputs and require controlled Redis access.

Important timing policy: the catalog uses a 5-second classifier deadline and
30-second reclaim idle threshold. Those defaults cannot finish a killed sync
request before timeout. E4 uses documented shorter idle thresholds for small
known fake batches and a longer request deadline. Tune them together; do not
copy the drill's short idle value to a slow real model.

## Verify

Tests do not download models. Explicitly prepare the trusted host cache for the
real-model checks; the Compose model volume is separate from that host cache.

```bash
uv run --extra ml python -m tools.bench.prepare_model
uv run --extra ml ruff check .
uv run --extra ml ruff format --check .
uv run --extra ml mypy src
uv run --extra ml pytest -m "not integration and not chaos and not gpu"
uv run --extra ml pytest -m integration
uv run --extra ml pytest -m chaos
```

Real-model checks use local_files_only=True and skip if dependencies or cached
weights are absent. Integration tests use disposable Redis 7 containers through
Testcontainers. Fake-clock unit tests cover flush timing and busy-runner growth.
The agent environment requires granted socket access for Docker and asyncio
thread-wakeup operations; ordinary local execution does not need that workaround.

See [measured experiments](docs/EXPERIMENTS.md): E2/E3 cover batching; E4/E5
cover SIGKILL recovery and overload with/without admission. The reliability drills
start disposable Redis containers and worker subprocesses, never killing Compose
workers. Fake compute results are explicitly labeled simulated.

## Learn and stop

Start with [the docs index](docs/README.md), then [architecture](docs/ARCHITECTURE.md),
[lesson 04](docs/learning/04-llm-serving.md), [configuration](docs/CONFIG.md)
and [progress](docs/PROGRESS.md).

```bash
docker compose -f deploy/docker/docker-compose.yml --profile torch down
```

This stops containers while retaining Redis and model volumes. E1 remains
reproducible through scripts/experiment_e1.py; see its historical measured record.
GPU details, image pin, cold-start evidence and pending checks are in
[GPU verification](docs/GPU_VERIFICATION.md).


## Streaming chat

Use one chat profile at a time. The CPU fake profile produces **simulated** text
and timings; it verifies the same HTTP/Redis path without model weights or CUDA.
With the demonstration environment above:

```bash
docker compose -f deploy/docker/docker-compose.yml --profile fake-llm up --build -d --wait
curl --fail --silent --show-error -N -H 'X-API-Key: phase1-verification-key' -H 'Content-Type: application/json' -d '{"model":"chat","messages":[{"role":"user","content":"Explain queues."}],"max_tokens":32,"stream":true}' http://localhost:8000/v1/chat/completions
docker compose -f deploy/docker/docker-compose.yml --profile fake-llm down
```

Expect ordered data events, usage/meta and data: [DONE]. Set stream=false for a
single chat.completion JSON response. The platform model name is chat; the worker
uses its configured Qwen engine model. Tokens above 512 are rejected with 422.
A brief no-worker 503 during registration is expected; /v1/models reports capacity.
X-Request-ID identifies the cached full result. Clients must inspect SSE errors:
a failure after headers still has HTTP 200, followed by an error event and DONE.
Disconnect closes upstream generation; partially emitted streams are not resumed.

For real GPU use the mutually exclusive gpu profile and exact commands in
[GPU_VERIFICATION.md](docs/GPU_VERIFICATION.md). LLM metrics are exposed; full
Prometheus/Grafana provisioning is enabled by default. Learn the boundary in
[lesson 04](docs/learning/04-llm-serving.md), [LLM concepts](docs/concepts/llm-serving.md),
and [ADR-0007](docs/adr/0007-llm-adapter.md). E6a/E6b include measured fake and
real GPU results, separately labeled, with raw per-request artifacts.

Monitoring opens at http://localhost:3000 (read-only local Grafana) and
http://localhost:9090 (Prometheus). See [lesson 05](docs/learning/05-observability.md)
and [panel queries](docs/dashboards.md). E7 records a real dashboard-based
diagnosis with simulated model compute.
