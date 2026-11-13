# Learn the inference platform

This repository has two equal deliverables: a working local serving platform and
an explanation you can test. Start with the [quickstart](../README.md), then use
one lesson per sitting. Predict an observation before running its experiment.

| Read in order | Companion and evidence |
|---|---|
| [00: Big picture](learning/00-big-picture.md) | [Architecture](ARCHITECTURE.md), responsibilities and boundaries |
| [Walkthrough: one request, every hop](learning/WALKTHROUGH.md) | Every Redis command of one real request, captured live |
| [01: Request path](learning/01-request-path.md) | [Redis Streams](concepts/redis-streams.md), E1, ADRs 0001–0003 |
| [02: Dynamic batching](learning/02-dynamic-batching.md) | [Batching](concepts/batching.md), E2/E3, [ADR-0004](adr/0004-dynamic-batching.md) |
| [03: Reliability](learning/03-reliability.md) | [Backpressure](concepts/reliability-and-backpressure.md), E4/E5, ADRs 0005–0006 |
| [04: LLM serving](learning/04-llm-serving.md) | [LLM concepts](concepts/llm-serving.md), E6a/E6b, [ADR-0007](adr/0007-llm-adapter.md), [GPU evidence](GPU_VERIFICATION.md) |
| [05: Observability](learning/05-observability.md) | [Latency](concepts/latency-and-observability.md), [dashboards](dashboards.md), E7, [ADR-0008](adr/0008-metrics.md) |
| [06: Autoscaling](learning/06-autoscaling.md) | [Controller concepts and HPA/KEDA](concepts/autoscaling.md), E8–E10, [ADR-0009](adr/0009-autoscaling.md) |
| [07: Deployment](learning/07-kubernetes-deployment.md) | [Deployment concepts](concepts/deploying-ml-workloads.md), real kind E11–E13, [ADR-0010](adr/0010-deployment.md) |
| [08: Capstone](learning/08-capstone.md) | [Benchmarks](BENCHMARKS.md), [capacity worksheet](CAPACITY_PLANNING.md), [SLO proposal](SLO.md), [runbook](RUNBOOK.md), [audit](VERIFICATION.md) |

Use the [full study plan](learning/README.md) for a concept map and Redis cheat
sheet. The loop is **Run → Read → Break → Explain**. Run a known path, read its
functions, break one boundary, then teach the observation without notes. If you
cannot predict XPENDING, batch size or queue age, return to the relevant concept
instead of copying another command.

Keep the [glossary](GLOSSARY.md) and [configuration](CONFIG.md) nearby. All metrics
have explicit units and populations; all simulated inference is labeled. Hardware,
source, predictions and measured surprises live in [EXPERIMENTS.md](EXPERIMENTS.md).
The [progress record](PROGRESS.md) preserves phase history.
The [wrap-up verification](FINAL_VERIFICATION.md) records hosted CI, actual GPU
kind, snapshot restore and bounded-soak results for the localhost sign-off. The final audit is the
source for what is actually verified versus pending; document existence alone
cannot prove a new reader understands the system.
