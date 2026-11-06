# Configuration
Settings are read from environment variables only; the application does not load .env.
Compose reads its API key interpolation from the shell. API keys are JSON arrays.
Model policy is loaded from config/models.yaml and validated at startup.

| Variable | Default | Purpose |
|---|---|---|
| INFER_API_KEYS | required | Nonempty JSON array of secret API keys; hashed on gateway startup |
| INFER_REDIS_URL | redis://localhost:6379/0 | Redis connection; use rediss:// for TLS |
| INFER_MODELS_PATH | config/models.yaml | Trusted allowlist YAML path |
| INFER_WORKER_MODEL | fake-classifier | One model per worker process |
| INFER_REDIS_TIMEOUT_S | 3 | Connect and socket timeout, greater than blocking reads |
| INFER_INBOX_TTL_S | 120 | Gateway inbox expiry, refreshed during reads |
| INFER_MAX_GATEWAY_INFLIGHT | 1000 | Maximum concurrent response waiters per gateway |

Model fields: backend (currently fake only), fake.base_ms, fake.per_item_ms,
fake.jitter_ms (simulated cost), max_input_chars, request_timeout_ms, max_queue_depth.
Queue admission enforcement arrives in Phase 3; max_queue_depth is policy only for now.
No real model names are accepted in Phase 1. Test-only INFER_TEST_REDIS_URL can
point integration tests at a dedicated real Redis instead of Testcontainers.
INFER_SMOKE_API_KEY is consumed only by scripts/smoke.sh.

Redis has no published Compose port. Production needs authentication
(requirepass or ACLs), TLS, durable storage, and a separate availability design.
Do not use the example API key for an exposed deployment.
