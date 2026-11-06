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
| INFER_REDIS_TIMEOUT_S | 3 | Connect, socket and pool-wait timeout, greater than blocking reads |
| INFER_INBOX_TTL_S | 120 | Gateway inbox expiry, refreshed during reads |
| INFER_MAX_GATEWAY_INFLIGHT | 1000 | Maximum concurrent response waiters per gateway |

Model fields: backend (fake or torch), fake.base_ms, fake.per_item_ms,
fake.jitter_ms (simulated cost), max_input_chars, request_timeout_ms, max_queue_depth.
Phase 3 enforces max_queue_depth as the retained-work admission bound.
Phase 1 accepted only the fake backend; Phase 2 adds the configured sentiment model. Test-only INFER_TEST_REDIS_URL can
point integration tests at a dedicated real Redis instead of Testcontainers.
INFER_SMOKE_API_KEY is consumed by scripts/smoke.sh and tools/distribution_smoke.py.

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

## Phase 3 reliability and admission

| Variable | Default | Purpose |
|---|---|---|
| INFER_HEARTBEAT_INTERVAL_S | 3 | Registry refresh interval |
| INFER_HEARTBEAT_TTL_S | 10 | Registry expiry; must exceed refresh interval |
| INFER_RESULT_TTL_S | 300 | Accepted-request and final-result cache TTL |
| INFER_CANCEL_TTL_S | 120 | Best-effort cancellation flag lifetime |
| INFER_RATE_LIMIT_PER_S | 100 | Per-key inference token refill per second |
| INFER_RATE_LIMIT_BURST | 200 | Maximum tokens per key |
| INFER_ADMISSION_ENABLED | true | Check live workers and retained work depth; disable only in controlled E5 |
| INFER_MAX_BODY_BYTES | 65536 | Count actual HTTP body bytes before parsing |

Model fields: reclaim_idle_ms (30000), reclaim_interval_ms (2000),
max_deliveries (3). Reclaim idle time must exceed the slowest expected locally
buffered batch plus inference and reply time. Reduced values in chaos tests are
for controlled short fake workloads only. Deliberately setting it too short can
duplicate live work. An entry is quarantined when its delivery count is greater
than max_deliveries. DLQ streams retain approximately 10000 recent entries.

Admission uses XLEN atomically with enqueue, so max_queue_depth bounds retained
undelivered plus pending work. Heartbeats include accepting, device, backend,
in_flight (all locally outstanding items), busy_ratio (recent compute fraction)
and Redis started_at timestamp. Cancellation cannot interrupt a running CPU model:
it skips queued work or discards its eventual output. API polling is authenticated
and restricted to the key that accepted the request; rate limits apply to inference
submissions. Cached pending state expires at its request deadline logically and at
result TTL physically. Final cache writes refresh the result TTL.

INFER_REDIS_MAX_CONNECTIONS (default 64, minimum 2) bounds each process's blocking
Redis connection pool. Callers wait for a connection up to INFER_REDIS_TIMEOUT_S
rather than immediately receiving a pool-exhaustion error. Dedicated blocking
stream reads consume pool slots too. Handler deadline accounting includes the
token-bucket wait; queue work receives the remaining budget.

The default 5-second classifier request deadline is shorter than the default
30-second reclaim idle threshold. Successful synchronous recovery needs a tuned
deadline longer than reclaim wait, scheduling delay and inference. E4 uses
controlled shorter idle thresholds for its measured small fake batches.

## Phase 4 LLM and streaming

| Variable | Default | Purpose |
|---|---|---|
| INFER_LLM_BASE_URL | unset | Override this worker's trusted LLM upstream URL |
| INFER_GATEWAY_STREAM_BUFFER | 128 | Maximum buffered Redis chunk bundles per streaming waiter |
| INFER_WORKER_METRICS_PORT | 9001 | LLM worker Prometheus endpoint, internal |

LLM models require llm configuration: base_url, served_model, max_concurrent (32),
max_tokens_cap (512), coalesce_ms (20), max_buffered_chunks (128),
max_output_chars (262144), cancel_poll_ms (200), http_timeout_s (60).
Chat request bodies are limited by the existing byte cap and per-model aggregate
message-character limit. Invalid max_tokens above the model cap returns 422.
The chat catalog uses reclaim_idle_ms=90000, above its 60000 ms request deadline.

Fake server settings use INFER_FAKE_: TTFT_MS=40, TOKEN_MS=5, TOKENS=32,
SERVED_MODEL=Qwen/Qwen2.5-0.5B-Instruct, ERROR_STATUS=0 (off),
FAIL_AFTER unset, STALL_AFTER unset. Failure/stall controls are for tests only.
Fake usage counts synthetic tokens; it is not real tokenizer or GPU performance.
