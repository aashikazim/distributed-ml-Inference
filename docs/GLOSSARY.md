# Glossary

| Term | Plain-language meaning |
|---|---|
| Inference | Running an already trained model to produce an output |
| Replica | One running copy of a model-serving worker |
| Gateway | HTTP service that accepts requests and waits for worker replies |
| Backend | Implementation that performs model work; fake simulation, torch classifier, or LLM adapter |
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
| Reclaim | Transfer sufficiently idle pending ownership using XAUTOCLAIM |
| Dead-letter queue (DLQ) | Bounded stream holding repeatedly failing envelopes, errors and delivery counts |
| Deadline | Point beyond which work or waiting should stop |
| Timeout | Maximum permitted duration for an operation |
| Monotonic clock | Clock for elapsed time that is not adjusted like wall time |
| Executor | Thread pool used to keep blocking compute off the event loop |
| Event loop | Scheduler that lets asynchronous I/O tasks share one thread |
| Lifespan | Application startup/shutdown context managing resources |
| Backpressure | Preventing fast producers from overwhelming slower consumers |
| Admission control | Rejecting new work before enqueue when retained depth or live capacity is insufficient |
| Dynamic batching | Gathering independent inputs into a compute batch with a size and wait policy |
| Continuous batching | Updating the active LLM sequence set during generation, owned by vLLM |
| Latency | Elapsed time from a defined start to finish |
| Throughput | Completed work per unit time |
| SLO | Measured service reliability or latency target; derived in Phase 8 |
| Autoscaling | Adjusting replica counts using a repeated feedback decision |
| AOF | Redis append-only persistence log |
| SSE | HTTP server-sent events with data frames separated by blank lines |
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


| Term | Plain-language meaning |
|---|---|
| Delivery count | Initial delivery plus redeliveries/claims recorded in the pending ledger |
| Heartbeat | Periodically refreshed TTL registration advertising a worker's capacity |
| Reclaim idle threshold | Minimum time since delivery/claim before ownership may transfer |
| Token bucket | Per-key rate budget that refills over Redis server time, up to a burst cap |
| Load shedding | Rejecting excess requests early so accepted work has bounded waiting |
| Retry storm | Many callers repeating failed work together and amplifying overload |
| Tail latency | Slow end of the request-latency distribution |
| SIGTERM | Signal asking a worker to stop reading, drain and exit cleanly |
| SIGKILL | Immediate process termination; no cleanup code can run |
| Redis transaction | MULTI/EXEC group of commands executed without other clients interleaving |
| Blocking connection pool | Fixed connection budget whose callers wait with a timeout for free slots |
| Async polling | Fetching the eventual outcome using an accepted request ID |


| Term | Plain-language meaning |
|---|---|
| Prefill | Processing prompt tokens before generating new content |
| Decode | Repeatedly generating the next token using previous attention state |
| KV cache | Stored attention keys and values used to avoid recomputing previous tokens |
| PagedAttention | Block-based management of attention-cache storage, implemented by vLLM |
| TTFT | Time to first content token, measured from an explicitly named starting point |
| TPOT | Time per output token during generation; our HTTP observation is an estimate |
| Sequence | One prompt and its growing generated output |
| Semaphore | Counter that limits how many concurrent tasks may enter a protected operation |
| Chunk coalescing | Bundling original upstream chunks into fewer Redis writes within a short window |
| Prefix caching | Reusing engine attention state for shared prompt prefixes |
| Eager execution | Running model operations without CUDA graph replay in this tested server mode |
| Stream interruption | Explicit failure when a partially emitted answer cannot safely finish or resume |


| Term | Plain-language meaning |
|---|---|
| Histogram | Bucket counts plus sum/count describing a population of observations |
| Quantile / percentile | Estimated boundary below which a chosen fraction of observations falls |
| SLI | Measured service-level indicator |
| Error budget | Fraction of outcomes an SLO permits to miss its target |
| RED | Request rate, errors and duration |
| USE | Resource utilization, saturation and errors |
| Cardinality | Number of distinct metric-label combinations |
| Scrape | Prometheus polling an HTTP metrics endpoint |
| Exporter | Process translating Redis state into measurable gauges |


| Term | Plain-language meaning |
|---|---|
| Control loop | Repeated observation, decision and actuation followed by new observation |
| Actuator | Adapter that applies a desired replica count |
| Cooldown | Minimum elapsed time before another change in a direction |
| Stabilization window | Recent-demand memory delaying downscale |
| Hysteresis | Policy that resists rapid reversal around a changing signal |
| Replica-second | One desired replica reserved for one second; a simulated cost proxy here |
| Leadership lease | Expiring token authorizing one controller to act while it owns renewal |
| Fencing | Rejecting stale writers even if an old operation arrives after leadership changes |
| HPA | Kubernetes Horizontal Pod Autoscaler |
| KEDA | Kubernetes event-driven autoscaling project with existing event-source scalers |


| Term | Plain-language meaning |
|---|---|
| Pod | Kubernetes scheduling/lifecycle unit containing one or more containers |
| Deployment | Controller maintaining interchangeable Pods at a desired replica count |
| StatefulSet | Controller associating stable Pod identity with storage |
| PVC | PersistentVolumeClaim asking the cluster for storage |
| Startup probe | Initialization allowance before other health probes govern restarts |
| Readiness probe | Check determining whether a Pod is available for traffic |
| Liveness probe | Process health check used to decide restarts |
| PDB | PodDisruptionBudget limiting voluntary eviction, not force kills |
| RBAC | Role-based authorization limiting which API operations a service account can perform |
| Headless Service | DNS discovery of individual ready Pod IPs rather than one load-balanced service address |
| Kustomize | Composition of Kubernetes resources and focused overlay changes |


| Term | Plain-language meaning |
|---|---|
| Open-loop load | Arrivals scheduled independently of earlier response completion |
| Closed-loop load | A fixed user population issues more work after earlier work and think time |
| Coordinated omission | A slow system also slows closed-loop arrivals, hiding some fixed-rate waiting |
| System boundary | Exact entry/exit points and population included in a measurement |
| Service-level proposal | A candidate target requiring representative operation and review before acceptance |
| Speculative decoding | An LLM engine technique: a cheap proposer guesses several next tokens and the full model checks them all in one pass. It is lossless with standard verification. Not enabled in this project |
| Proposer (drafter) | The cheap source of guessed tokens in speculative decoding: n-gram prompt lookup, a small draft model, or EAGLE/MTP heads |
| Acceptance rate | Fraction of guessed tokens the target model accepts; it decides whether speculative decoding helps |
| Bucket resolution | Width of a histogram bucket at a given latency. Percentile estimates are only as precise as the bucket containing them |
| Coalescing window | Short delay that bundles several streamed LLM chunks into one Redis write |
