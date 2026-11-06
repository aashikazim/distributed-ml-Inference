# ADR-0010: one process per serving container, development Redis with persistence

## Status
Accepted in Phase 7. Local kind experiments are recorded separately from the
untested GPU Kubernetes overlay. Hosted GitHub Actions execution is pending.

## Context
Model warmup and graceful draining differ from ordinary stateless web startup.
A process can be alive before it can serve, and killing it is different from
asking it to stop. Redis durability also needs an explicit deployment boundary.

## Decision
Use multi-stage non-root images with role-specific build targets and a shared
locked application environment. CPU and CUDA ML extras are mutually exclusive;
Torch comes from the corresponding explicit wheel index. Keep vLLM as its own
pinned official server. One Uvicorn process per container preserves metric meaning.

Kustomize base contains gateways, fake classifier/chat workers, fake upstream,
exporter, autoscaler, Prometheus/Grafana, and a development Redis StatefulSet with
AOF on a PVC. CPU sentiment is an optional component with its own model PVC and
ML image. A GPU overlay adds vLLM resource requests, scheduling selectors/tolerations,
model storage and adapter routing; it is explicitly untested on a GPU cluster.

Worker readiness follows load/warmup and becomes false on draining. Liveness is
process-local, independent of Redis. Startup probes allow warmup before liveness
restarts are considered. Gateways use Redis/inbox readiness and process liveness.
Rollouts use maxUnavailable=0 and maxSurge=1. Classifier grace 45 seconds exceeds
the kind request budget (30 seconds); chat grace 80 seconds exceeds 60 seconds.
CPU threads default to one per replica within explicit CPU/memory resources.

Give only autoscaler a service-account token. Its namespace Role allows get/patch
on named worker-fake deployments/scale, not arbitrary workload changes. API keys
come from a Secret created from environment, never a committed secret manifest.
Use a dedicated kind cluster/context and isolated temporary kubeconfig. Gateway
NodePort maps only to localhost:8001; Redis/metrics remain cluster-internal.

## Alternatives considered
- Helm: useful packaging ecosystem, but Kustomize satisfies this project's fixed
  stack and keeps overlays visible as ordinary Kubernetes manifests.
- Embed Redis in a worker: simpler startup, but destroys shared delivery state.
- Production Redis as this StatefulSet: convenient dev persistence, but one node
  and AOF do not provide failover, replication guarantees or zero-loss durability.
- Multiple Uvicorn workers per container: uses local CPUs but complicates metric
  aggregation and duplicates lifecycle state; scale containers instead.

## Consequences
The kind overlay uses shorter control/reclaim timings for known small simulated
batches; they are not generic production GPU defaults. PDBs help voluntary eviction,
not force deletion, SIGKILL or node loss. Readiness does not prove a future request
cannot fail. Engine first-use kernel work can still occur after its health probe.

Production should choose managed Redis or an appropriately operated HA topology,
authentication/TLS, backups and recovery tests, durable monitoring storage,
notification routing and ingress policies. This repository adds no extra queue
system or service mesh. The CI workflow checks code/tests and builds role images;
GHCR publication is conditional on a push to main. No hosted CI success is claimed
without an actual run. See [deployment concepts](../concepts/deploying-ml-workloads.md).
