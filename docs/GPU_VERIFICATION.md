# GPU verification and reproduction

## What was actually verified

On 2026-11-06, real Qwen/Qwen2.5-0.5B-Instruct inference and disconnect abort were
verified on the local **NVIDIA GB10**, not inferred from the owner's hardware
name. The full path was TCP gateway → real Redis 7 → LLM worker → official vLLM
server on GPU. A separate Compose GPU smoke exercised the deployed processes.

| Item | Observed configuration |
|---|---|
| Host | Linux aarch64; owner reports Ubuntu 24 LTS; 20 logical CPU cores |
| GPU / driver | NVIDIA GB10, compute capability 12.1 / 580.95.05 |
| Device memory | torch reports 128526049280 bytes; nvidia-smi reports memory N/A |
| Official engine | vLLM 0.33.0, torch 2.14.0+cu130, CUDA 13.0 |
| Image | vllm/vllm-openai@sha256:d825baf9ce4b1b6bfc5ecc7b79880c7e6748c65a72d697ce3d47607aafa84c6e |
| Model revision observed | 7ae557604adf67be50417f59c2c2f167def9a775 |
| Engine | Context 2048; max sequences 32; memory utilization 0.25; eager execution; default prefix caching enabled |
| Process | Non-root UID 1000, group 0; writable host cache mounted at /models |

See [environment](results/p4-gpu-environment.json),
[first cold request](results/p4-gpu-first-request.json), and
[final verification](results/p4-gpu-verification.json). The opt-in test observes
vLLM's active-sequence metric above zero before closing the client, then waits
for both zero active sequences and a cached CANCELLED worker outcome. Its cancel
poll is 20 ms to keep the test short; the catalog default is 200 ms. A single
measured abort is evidence for this version/model, not a universal latency promise.

The first completion spent 17.29 seconds before content and 17.35 seconds total,
consistent with first-use initialization. It is preserved separately from the
warmed E6 experiments. /health succeeding does not establish warmed latency.

## Exact local steps exercised

Run from the repository root. The fake-llm and gpu profiles are alternatives:
both use localhost:8100 and the chat queue. Stop the previous profile first.
Docker must already have NVIDIA Container Toolkit support; installation is a host
administration step, documented by [NVIDIA](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html).

```bash
nvidia-smi
docker run --rm --gpus all python:3.12-slim-bookworm nvidia-smi
export UV_CACHE_DIR=/tmp/infer-uv-cache
export INFER_API_KEYS='["phase1-verification-key"]'
mkdir -p /tmp/infer-vllm-cache
docker compose -f deploy/docker/docker-compose.yml --profile gpu up --build -d --wait
curl --fail --silent --show-error -H 'X-API-Key: phase1-verification-key' http://localhost:8000/v1/models
curl --fail --silent --show-error -H 'X-API-Key: phase1-verification-key' -H 'Content-Type: application/json' -d '{"model":"chat","messages":[{"role":"user","content":"Say hello."}],"max_tokens":32,"stream":false}' http://localhost:8000/v1/chat/completions
curl --fail --silent --show-error -N -H 'X-API-Key: phase1-verification-key' -H 'Content-Type: application/json' -d '{"model":"chat","messages":[{"role":"user","content":"Explain queues."}],"max_tokens":32,"stream":true}' http://localhost:8000/v1/chat/completions
INFER_GPU_BASE_URL=http://127.0.0.1:8100 uv run --extra ml pytest -m gpu -vv
uv run --extra ml python -m tools.llm_experiments E6a --backend vllm --output docs/results/e6a-vllm.json
uv run --extra ml python -m tools.llm_experiments E6b --backend vllm --output docs/results/e6b-vllm.json
docker compose -f deploy/docker/docker-compose.yml --profile gpu down
```

Allow model download, engine startup and worker registration. If /v1/models shows
chat live_workers=0, inspect the worker and engine before retrying requests.
The opt-in pytest starts its own gateway, worker and disposable Redis against the
running GPU server; it does not test Compose auth through that gateway. The curl
commands above cover the Compose gateway separately. The experiment runner owns
its own Redis and TCP gateway too, while reusing this vLLM server. It overwrites
its named result files, so copy historical measurements before rerunning if needed.

The tested cache is owned by UID 1000. On another host set INFER_VLLM_UID to the
cache owner's non-root UID, or select a writable directory with INFER_VLLM_CACHE.
Do not solve cache errors by running the service as root. The exact image is
pinned; the observed model revision is recorded, but the model's upstream main
reference can change on a new uncached download. Pin the recorded revision when
reproducing historical engine output rather than assuming main is immutable.

## Why these engine flags

- `--max-model-len 2048`: bounds prompt plus generation context and cache needs.
  Our character limit is not tokenizer-aware; over-context requests are still
  rejected by the upstream engine and mapped to a client error.
- `--gpu-memory-utilization 0.25`: conservative fraction for this large shared-memory
  device; not a portable recommendation for smaller GPUs.
- `--max-num-seqs 32`: bounds engine sequence scheduling, aligned with adapter
  max_concurrent=32. E6 swept 1–16, not the full 32-sequence configuration.
- `--enforce-eager`: the tested execution mode, avoiding CUDA graph capture here.
  These measurements do not establish graph-mode performance.

## CPU reproduction and pending checks

Use the fake-llm profile and [lesson 04](learning/04-llm-serving.md) for CPU-only
transport and failure testing. Its tokens and inference timings are **simulated**.
The application does not need CUDA packages when calling the separate GPU server.

**Pending:** other GPUs/models, long-context saturation, production-sized load,
CUDA graph comparisons, GPU Kubernetes scheduling and a GPU cluster. The GPU
Kustomize overlay is now provided in deploy/k8s/overlays/gpu, rendered but
**untested on a GPU Kubernetes cluster**.
Prometheus scraping configuration, dashboards and alerts were added in Phase 5.
Phase 4 abort evidence queried the real engine directly. Phase 7 additionally
built the CUDA worker image and ran real DistilBERT load/warmup/prediction on
GB10 with torch 2.14.1+cu130; see results/p7-cuda-runner.json. That Docker
verification does not establish Kubernetes GPU scheduling.

Primary references: [vLLM Docker deployment](https://docs.vllm.ai/en/stable/deployment/docker/),
[vLLM GPU installation](https://docs.vllm.ai/en/stable/getting_started/installation/gpu/),
and [online serving](https://docs.vllm.ai/en/stable/serving/online_serving/).
