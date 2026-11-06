#!/usr/bin/env bash
set -euo pipefail
: "${INFER_SMOKE_API_KEY:?Set INFER_SMOKE_API_KEY to one configured API key}"
curl --fail --silent --show-error http://localhost:8000/healthz
curl --fail --silent --show-error http://localhost:8000/readyz
curl --fail --silent --show-error -H "X-API-Key: $INFER_SMOKE_API_KEY" http://localhost:8000/v1/models
curl --fail --silent --show-error -H "X-API-Key: $INFER_SMOKE_API_KEY" \
  -H 'Content-Type: application/json' -d '{"input":"Learning Redis Streams"}' \
  http://localhost:8000/v1/infer/fake-classifier
