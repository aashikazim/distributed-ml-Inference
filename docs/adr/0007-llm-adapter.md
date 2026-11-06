# ADR-0007: vLLM owns generation; our adapter owns the platform boundary

## Status
Accepted in Phase 4. Real completion and abort were verified on NVIDIA GB10 with vLLM 0.33.0.
Verification status and image revision are recorded in
[GPU_VERIFICATION.md](../GPU_VERIFICATION.md); fake-backend results never establish
GPU behavior.

## Context
A classifier processes a fixed tensor batch once. Autoregressive generation has
a prompt prefill and repeated decode steps; sequences end at different times.
Scheduling, KV-cache placement and GPU kernels belong inside a generation engine.

## Decision
Run the official vllm/vllm-openai server as a separate service. LLMAdapter calls
its OpenAI-compatible streaming HTTP API using httpx. It is not a ModelRunner
and never uses DynamicBatcher. An asyncio.Semaphore bounds active sequences,
and the stream consumer reads only free slots. Excess requests remain in Redis.
Align max_concurrent with vLLM --max-num-seqs.

Always stream from upstream, even when the client requests one final response;
this lets the adapter observe first content and completion usage. Validate the
subset of OpenAI chunk fields we support, require a proper finish and [DONE],
bound event/output sizes, and close the HTTP context on all exits.

Coalesce by bundling original chunks into bounded Redis messages. The gateway
expands each bundle back into the original SSE events in order. This reduces
XADD traffic without merging text fragments or changing finish semantics. A
timer flushes buffered chunks even when upstream pauses. The default is 20 ms.

Use a separate cancellation monitor, independent of token arrivals. It checks
cancel flags at the configured interval and cancels the HTTP operation, closing
the upstream stream. Worker deadlines also close it. The fake TCP server confirms
connection closure; real vLLM must separately confirm generation abort on GPU.

## Alternatives considered
- Implement LLM scheduling in DynamicBatcher: reuses code, but fixed classifier
  batches do not implement generation scheduling or KV-cache management.
- Embed vLLM in the worker: avoids one HTTP hop, but couples CUDA/process lifecycle,
  package versions and platform code to generation internals.
- Route gateway directly to vLLM: fewer Redis hops, but bypasses common admission,
  ownership, registry, cancellation and result handling.
- XADD every upstream chunk: simple and low intentional delay, but increases Redis
  operations and response-reader work; E6b measures the coalescing trade-off.
- Merge strings into new synthetic token chunks: fewer SSE events too, but changes
  chunk boundaries and makes preservation of role/finish/usage semantics harder.

## Consequences
A slow client cannot block the gateway's single global inbox reader. Its bounded
stream queue overflows into a SLOW_CONSUMER error and cancellation, while other
requests keep receiving replies. HTTP errors before the first response frame map
to error status codes. After SSE headers have started, errors are SSE error
objects followed by [DONE]; the HTTP status cannot be changed retroactively.

Record a stream_started marker in the existing typed result JSON before its first
chunk publication. A reclaimed partial stream is terminated as interrupted/expired,
not silently restarted into the same HTTP response. Before recording the marker, retry after a crash remains possible. Once the
marker exists, interruption is conservative even if no chunk reached the client. Cached finals can be returned,
but partial stream replay/resumption is not supported. A final text-hash check
compares emitted content with the complete result, detecting missing/corrupt
chunk content without retaining another full output in the gateway. It cannot
retract already emitted text. Request wire fields and
Redis key names remain unchanged.

TTFT measures dequeue to first nonempty upstream content. Gateway TTFT includes
queueing and coalescing. TPOT is an observed estimate using the content time span
and reported completion-token count; HTTP chunks may contain multiple tokens.
vLLM's own metrics remain authoritative for engine scheduling. Completion-token
counters require final upstream usage; failed partial streams may have no usage.
Never describe simulated token timings as real model performance.

The fake server has synthetic TTFT/token delays and bounded synthetic output,
health/models/metrics and diagnostic counts. It stores no prompts in diagnostics.
No tools, vision, function calling or other OpenAI API features are implied by
the supported subset. GPU Kubernetes scheduling remains Phase 7 and unverified
until actually exercised on a GPU cluster.
