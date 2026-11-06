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

Model fields: backend (fake or torch), fake.base_ms, fake.per_item_ms,
fake.jitter_ms (simulated cost), max_input_chars, request_timeout_ms, max_queue_depth.
Queue admission enforcement arrives in Phase 3; max_queue_depth is policy only for now.
Phase 1 accepted only the fake backend; Phase 2 adds the configured sentiment model. Test-only INFER_TEST_REDIS_URL can
point integration tests at a dedicated real Redis instead of Testcontainers.
INFER_SMOKE_API_KEY is consumed only by scripts/smoke.sh.

Redis has no published Compose port. Production needs authentication
(requirepass or ACLs), TLS, durable storage, and a separate availability design.
Do not use the example API key for an exposed deployment.

## Phase 2 settings

| Variable | Default | Purpose |
|---|---|---|
| INFER_DEVICE | auto | cuda if available, otherwise cpu; cpu/cuda force selection |
| INFER_TORCH_THREADS | 1 | PyTorch intra-op CPU threads per worker |
| INFER_TORCH_FP16 | false | Optional half precision on CUDA only; rejected on CPU |
| INFER_MODEL_CACHE | /tmp/infer-model-cache | Trusted Hugging Face cache location |
| INFER_MODEL_LOCAL_FILES_ONLY | false | Prevent network downloads and require cached weights |

Torch model fields: hf_id (required trusted model name), revision (default main),
task (classification), max_batch_size (default 1), max_wait_ms (default 0),
prefetch_bound (default twice max_batch_size; must be at least max_batch_size).
The bound counts active, staged and queued items together. The default catalog
now enables sentiment (torch, batch 16, wait 10 ms) and fake-classifier (simulated,
batch 32, wait 10 ms). Existing minimal fake configurations remain batch size one.

The ml optional dependency extra installs CPU PyTorch and Transformers. The
worker-sentiment Compose service is enabled with the torch profile and uses a
persistent writable /models cache, CPU device and one intra-op thread.
Weights must be safetensors; remote model code is disabled. Model downloads use
Hugging Face Hub's connection/read timeouts (HF_HUB_ETAG_TIMEOUT=10 and
HF_HUB_DOWNLOAD_TIMEOUT=30 in Compose); the inference hot path makes no model HTTP
calls after warmup. These HF_* variables are library download controls, not
application settings. Transformers is constrained to major version 4 to keep the
implemented runner API stable; the exact dependency versions are locked.
