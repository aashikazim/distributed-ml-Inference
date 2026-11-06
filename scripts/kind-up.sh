#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
: "${INFER_API_KEYS:?Set INFER_API_KEYS to a JSON array}"
export KUBECONFIG="${KUBECONFIG:-/tmp/infer-kind-kubeconfig}"
cluster_name=infer-platform
if ! kind get clusters | rg -qx "$cluster_name"; then
  kind create cluster --name "$cluster_name" --image kindest/node:v1.34.0 --config deploy/k8s/kind.yaml --kubeconfig "$KUBECONFIG" --wait 180s
fi
kind export kubeconfig --name "$cluster_name" --kubeconfig "$KUBECONFIG"
docker build --target gateway -f deploy/docker/Dockerfile -t infer-platform:kind .
kind load docker-image infer-platform:kind --name "$cluster_name"
kubectl --context kind-infer-platform create namespace infer-platform --dry-run=client -o yaml | kubectl --context kind-infer-platform apply -f -
kubectl --context kind-infer-platform -n infer-platform create secret generic platform-secrets --from-literal="api-keys=$INFER_API_KEYS" --dry-run=client -o yaml | kubectl --context kind-infer-platform apply -f -
kubectl --context kind-infer-platform apply -k deploy/k8s/overlays/kind
kubectl --context kind-infer-platform -n infer-platform rollout status statefulset/redis --timeout=180s
for deployment in gateway worker-fake fake-openai worker-chat exporter autoscaler prometheus grafana; do
  kubectl --context kind-infer-platform -n infer-platform rollout status "deployment/$deployment" --timeout=240s
done
