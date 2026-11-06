# Deploying model workloads without confusing alive with ready

A container packages code and dependencies; a Pod is Kubernetes' scheduling and
lifecycle unit. A Deployment reconciles interchangeable Pods, while a StatefulSet
provides stable identity/storage association for Redis here. Kustomize combines
a base with focused overlays rather than copying an entire environment by hand.

## Startup, readiness and liveness

Loading weights and warming a model can take much longer than starting Python.
A **startup probe** gives that initialization a budget. A **readiness probe**
controls whether a Pod is considered available for traffic. A **liveness probe**
checks whether the process should be restarted. Liveness should not restart a
healthy process merely because Redis is briefly unavailable.

Classifier workers finish load and warmup before setting their ready event and
writing an accepting heartbeat. Chat workers verify their upstream model first.
Worker HTTP /readyz checks ready and draining flags; /healthz is process-local.
Gateway /readyz checks Redis and inbox health. K8s startup ordering does not mimic
Compose depends_on: services can restart while Redis or the upstream initializes,
then recover through reconciliation. Initial restarts were observed on the first
kind startup; stable readiness was checked before experiments.

A probe is evidence about now, not a promise about the next request. The real
vLLM cold request in Phase 4 illustrates that health can succeed before first-use
kernel initialization has completed. For latency-sensitive deployment, include
representative warmup and measure readiness-to-first-request behavior.

## Shutdown, rollout and requests in flight

SIGTERM asks a worker to stop reading and drain accepted work. The TTL heartbeat
advertises accepting=false, then is deleted at exit. Its termination grace must
exceed the longest permitted work plus response/cleanup margin. SIGKILL runs no
cleanup; Redis pending ownership enables reclaim if deadlines allow.

maxUnavailable=0 keeps old capacity until a replacement is ready; maxSurge=1
allows one additional Pod during a rolling update. Spare node resources are
therefore needed. A PodDisruptionBudget limits voluntary eviction, but does not
prevent force deletion or enforce delivery semantics. ACK-after-result and
reclaim remain necessary regardless of Kubernetes rollout settings.

E12's first force-delete returned before the old process stopped; the selected
request completed there. That is not reclaim evidence. The final drill sends an
explicit process SIGKILL, deletes the Pod, and checks the selected pending ID
completes on another worker. See [experiments](../EXPERIMENTS.md) for both records.

## Requests, limits and CPU threads

Resource requests help placement; limits constrain consumption. CPU limits can
throttle a process; memory limits can terminate it when exceeded. An efficient
batch can use several CPU threads, but many replicas each claiming all host
cores fight over the same machine. We configure one intra-op thread by default.
Benchmark other settings in the intended placement before changing them.

Weight caches reduce download/startup cost but consume storage and need writable
non-root ownership. Images omit model weights and secrets. Optional ML images
are larger than the fake/HTTP image; the CPU wheel index avoids CUDA dependencies
in CPU deployments. CPU and CUDA extras have separate locked sources and cannot
be installed together. Shared runtime build targets provide distinct entrypoints
without maintaining divergent dependency sets for every role.

## GPU scheduling and vLLM

A Kubernetes NVIDIA device plugin advertises GPU resources. The untested GPU
overlay requests nvidia.com/gpu, uses a node selector for GPU-capable labeled
nodes and a matching toleration for GPU-tainted nodes. Labels/taints must match
the actual cluster setup; the overlay is not an installer for drivers, runtime
support, GPU feature discovery or the device plugin. Check those prerequisites
with the cluster operator. The NVIDIA resource is an integer GPU allocation,
not a guarantee of this project's tested GB10 memory capacity.

The vLLM Deployment owns one engine and its GPU allocation; the adapter cap stays
aligned with its sequence cap. Do not scale adapters independently as if each
created another GPU engine. The current Kubernetes autoscaler map manages only
the fake classifier. Real vLLM and CUDA classifier runner were verified in Docker;
GPU Kubernetes scheduling remains explicitly untested.

## Redis and monitoring availability

Redis AOF on a single PVC survives a Pod replacement if the storage remains.
It does not provide HA, synchronous replication or guaranteed zero lost writes.
A production deployment needs an operated Redis HA/service decision, backups,
restore tests, authentication and TLS. Enable Redis requirepass or ACLs and use
a rediss URL with trusted certificates where appropriate; secrets remain env or
Secret values, not committed files. No production Redis failover drill is claimed.

Prometheus/Grafana use ephemeral development storage in kind. Their provisioning
is reproducible, but their history is not durable. Production also needs durable
retention, access controls and alert notification routing.

Primary references: [kind quick start](https://kind.sigs.k8s.io/docs/user/quick-start/),
[Kubernetes documentation](https://kubernetes.io/docs/). See
[lesson 07](../learning/07-kubernetes-deployment.md) and [ADR-0010](../adr/0010-deployment.md).
