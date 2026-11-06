# Glossary

| Term | Plain-language meaning |
|---|---|
| Inference | Running an already trained model to produce an output |
| Replica | One running copy of a model-serving worker |
| Gateway | HTTP service that accepts requests and waits for worker replies |
| Backend | Implementation that performs model work; fake simulation or real torch classifier |
| Stream | Redis sequence of entries with IDs and string fields |
| Consumer | Named reader in a Redis consumer group; one worker here |
| Consumer group | Shared delivery ledger that distributes new entries among consumers |
| Pending-entry list (PEL) | Entries delivered to a group consumer but not acknowledged |
| ACK / XACK | Mark a delivered stream entry as handled for a group |
| XDEL | Delete the actual entry from a stream |
| Inbox | Response stream belonging to one gateway lifetime |
| Waiter | Bounded local asyncio queue waiting for one request's response |
| Cursor | Last stream ID a reader has processed |
| Pull distribution | Available workers ask for work rather than receive assigned pushes |
| Backlog | Work not yet delivered to a worker |
| In-flight | Work delivered and not finished |
| Idempotency | Repeating work does not create additional external side effects |
| At-least-once delivery | Retry-capable handling may process the same work more than once |
| Reclaim | Transfer idle pending work to a new worker; Phase 3 |
| Dead-letter queue (DLQ) | Separate place for repeatedly failing work; Phase 3 |
| Deadline | Point beyond which work or waiting should stop |
| Timeout | Maximum permitted duration for an operation |
| Monotonic clock | Clock for elapsed time that is not adjusted like wall time |
| Executor | Thread pool used to keep blocking compute off the event loop |
| Event loop | Scheduler that lets asynchronous I/O tasks share one thread |
| Lifespan | Application startup/shutdown context managing resources |
| Backpressure | Preventing fast producers from overwhelming slower consumers |
| Admission control | Rejecting new work when capacity is insufficient; Phase 3 |
| Dynamic batching | Gathering independent inputs into a compute batch with a size and wait policy |
| Continuous batching | Updating the active LLM sequence batch during generation; later vLLM |
| Latency | Elapsed time from a defined start to finish |
| Throughput | Completed work per unit time |
| SLO | Measured service reliability or latency target; derived in Phase 8 |
| Autoscaling | Adjusting replica counts using a repeated feedback decision |
| AOF | Redis append-only persistence log |
| SSE | HTTP server-sent event stream; Phase 4 |
| TTL | Time to live before a Redis key expires |
| ADR | Architecture decision record explaining a choice and its costs |


| Term | Plain-language meaning |
|---|---|
| Work-conserving flush | Dispatch when eligible and available; keep filling while the runner is occupied |
| Prefetch bound | Maximum locally accepted work, including active, staged and queued inputs here |
| Staged batch | Next batch collecting inputs while the previous invocation is active |
| Dynamic padding | Fill shorter token rows up to the longest row in this batch |
| Token | Unit of text represented by an integer ID for the model |
| Attention mask | Marks real token positions so padding is not treated as input content |
| Truncation | Cut tokenized input to a maximum length (256 tokens in this runner) |
| Warmup | Initial prediction before the worker starts accepting work |
| Evaluation mode | Model mode disabling training behavior such as dropout |
| Inference mode | PyTorch context avoiding gradient bookkeeping during prediction |
| Intra-op threads | CPU threads cooperating within one model operation |
| Amortized item cost | Shared batch execution time divided by number of items; not item latency |
| Poison input | Input that repeatedly makes inference fail |
| Safetensors | Tensor-weight serialization format used instead of pickle model weights |
| Little's Law | In a stable interval, average work present equals throughput times average time |
