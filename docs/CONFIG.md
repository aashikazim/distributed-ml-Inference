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


Compose-only variables: INFER_VLLM_UID (1000, non-root) selects the engine cache
owner, and INFER_VLLM_CACHE (/tmp/infer-vllm-cache) selects the host bind mount.
They are Compose interpolation controls, not pydantic application settings. The
engine runs with group 0 as supported by the tested official image. Keep the
cache writable by that non-root UID. The pinned engine image and flags are in
[GPU verification](GPU_VERIFICATION.md). fake-llm and gpu profiles are mutually
exclusive because they share port 8100 and the chat model queue.

The gateway returns X-Request-ID for chat requests, including streaming. The final
result is available through the authenticated result endpoint once cached. Result
JSON includes stream_started to distinguish reclaim before/after partial
publication, using the existing result key. Chat does not support async submission
or stream resumption. Streaming late errors are SSE error frames rather than a
new HTTP status. Bounded overflow uses INFER_CANCEL_TTL_S for its cancel marker.

The engine has localhost:8100/metrics; the chat worker exposes its LLM counters,
TTFT/TPOT histograms and active-sequence gauge on internal port 9001. Gateway
/metrics exposes client-side TTFT. Full platform instrumentation and Prometheus
scraping configuration arrive in Phase 5.

INFER_EXPORTER_INTERVAL_S (2): seconds between Redis platform-statistic polls.

INFER_WORKER_METRICS_PORT also serves classifier metrics now; zero chooses a random
port for isolated local drill subprocesses. Compose uses 9001 internally.

## Phase 6 controller settings

| Setting | Default | Meaning |
|---|---|---|
| INFER_SCALER_MODE | dryrun | Log only, or kubernetes for real scale-subresource writes |
| INFER_AUTOSCALE_INTERVAL_S | 5 | Control-loop polling period |
| INFER_AUTOSCALE_LOCK_TTL_S | 30 | Leadership lease seconds, minimum 15; renewed every TTL/3 |
| INFER_AUTOSCALER_METRICS_PORT | 9003 | Internal metrics listener; zero chooses an ephemeral port |
| INFER_KUBERNETES_NAMESPACE | infer-platform | Namespace of managed deployments |
| INFER_WORKER_DEPLOYMENTS | {} | JSON object mapping model name to Deployment name |

Optional model autoscale policy: min=1, max=8, target_backlog_per_replica=10,
target_busy_ratio=0.7, max_head_age_s=0.5, up_cooldown_s=5,
down_cooldown_s=30, stabilization_s=120, max_up_step=4. Model-specific catalog
values override these defaults. Missing autoscale excludes that model from the
controller. Kubernetes mode further restricts to the explicit deployment map.
The Kubernetes adapter uses in-cluster credentials, get/patch on deployments/scale
and request timeouts (connect 3 s, read 5 s). Blocking client calls run in threads.
Dry-run uses live accepting workers as current and never changes Compose replicas.

## Phase 7 packaging and overlays

Docker build args: INSTALL_ML=0 keeps the HTTP/fake image small; INSTALL_ML=1
with TORCH_DEVICE=cpu (default) uses the locked ml extra/CPU index, while
TORCH_DEVICE=cuda uses the mutually exclusive cuda extra/cu130 index. Both were
built on arm64 and ran classifier warmup/prediction; this is separate from GPU
Kubernetes verification. Runtime roles are named Dockerfile targets gateway,
worker, autoscaler, exporter and fake-openai. All run as UID 10001.

The kind model catalog intentionally differs from Compose: fake batches of four,
prefetch four, base 50 ms, deadline 30000 ms, reclaim idle 2000 ms, interval 100 ms;
controller interval 2 s, stabilization 20 s, min 1/max 4. These settings make
small simulated reliability/scaling drills observable; do not copy them to slow
real models. Kind maps only fake-classifier to a managed Deployment. CPU
sentiment is optional and not automatically scaled. GPU chat adapter/engine
scaling remains a separate deployment design, not silently coupled to this map.

KUBECONFIG (tool variable, not app setting) defaults to /tmp/infer-kind-kubeconfig
in kind-up.sh. Its dedicated context is kind-infer-platform; localhost:8001 maps
the gateway NodePort. API keys are inserted into platform-secrets from the existing
environment. ConfigMaps disable name hashing for controlled drills; after changing
models, restart affected processes so their startup-loaded catalog is refreshed.
Production Redis auth/TLS, notification routing and durable monitoring retention
are deployment choices, not enabled by the local development examples.

## Phase 8 measurement and test controls

These are tool/test variables rather than pydantic application settings:
INFER_LOAD_SCENARIO (steady; also ramp/spike/mixed) selects the bounded Locust
shape, and INFER_LOAD_API_KEY supplies its key (public demonstration fallback).
INFER_TEST_REDIS_URL optionally selects CI's real Redis service; otherwise tests
create Testcontainers Redis. INFER_GPU_BASE_URL explicitly enables the opt-in
real-server test. INFER_GPU_REPORT_PATH selects its artifact output, defaulting
to docs/results/p4-gpu-verification.json so historical Phase 4 instructions still
work; Phase 8 supplies its own output path. UV_CACHE_DIR, PATH and KUBECONFIG are
local tooling controls, not application configuration.

Measured local SLO targets are recording rules generated by tools.bench.propose_slo
from committed benchmark data, not hard-coded application settings. Review their
scope before changing/deploying them. Live response total_ms and
gateway_overhead_ms describe the current HTTP delivery through final construction;
cached results retain worker timings and leave these delivery-specific fields null.
No Redis key or ACK-order change is introduced by the extra timing metadata.

## Generic serving (Phase D)

Environment settings:

| Variable | Default | Meaning |
|---|---|---|
| INFER_CLAIM_CHECK_BYTES | 16384 | A serialized request payload larger than this is stored at `blob:{request_id}` (TTL = INFER_RESULT_TTL_S) and the stream entry carries only `payload_ref`. The blob is deleted atomically with XACK + XDEL. INFER_MAX_BODY_BYTES still caps the HTTP body, so raise both for image models. |
| INFER_WORKER_CACHE_S | 1.0 | How long a gateway reuses its list of live worker heartbeat keys before rescanning. Liveness is still checked atomically with EXISTS in the admission script; `0` rescans on every request, as before. |

New per-model fields in `config/models.yaml`:

| Field | Default | Meaning |
|---|---|---|
| `backend` | (required) | Adds `hf_pipeline` (Hugging Face `pipeline`) and `onnx` (ONNX Runtime) to `fake`, `torch`, `llm`. Backends map to runners in `worker/runners/registry.py`. |
| `inputs` | none | Named typed inputs: `{name: {type: text\|json\|tensor\|image, ...}}`. Text needs `max_chars`; json and image need `max_bytes`; tensor needs `dtype`, `max_elements` and optionally `shape` (`-1` = any size). Omitted, the model accepts the original single text field `input` limited by `max_input_chars`. `options` is a reserved name. |
| `pipeline.task` | (required for hf_pipeline) | One of `text-classification`, `token-classification`, `feature-extraction` (mean-pooled embedding), `image-classification`. Needs `hf_id`; weights must be safetensors; remote code is never run. |
| `pipeline.kwargs` | `{}` | Extra call-time arguments for the pipeline, e.g. `top_k: 3`. |
| `onnx.path` | (required for onnx) | Model file. Relative paths resolve under INFER_MODEL_CACHE. Never taken from a request. |
| `onnx.outputs` | all graph outputs | Output names to return. |
| `onnx.intra_op_threads` | 1 | ONNX Runtime CPU threads per replica (same cores ÷ replicas rule as torch). |
| `max_batch_cost` | none | Optional cost budget per batch, in addition to `max_batch_size`. The first item always fits, so one oversized item still runs alone. |
| `batch_cost_mode` | `padded` | `padded`: batch cost = items × largest item cost (what dynamic padding actually computes). `sum`: add item costs. |

Item cost comes from the runner's optional `cost()` method: estimated tokens for text
runners, total tensor elements for ONNX, 1 per image. Without one, the cost is
the payload size in bytes.
