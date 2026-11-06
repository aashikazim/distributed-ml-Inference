# ADR-0008: bounded labels and aggregate latency distributions

## Status
Accepted in Phase 5.

## Context
A single average hides requests waiting behind overloaded compute. Individual
request IDs would make excellent log lookup keys but terrible metric labels:
each ID creates another time series and never forms a reusable population.

## Decision
Use prometheus-client counters, gauges and classic histograms with per-process
registries injected into lifecycle objects. Run one Uvicorn process per container.
Keep model, canonical endpoint, HTTP status, outcome and bounded reason labels;
Prometheus adds job/instance for discovery. Unknown models share one label value.
Request IDs, traces, inputs and keys never become labels. Logs carry request and
trace identity; inputs are represented only by lengths.

Reuse LATENCY_BUCKETS: 5, 10, 25, 50, 100, 250, 500 milliseconds, then 1, 2.5, 5,
10, 30 and 60 seconds, plus the automatic infinity bucket. These boundaries cover
short local inference through long chat deadlines. Quantiles are interpolations,
not exact sample percentiles. Sub-5-ms TPOT measurements lose precision here;
raw per-request estimates remain available. Native histograms are attractive but
would add a deployment/client compatibility choice to this learning project.

Observe actual model invocations once for batch-size and compute distributions;
observe local batch wait once per published item. Published outcome counters
include duplicate replay attempts, not guaranteed unique business requests.
Gateway duration covers complete response/stream lifetime, including rejections;
panels must show errors alongside latency. Poll endpoints are separately labeled.
A Redis exporter computes undelivered head age after the consumer group's last
read position; old pending work is not undelivered head age. Snapshots are not
atomic across commands and can transiently differ while work completes.

Prometheus discovers worker IPs through Compose DNS, so scaling replicas does
not silently scrape only one worker. Grafana provisions four dashboards. Define
the p95 alert against platform_latency_slo_seconds without inventing its value;
Phase 8 supplies a measured local proposal. Other alerts cover sustained backlog
growth, no accepting workers, DLQ evidence and stale exporter observations.

## Alternatives considered
- Logs alone: preserve request detail, but aggregation and regression comparison
  require more work and a separate indexing system.
- Client-side summaries: accurate local quantiles, but cannot combine replica
  quantiles into a correct platform quantile.
- Metric labels per request: direct lookup, but unbounded storage/cardinality.
- Export XLEN as backlog: cheap, but includes pending deliveries and misleads scaling.

## Consequences
Classic buckets consume multiple series per label combination and approximate
quantiles. Aggregate first by bucket boundary, then compute a quantile; never
average replica p95 values. Redis polling and scrape delay mean graphs trail
reality. Missing/stale metrics must not be interpreted as a zero queue. Grafana
runs anonymous read-only viewing on localhost for development; production auth,
TLS, retention, Redis requirepass/TLS and HA need deployment-specific setup.


## Phase 8 measurement follow-up

The local classifier proposal now supplies measured 250 ms recording targets;
[SLO.md](../SLO.md) records the data, derivation and limited scope. Live replies
add gateway elapsed/residual metadata at final construction. Canonical cached
worker outcomes do not acquire delivery-specific timings; poll fields remain
null rather than inventing the original gateway duration. The duplicate replay
regression still verifies canonical outcome equality and no extra compute.
All reclaimed attempts now count, including the claim that triggers quarantine.
