# Learn the inference platform

Phase 5 has a real distributed request path, dynamic batching, CPU-verified
DistilBERT, bounded reliability controls, and streaming chat verified on real GB10
vLLM and a fake TCP server. Fake inference remains **simulated**. Start with
[the repository quickstart](../README.md), then follow this order:

1. [Big picture](learning/00-big-picture.md): responsibilities and boundaries.
2. [Architecture](ARCHITECTURE.md): container and request sequence diagrams.
3. [Redis Streams](concepts/redis-streams.md): delivery versus acknowledgement.
4. [Request-path lesson](learning/01-request-path.md): follow one input through code.
5. [E1](EXPERIMENTS.md): predict, kill a worker, inspect pending work.
6. [ADRs](adr/0001-redis-streams.md): queue choice, then
   [inboxes](adr/0002-gateway-inboxes.md) and [pull distribution](adr/0003-pull-distribution.md).

Next read [dynamic batching](learning/02-dynamic-batching.md), its
[concept guide](concepts/batching.md), [ADR-0004](adr/0004-dynamic-batching.md),
and E2/E3 in the experiment record.

Continue with [lesson 03](learning/03-reliability.md),
[reliability/backpressure](concepts/reliability-and-backpressure.md),
[ADR-0005](adr/0005-delivery-semantics.md), [ADR-0006](adr/0006-admission-control.md),
and E4/E5. Then read [lesson 04](learning/04-llm-serving.md),
[LLM concepts](concepts/llm-serving.md), [ADR-0007](adr/0007-llm-adapter.md),
[GPU verification](GPU_VERIFICATION.md), and E6a/E6b.

Use the [study plan](learning/README.md) for the full roadmap. Keep the
[glossary](GLOSSARY.md) nearby. Read [configuration](CONFIG.md) before changing
settings, and [progress](PROGRESS.md) to distinguish working code from future work.

Study by running the system, reading the relevant functions, breaking one boundary,
then explaining the observation aloud. Copying commands alone does not establish
understanding: predict XPENDING and XLEN first, then compare the actual result.

Future document slots are explicit: BENCHMARKS.md and RUNBOOK.md arrive in Phase 8,
Later lessons, concept
documents and ADRs are written when their implementation exists, not prefilled
with unverified claims.

Continue with [lesson 05](learning/05-observability.md), [latency concepts](concepts/latency-and-observability.md), [dashboard queries](dashboards.md) and [ADR-0008](adr/0008-metrics.md).

Then study [lesson 06](learning/06-autoscaling.md), [autoscaling concepts](concepts/autoscaling.md), [ADR-0009](adr/0009-autoscaling.md), and simulated E8–E10 before actual kind deployment.
