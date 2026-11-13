# ADR-0011: Typed multi-input requests and claim-check payloads

## Status
Accepted in Phase D (generic serving).

## Context
The original contract was one text field: `{"input": "..."}`. That fits text
classifiers, but not image models, tabular models with numeric tensors, or models
with several named inputs (question + context, image + prompt). The whole request
also travelled *inside* the Redis stream entry. Every `XREADGROUP`, `XAUTOCLAIM`,
`XRANGE` and DLQ copy moved the full body, so a 1 MB image is copied many times.

Constraints: no pickle on the wire, JSON only. Existing clients and the stream
format must keep working during a rolling update. Gateway and workers must not
disagree about what a valid request is.

## Decision
**Typed inputs.** `POST /v1/infer/{model}` accepts either the legacy body or
`{"inputs": {name: value}, "options": {}}`. Each value is one of four types:
`text` (string), `json` (any JSON), `tensor` (dtype, shape, flat data list) or
`image` (base64 file bytes). Each model declares its inputs and their limits in
`models.yaml`. A model without a declaration accepts one text input called `input`,
which is exactly the old contract. `GET /v1/models` publishes each declaration.

One function, `common.inputs.normalize`, validates a body against a declaration.
The gateway calls it to reject bad requests with 422 before enqueueing, and the worker
calls it again before inference (defence in depth, and rolling-update safety).
Runners receive `{name: value, "options": {...}}`. Images arrive as raw bytes and
are decoded inside the executor thread, never on the event loop.

Legacy bodies are serialized with `exclude_none`, so their stream `payload` is
byte-identical to before. Old workers keep processing legacy traffic during an upgrade.

**Claim-check.** If the serialized payload exceeds `INFER_CLAIM_CHECK_BYTES`
(16 KiB default), the gateway first writes it to `blob:{request_id}` (TTL =
result TTL), then enqueues an entry with an empty `payload` and
`payload_ref=blob:{request_id}`. The worker fetches the blob on accept. The blob is
deleted in the same MULTI/EXEC as XACK + XDEL. An entry without `payload_ref`
omits the field entirely. A missing blob (expired, or Redis data loss) produces a
definite `PAYLOAD_MISSING` error instead of a hang.

## Alternatives considered
- **Multipart/form-data uploads:** the natural format for files, and no base64 33%
  overhead. But it needs a second parser and code path, and JSON clients and tests get
  more complex. A future `/v1/infer/{model}/upload` could map onto the same declaration.
- **Binary tensor encoding** (raw bytes + dtype, as in the KServe v2 binary extension):
  much smaller and faster than JSON number lists. Rejected for now because JSON is
  readable and debuggable for a learning project. Large tensors should use it later.
- **Object storage (S3/MinIO) for blobs:** the textbook claim-check. It moves bytes
  out of Redis memory entirely. Rejected because AGENTS.md keeps Redis as the only
  infrastructure dependency. The blob lives in Redis, so Redis memory still holds it once.
  The win is that stream operations stay small. A `BlobStore` interface would be the seam.
- **Always claim-check:** simpler, but adds a round trip to every small request.

## Consequences
- New key `blob:{request_id}` (String, TTL = result TTL). The RequestMessage has an
  optional `payload_ref` field. Delivery semantics (ACK after reply, reclaim, DLQ) are
  unchanged. A reclaimed entry re-reads the blob, which still exists until ACK.
- The DLQ stores the envelope, so a dead-lettered large request keeps only its
  reference, and the blob expires with its TTL. DLQ replay of large payloads would need
  to copy the blob first.
- `INFER_MAX_BODY_BYTES` still bounds HTTP bodies. Image models need it raised, and base64
  adds about 33% to the file size.
- Validation runs twice per request (gateway and worker). It is cheap compared with inference.
