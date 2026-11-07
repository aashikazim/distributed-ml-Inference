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

## CPU reproduction and scope limits

Use the fake-llm profile and [lesson 04](learning/04-llm-serving.md) for CPU-only
transport and failure testing. Its tokens and inference timings are **simulated**.
The application does not need CUDA packages when calling the separate GPU server.

**Pending:** other GPUs/models, long-context saturation, production-sized load,
CUDA graph comparisons and other GPU clusters. The GPU Kustomize overlay in
deploy/k8s/overlays/gpu was subsequently verified on real local GB10 kind.
Prometheus scraping configuration, dashboards and alerts were added in Phase 5.
Phase 4 abort evidence queried the real engine directly. Phase 7 additionally
built the CUDA worker image and ran real DistilBERT load/warmup/prediction on
GB10 with torch 2.14.1+cu130; see results/p7-cuda-runner.json. That Docker
verification alone does not establish Kubernetes GPU scheduling; the separate
wrap-up below does.

Primary references: [vLLM Docker deployment](https://docs.vllm.ai/en/stable/deployment/docker/),
[vLLM GPU installation](https://docs.vllm.ai/en/stable/getting_started/installation/gpu/),
and [online serving](https://docs.vllm.ai/en/stable/serving/online_serving/).


## Phase 8 final regression

The final source passed real GB10 completion and disconnect abort again, and the
rebuilt Compose stack returned real chat with gateway timing metadata. Prometheus
actually scraped the real vLLM endpoint with a healthy target and positive sample
count: [Compose/engine evidence](results/p8-compose-real-vllm.json). The warmed
opt-in test recorded 21.24 ms worker TTFT and 34.20 ms disconnect-to-zero/CANCELLED
with its 20 ms test polling. Its cold restart result is separately preserved.
These are individual observations, not default-poll or percentile guarantees.

While the GPU server is running, the executed command below preserves historical
Phase 4 output by selecting a different test artifact:

```bash
INFER_GPU_BASE_URL=http://127.0.0.1:8100 INFER_GPU_REPORT_PATH=docs/results/p8-gpu-verification.json uv run --extra ml pytest -m gpu -vv
```

See [final verification](results/p8-gpu-verification.json) and
[cold run](results/p8-gpu-cold-run.json). GPU Kubernetes was subsequently verified in the wrap-up below.


## Wrap-up: real GPU Kubernetes on localhost

The full GPU overlay ran on kind 0.30.0 / Kubernetes 1.34.0 with NVIDIA device
plugin v0.18.0, Container Toolkit 1.18.0 and driver 580.95.05 on the same GB10.
Kubernetes allocated one actual GPU to the non-root vLLM Pod. The final image ID
matches the pinned official registry digest; the model revision is recorded.

Gateway → Redis → LLM adapter → GPU Pod completion passed. A longer request used
1,031 prompt tokens and generated 128 tokens. Disconnecting another active stream
changed the engine's running-sequence gauge from 1 to 0 and persisted CANCELLED
in Redis in **234.96 ms** with the catalog's 200 ms cancellation polling. The
short completion's first-use TTFT was **17.28 s**; health readiness does not imply
all request-shape kernels are warm. These are individual observations.

[Final GPU kind evidence](results/final-kind-gpu.json) and
[initial successful attempt](results/final-kind-gpu-initial-success.json).
The initial attempt had a noncanonical locally imported image reference; the
final run repeated the check after removing that import and pulling the pinned
registry image. The actual fake server was removed before the final run.

The real run exposed and fixed three deployment issues:

- The GPU overlay must explicitly set namespace infer-platform; its added
  resources otherwise land in default even though base resources are namespaced.
- Disable service-link injection for the engine. A Service named vllm injects
  VLLM_PORT, conflicting with vLLM's internal port setting. The
  [official environment-variable guide](https://docs.vllm.ai/en/latest/configuration/env_vars/)
  documents this collision. Initial startup failure is
  [retained](results/final-gpu-initial-failure.json).
- A single-GPU engine uses Recreate. A surge replacement needs a second GPU and
  can remain Pending indefinitely. Engine updates have downtime; classifier
  rolling-update results do not establish interruption-free engine updates.

A local-only ARM64 helper copies NVIDIA driver files into the dedicated kind
node and installs a device plugin with narrow driver/device/CDI mounts. It does
not change the host Docker daemon. It is specific to this tested GB10 setup,
not a production driver installer. Other clusters need their own supported
NVIDIA driver/runtime/plugin installation. Sources: [NVIDIA device plugin v0.18.0](https://github.com/NVIDIA/k8s-device-plugin/tree/v0.18.0)
and [NVIDIA CDI support](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/cdi-support.html).

Executed commands (kind and kubectl were installed in /tmp/infer-tools):

```bash
PATH=/tmp/infer-tools:$PATH KUBECONFIG=/tmp/infer-kind-kubeconfig UV_CACHE_DIR=/tmp/infer-uv-cache uv run python -m tools.prepare_kind_gpu
PATH=/tmp/infer-tools:$PATH KUBECONFIG=/tmp/infer-kind-kubeconfig kubectl apply -k deploy/k8s/overlays/gpu
PATH=/tmp/infer-tools:$PATH KUBECONFIG=/tmp/infer-kind-kubeconfig kubectl -n infer-platform scale deployment/vllm --replicas=1
PATH=/tmp/infer-tools:$PATH KUBECONFIG=/tmp/infer-kind-kubeconfig kubectl -n infer-platform port-forward svc/vllm 18100:8000
```

The forward stays running. In another terminal, the executed verifier is:

```bash
PATH=/tmp/infer-tools:$PATH KUBECONFIG=/tmp/infer-kind-kubeconfig UV_CACHE_DIR=/tmp/infer-uv-cache uv run python -m tools.verify_kind_gpu
```

Use the existing kind API-key setup from lesson 07. A fresh model PVC needs
network access to download weights; this run preloaded its PVC from the already
verified local model cache. Do not run fake and real chat adapters on the same
queue while trying to verify which engine served a request.

The owner scoped final acceptance to localhost. Cloud deployment is outside this
project sign-off, rather than a required unfinished task. Longer contexts,
other models, CUDA graph mode and production traffic require separate measurement.

The final opt-in regression also passed against the Kubernetes-hosted engine:

```bash
INFER_GPU_BASE_URL=http://127.0.0.1:18100 INFER_GPU_REPORT_PATH=docs/results/final-gpu-regression.json UV_CACHE_DIR=/tmp/infer-uv-cache uv run --extra ml pytest -m gpu -vv
```

Its [separate record](results/final-gpu-regression.json) measured 21.35 ms warm
TTFT and 1,017.68 ms from disconnect to zero/CANCELLED with 20 ms test polling.
That differs from the 234.96 ms full-kind observation and reinforces that neither
one measurement nor the polling interval is an abort-latency guarantee.
