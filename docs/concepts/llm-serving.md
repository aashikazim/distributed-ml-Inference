# LLM serving: where the platform stops and the engine starts

A classifier consumes one padded batch and produces one result per input.
An autoregressive language model first reads the prompt, then repeatedly predicts
another token using the prompt and all previously generated tokens. A **token**
is a tokenizer unit, which can be part of a word, a word, or punctuation.

## Prefill, decode and the KV cache

**Prefill** processes the prompt and constructs attention state. **Decode** uses
that state to produce successive tokens. Recomputing the prompt on every decode
step would waste work. A **KV cache** retains attention keys and values for prior
tokens. Longer prompts and outputs use more cache, reducing concurrent capacity.

**PagedAttention** manages KV-cache storage in blocks, with mappings from a
sequence's logical blocks to physical memory. This reduces allocation waste and
supports sharing; it does not make GPU memory unlimited. vLLM owns that memory
management. Our worker never allocates KV blocks or schedules CUDA kernels.

## Three batching policies

| Policy | Scheduling unit | Main limitation or benefit |
|---|---|---|
| Static | Fixed batch for an entire invocation | Short generations wait for long ones; capacity can sit unused |
| Dynamic classifier batching | Collect independent inputs before one forward execution | Trades a small collection delay for amortized compute |
| Continuous generation batching | Reschedule active sequences across decode iterations | Finished sequences leave and new ones enter without waiting for the longest generation |

Continuous batching belongs inside the generation engine. Putting chat requests
through our DynamicBatcher would add another collection delay without implementing
iteration scheduling. An adapter is closer to a restaurant host: admit only as
many guests as there are seats, then let the kitchen schedule its work.

```mermaid
flowchart LR
    Q[Redis waiting requests] -->|Free semaphore slot| A[LLM adapter]
    A -->|Independent HTTP streams| V[vLLM]
    V --> P[Prompt prefill]
    P --> D[Repeated decode iterations]
    D -->|Finished sequences release capacity| V
    V -->|Chunks and final usage| A
```

## What our platform owns

The gateway owns auth, validation, rate limits, retained-depth admission and SSE.
The LLM worker owns bounded stream consumption, deadlines, cancellation monitoring,
HTTP errors, Redis publication and final-result caching. Its semaphore cap should
match vLLM's configured maximum sequences. The worker reads only free capacity:
excess requests remain visible in Redis instead of accumulating in Python tasks.
The engine still makes its own scheduling decisions within that admitted set.

For each upstream content stream, a bounded producer queue feeds a coalescer.
The **coalescing window** bundles original chunks into one Redis XADD. The gateway
expands the bundle into the original ordered SSE events. A timer flushes even if
upstream pauses. Zero disables intentional waiting; 20 ms reduces Redis writes
but can delay the first client-visible text. E6b measures both effects.

## Streaming changes failure semantics

SSE is UTF-8 text with `data: JSON` events separated by blank lines and a final
`data: [DONE]`. Before headers, an upstream error can become an HTTP error status.
After headers, the connection already has status 200: a failure becomes an SSE
error object followed by [DONE]. Consumers must inspect events, not just HTTP 200.

A separate monitor checks Redis cancellation even when the upstream sends no
chunks. Cancelling generation closes httpx's stream context. Both the TCP fake
server and the actual GB10 vLLM test confirmed upstream work stops after a client
closes its stream; this remains a version-specific measured behavior.

A worker records `stream_started` in the existing result JSON before publishing
its first chunk. Reclaim of an unfinished partial stream returns STREAM_INTERRUPTED,
rather than appending a second generation to already-visible text. Full stream
resumption is unsupported. Before any chunk is published, normal crash redelivery
can retry. Redis inbox trimming can also lose chunks: the gateway compares the
hash of emitted text with the final completion and reports interruption if they
differ. It cannot retract text already shown. The full cached completion may still
be available through authenticated result polling using X-Request-ID.

Slow clients have bounded local queues. Overflow cancels that request and reports
SLOW_CONSUMER without blocking every other request's inbox reader. Redis remains
at-least-once; simultaneous deliveries can still duplicate compute around races.

## Measuring the two experiences

**TTFT** is time to first content token. Worker TTFT starts at dequeue and excludes
Redis queueing; gateway/client TTFT also includes queueing, transport and coalescing.
Role-only chunks do not count as content. **TPOT** is time per output token during
decode. Our adapter estimates it from first/last content times and final usage;
a network chunk can contain multiple tokens, so this is not a kernel timing.
An output shorter than two observed content chunks has no meaningful estimate.
**Tokens/s** in E6 is total reported completion tokens divided by burst elapsed
time, including waiting. Aborted streams without usage do not invent token counts.

A healthy engine may still compile or initialize a kernel on its first request.
Our first real completion took 17.35 s, while later warmed measurements were much
shorter. Do not infer latency readiness from /health alone. E6 uses the warmed
server and records prefix caching and eager execution; it is not a production SLO.

## Sources and next reading

The tested engine is vLLM 0.33.0; environment and pending deployment checks are in
[GPU verification](../GPU_VERIFICATION.md). Read [lesson 04](../learning/04-llm-serving.md),
[ADR-0007](../adr/0007-llm-adapter.md), and [E6a/E6b](../EXPERIMENTS.md).

Primary references: [vLLM online serving](https://docs.vllm.ai/en/stable/serving/online_serving/),
Kwon et al., *Efficient Memory Management for Large Language Model Serving with
PagedAttention*, SOSP 2023 ([paper](https://arxiv.org/abs/2309.06180));
Yu et al., *Orca: A Distributed Serving System for Transformer-Based Generative
Models*, OSDI 2022 ([paper and talk](https://www.usenix.org/conference/osdi22/presentation/yu)).
