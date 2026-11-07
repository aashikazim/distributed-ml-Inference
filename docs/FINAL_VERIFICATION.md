# Project wrap-up verification

This page separates an executed check from a guarantee. A successful request
proves a particular path worked with particular software and hardware. It does
not establish every failure mode or every future workload.

## Checks added after Phase 8

The wrap-up uses the same Ubuntu/aarch64 GB10 host as the earlier experiments,
Docker 28.3.3, kind 0.30.0 and Kubernetes 1.34.0. Dates and raw results are kept
in the linked JSON files rather than replacing the earlier phase measurements.

### Hosted CI and image publication

The first hosted run failed before tests: GitHub Actions split the Redis service
health command incorrectly when it used single quotes. The workflow now uses
`--health-cmd "redis-cli ping"`. The next run passed checks and all six image
builds. This illustrates why a workflow file and a locally successful Docker
build are insufficient evidence for hosted CI.

[Verification-branch run](https://github.com/aashikazim/distributed-ml-Inference/actions/runs/42296894313)
and [record](results/final-hosted-ci.json).

The [main-branch run](https://github.com/aashikazim/distributed-ml-Inference/actions/runs/42297779510)
also passed and published all six images to GHCR. Its [record](results/final-published-ci.json)
contains the exact commit, tags and registry manifest digests. Hosted tests passed
60 checks and skipped two real-model tests because weights were not cached; the
local run passed all 62 with cached CPU weights. The GPU marker remains opt-in.
Hosted builds target linux/amd64; local container and GPU verification used arm64.

### Backup and restore

A disposable Redis 7.4 source received two requests, one delivered to a worker
and one undelivered, plus an inbox entry and a cached result with a TTL. `SAVE`
created an RDB snapshot. The source was removed, and the snapshot was copied to
an independent container. The restored group retained its pending ownership and
last-delivered cursor. `XREADGROUP ... >` returned the undelivered entry;
`XAUTOCLAIM` recovered the old worker's pending entry. The cached result retained
its TTL and the inbox entry survived.

Executed from the repository root:

```bash
UV_CACHE_DIR=/tmp/infer-uv-cache uv run python -m tools.verify_backup
```

[Measured restore result](results/final-backup.json). This is a local snapshot
restore, not Redis HA. A snapshot recovers the state captured at its creation;
writes after that point require a newer backup or another durability strategy.
An operator must choose an acceptable recovery point and test off-host storage
and restore procedures for their actual deployment.

### Bounded soak

The wrap-up runs concurrent open-loop arrivals against the real kind HTTP path:
20 requests/s to the **simulated** classifier and 2 requests/s to the **real CPU**
DistilBERT classifier. The shared `tools.bench.e2e.trial` implementation measures
client duration and independently samples in-flight requests. GPU image import
and preparation overlap this test, so it is not an isolated capacity benchmark.

The reusable helper was also exercised with a short validation run:

```bash
UV_CACHE_DIR=/tmp/infer-uv-cache uv run python -m tools.verify_soak --seconds 2 --output /tmp/infer-soak-helper-check.json
```

The full 900-second run completed **18,000/18,000 simulated classifier** and
**1,800/1,800 real CPU sentiment** requests successfully. Client p95 was
**82.10 ms** and **69.36 ms**, respectively. The autoscaler remained enabled.
[Raw soak results](results/final-soak.json) retain the rates, measured duration,
percentiles and occupancy checks. The helper defaults to the same 900-second
duration; its short check validates the reusable entry point. A bounded soak can expose errors during
that observation window; it cannot prove an absence of memory leaks, establish a
30-day SLO, or predict behavior for larger models or longer prompts.

### Real GPU Kubernetes

The full GPU Kustomize overlay ran on the real local GB10 through the Kubernetes
gateway, Redis, adapter and engine Pod. The final image ID equals the pinned
registry digest. A longer prompt used 1,031 input tokens and generated 128 tokens.
Closing an active stream changed the engine metric from one running sequence to
zero and persisted CANCELLED within 234.96 ms. The first-use short completion
had 17.28 s TTFT, so readiness must not be confused with warmed request latency.
See [GPU result](results/final-kind-gpu.json) and [exact steps](GPU_VERIFICATION.md).

Real execution caught the overlay's missing namespace and a VLLM_PORT service
environment collision. Both are fixed and guarded by rendered-manifest checks.
The single-GPU engine uses Recreate to avoid waiting for an unavailable second
GPU; it has update downtime. Initial failed/successful attempts are retained.
This distinguishes actual allocation/inference from merely rendering YAML.

## Acceptance boundary: localhost

The owner confirmed that cloud deployment is not required. The local project
acceptance is complete once the checks in VERIFICATION.md pass. Cloud/WAN
placement, production ingress certificates, external notification routing, Redis
HA and monitoring retention are outside this localhost scope. No external
infrastructure is required for sign-off or claimed verified.

Resumable SSE, exactly-once computation, automatic DLQ replay and scale-to-zero
remain explicit scope exclusions. Implementing them would change the design.

The study plan and answer keys are delivered. Human understanding still needs
the owner's teach-back; automated checks cannot substitute for it. Start with:
why is `XACK` after result publication, why does LLM serving bypass our batcher,
and why can desired replicas exceed ready workers?

## Final local state

After GPU verification the CPU/fake overlay was restored and the temporary GPU
Deployment/Service removed; its model PVC is retained. Prometheus was restarted
to clear the removed GPU service's stale DNS-discovery target. The final record
checks gateway health/readiness, real CPU and simulated classifier inference,
simulated SSE completion, four Grafana dashboards, ready Pods and live scrapes.
[Final state](results/final-local-state.json). Local worker startup retries during
upstream replacement are recorded rather than described as zero restarts.
