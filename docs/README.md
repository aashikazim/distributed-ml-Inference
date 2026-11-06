# Learn the inference platform

Phase 1 has a real distributed request path and a **simulated** model. Start with
[the repository quickstart](../README.md), then follow this order:

1. [Big picture](learning/00-big-picture.md): responsibilities and boundaries.
2. [Architecture](ARCHITECTURE.md): container and request sequence diagrams.
3. [Redis Streams](concepts/redis-streams.md): delivery versus acknowledgement.
4. [Request-path lesson](learning/01-request-path.md): follow one input through code.
5. [E1](EXPERIMENTS.md): predict, kill a worker, inspect pending work.
6. [ADRs](adr/0001-redis-streams.md): queue choice, then
   [inboxes](adr/0002-gateway-inboxes.md) and [pull distribution](adr/0003-pull-distribution.md).

Use the [study plan](learning/README.md) for the full roadmap. Keep the
[glossary](GLOSSARY.md) nearby. Read [configuration](CONFIG.md) before changing
settings, and [progress](PROGRESS.md) to distinguish working code from future work.

Study by running the system, reading the relevant functions, breaking one boundary,
then explaining the observation aloud. Copying commands alone does not establish
understanding: predict XPENDING and XLEN first, then compare the actual result.

Future document slots are explicit: BENCHMARKS.md and RUNBOOK.md arrive in Phase 8,
GPU_VERIFICATION.md in Phase 4, dashboards.md in Phase 5. Later lessons, concept
documents and ADRs are written when their implementation exists, not prefilled
with unverified claims.
