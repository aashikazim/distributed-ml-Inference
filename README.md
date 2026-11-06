# Distributed ML Inference Platform

A Python 3.12 learning project: HTTP requests travel through Redis Streams to
pull-based workers and return through per-gateway inboxes.

**Phases 1–2 are implemented:** authenticated synchronous inference, a **simulated**
fake backend, real DistilBERT SST-2 sentiment inference, and bounded dynamic
batching. Reliability controllers, LLM streaming, metrics dashboards, autoscaling
and Kubernetes remain later phases.

## Run the fake backend

Prerequisites: Python 3.12, uv, Docker Engine and Compose. Run from the repository
root. These are public demonstration keys, not production secrets.

```bash
export UV_CACHE_DIR=/tmp/infer-uv-cache
uv sync --extra ml
export INFER_API_KEYS='["phase1-verification-key"]'
export INFER_SMOKE_API_KEY=phase1-verification-key
docker compose -f deploy/docker/docker-compose.yml up --build -d --wait
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
docker compose -f deploy/docker/docker-compose.yml --profile torch up --build -d --wait
docker compose -f deploy/docker/docker-compose.yml --profile torch logs --tail 12 worker-sentiment
curl --fail --silent --show-error -H "X-API-Key: $INFER_SMOKE_API_KEY" -H 'Content-Type: application/json' -d '{"input":"I loved this movie."}' http://localhost:8000/v1/infer/sentiment
```

Expect output.label POSITIVE with scores and timing metadata. max_batch_size=16
and max_wait_ms=10 are starting policies, not an SLO. A lone request normally
runs alone; several concurrent requests can share one execution.
Only synchronous inference is supported. Authenticated /v1/models lists the
allowlist and retained stream depths; /healthz checks process liveness and
/readyz checks Redis and the inbox reader.

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
```

Real-model checks use local_files_only=True and skip if dependencies or cached
weights are absent. Integration tests use disposable Redis 7 containers through
Testcontainers. Fake-clock unit tests cover flush timing and busy-runner growth.
The agent environment requires granted socket access for Docker and asyncio
thread-wakeup operations; ordinary local execution does not need that workaround.

See [E2/E3 commands and measurements](docs/EXPERIMENTS.md). E2 is runner-only;
E3 exercises the actual in-memory batcher at low and high load. Fake results are
labeled simulated and kept separate from real CPU model results.

## Learn and stop

Start with [the docs index](docs/README.md), then [architecture](docs/ARCHITECTURE.md),
[lesson 02](docs/learning/02-dynamic-batching.md), [configuration](docs/CONFIG.md)
and [progress](docs/PROGRESS.md).

```bash
docker compose -f deploy/docker/docker-compose.yml --profile torch down
```

This stops containers while retaining Redis and model volumes. E1 remains
reproducible through scripts/experiment_e1.py; see its historical measured record.
No GPU or real-vLLM verification is claimed.
