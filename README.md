# Distributed ML Inference Platform

A Python 3.12 learning project: HTTP requests travel through Redis Streams to a
pull-based worker and return through a per-gateway inbox. **Phase 1 only** is
implemented. The classifier and compute cost are explicitly **simulated**.

The long-term plan adds dynamic PyTorch batching, vLLM streaming, reliability,
observability, autoscaling and Kubernetes. These are not current capabilities.

## Run locally

Prerequisites: Python 3.12, uv, Docker Engine and Docker Compose. Run from this
repository's root. The public key below is for local demonstration only.

```bash
export UV_CACHE_DIR=/tmp/infer-uv-cache
uv sync
export INFER_API_KEYS='["phase1-verification-key"]'
export INFER_SMOKE_API_KEY=phase1-verification-key
docker compose -f deploy/docker/docker-compose.yml up --build -d --wait
./scripts/smoke.sh
```

The cache override makes the commands work in the restricted agent environment;
you may use another writable cache location. Application secrets are supplied
through environment variables. The application never loads your existing .env.
Redis has no published host port; the gateway binds localhost:8000.

Make a request:

```bash
curl --fail --silent --show-error -H "X-API-Key: $INFER_SMOKE_API_KEY" -H 'Content-Type: application/json' -d '{"input":"Learning Redis Streams"}' http://localhost:8000/v1/infer/fake-classifier
```

Expect output.label SIMULATED, output.input_chars, and meta containing worker_id,
batch_size, queue_ms, batch_wait_ms and inference_ms. There is no accuracy claim.
Only synchronous inference is supported; async query parameters are rejected.
Authenticated /v1/models lists the allowlist and retained queue depth.
/healthz is process liveness; /readyz checks Redis and the inbox reader.

## Verify and study

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy src
uv run pytest -m "not integration and not chaos and not gpu"
uv run pytest -m integration
uv run python scripts/experiment_e1.py
```

Integration tests and E1 start disposable Redis 7 containers via Testcontainers.
E1 deliberately kills its own worker subprocess; it does not kill the Compose
worker or modify shared model streams. It overwrites docs/e1-results.json with
your new measurements.

[Start learning](docs/README.md), then inspect [architecture](docs/ARCHITECTURE.md),
[configuration](docs/CONFIG.md), [measured E1 results](docs/EXPERIMENTS.md), and
[progress and limits](docs/PROGRESS.md).

Stop the local stack, retaining its Redis volume:

```bash
docker compose -f deploy/docker/docker-compose.yml down
```
