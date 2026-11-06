# Measured local SLO proposal

**Status: proposal for the tested local profile, not a production reliability
claim.** No 30-day observation was run. The values below are derived from the
actual Phase 8 baseline, not invented before measurement.

## Population and boundary

Valid authenticated synchronous classifier submissions with the small benchmark
sentences on the measured ARM kind node, two gateways, two fixed fake workers
or one real CPU sentiment worker. Tested arrival rates are bounded below. Worker
warmup has completed. Fake compute remains simulated. No chat/GPU latency target
is derived from synthetic token output.

Propose p95 successful client HTTP duration below the rounded target, and 99%
valid-submission availability over a proposed 30-day window. 429, 503, 504 and
other 5xx count against valid-submission availability; invalid inputs/auth failures
are outside that defined population. A 1% failure allowance is an error budget,
not permission to hide rejected work from the report.

## Derivation

For each model, take the worst raw per-trial p95, multiply by two for local
headroom, then round up to a shared histogram boundary. This is a transparent
planning rule, not a theorem about future tail latency.

| Model | Observed requests | Failures | Maximum tested req/s | Worst trial p95 ms | Proposed latency ms |
|---|---:|---:|---:|---:|---:|
| fake-classifier (simulated) | 3900 | 0 | 40 | 113.96 | 250 |
| sentiment (real CPU) | 300 | 0 | 5 | 64.97 | 250 |

The smallest baseline population is 300 real-CPU requests with zero observed
failures. A rough independent-trial 3/N failure estimate is about 1%; rounding
that up to a percentage motivates a **99% candidate**, not a measured month-long
service level. Requests share a host and failure domains, so independence is an
especially weak assumption. The error budget is a starting review proposal that
requires longer representative operation before acceptance.

The separate final-image check completed 800 fake and 100 real CPU requests with
p95 110.42 ms and 60.76 ms, still below 250 ms. It validates a short regression
sample after adding gateway timing fields; it does not increase the baseline's
statistical confidence by pretending the methods are identical.

## Alert implementation and interpretation

Recording rules publish platform_latency_slo_seconds per measured model. The
p95 rule compares the five-minute classifier histogram to that target for five
minutes. Histograms include fast error responses too, so the separate sustained
failure-fraction rule is essential; low duration alone cannot establish the
success-conditioned SLI. This alert pair is an operational approximation, not a
complete monthly error-budget accounting system. No data produces no valid
latency estimate; exporter freshness and target health must be checked separately.

Promtool tests simulate sustained latency/failure, backlog, no workers, DLQ and
stale exporter conditions, including checks that five-minute alerts do not fire
too early. Real dashboard queries and the E7 injection are separate evidence.
No external notification receiver is configured.

## Acceptance still required

Run longer tests with representative input lengths, burst patterns, outages,
resources and network placement. Measure success-conditioned latency directly,
including failures/rejections in the agreed availability population. Choose
notification routing and durable retention. Revisit admission depth: the kind
value 1000 was chosen for visible experiments, not derived to guarantee 250 ms.

Source: [p8-slo-proposal.json](results/p8-slo-proposal.json),
[benchmarks](BENCHMARKS.md), [capacity worksheet](CAPACITY_PLANNING.md) and
[runbook](RUNBOOK.md). Regeneration is the executed tools.bench.propose_slo command
in the benchmark guide; review changes before adopting a different environment's
numbers. GPU Kubernetes, hosted CI and production Redis HA remain outside this
local proposal's verified scope.
