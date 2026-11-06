# Glossary

| Term | Plain-language meaning |
|---|---|
| Inference | Running an already trained model to produce an output |
| Replica | One running copy of a model-serving worker |
| Gateway | HTTP service that accepts requests and waits for worker replies |
| Backend | Implementation that performs model work; currently a simulation |
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
| Dynamic batching | Gathering independent inputs into a compute batch; Phase 2 |
| Continuous batching | Updating the active LLM sequence batch during generation; later vLLM |
| Latency | Elapsed time from a defined start to finish |
| Throughput | Completed work per unit time |
| SLO | Measured service reliability or latency target; derived in Phase 8 |
| Autoscaling | Adjusting replica counts using a repeated feedback decision |
| AOF | Redis append-only persistence log |
| SSE | HTTP server-sent event stream; Phase 4 |
| TTL | Time to live before a Redis key expires |
| ADR | Architecture decision record explaining a choice and its costs |
