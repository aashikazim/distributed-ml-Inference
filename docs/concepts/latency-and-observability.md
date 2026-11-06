# Latency and observability

**Observability** means answering what is happening inside a system from its
outputs. Here those outputs are metrics, structured JSON logs and per-request
metadata. Metrics reveal populations; logs explain one request. Neither requires
logging the owner's text or adding request IDs to metric labels.

## Start with boundaries

Client duration includes validation/auth, admission, Redis queueing, local batch
collection, compute, response transport and client delivery. queue_ms measures
Redis enqueue-to-dequeue using Redis time. batch_wait_ms measures local waiting;
inference_ms measures the shared invocation duration, not amortized item cost.
The leftover is gateway/transport/scheduling overhead; it may also contain retries
and is not a separately synchronized clock measurement. Do not add percentiles:
p95(queue) + p95(compute) need not equal p95(total), because different requests
can occupy those percentiles.

**RED** asks about request Rate, Errors and Duration. Start with Overview.
**USE** asks about resource Utilization, Saturation and Errors. Here worker busy
ratio is utilization, queue backlog/head age indicate saturation, and outcomes/DLQ
indicate failures. CPU utilization alone misses GPU work and efficient batching.

## Counters, gauges and histograms

A counter only increases during a process lifetime; rate(counter[window]) handles
resets and estimates work per second. A gauge can rise or fall: alive workers,
backlog, active requests. A histogram counts observations in cumulative buckets,
plus their sum and count. The sum/count rates estimate a mean. A quantile such
as p95 estimates the point below which 95% of observations fall; it says nothing
about the slowest 5% by itself. p99 is unstable with a small sample population.

Classic histogram_quantile interpolates within configured buckets. A reported
p95 of several seconds can have substantial bucket error. Do not present it as
an exact percentile from raw timings. Aggregate replica bucket rates by model
and le before calculating the quantile. Averaging replica p95 values is invalid.

**Tail latency** is the slow end of a distribution. A queue near saturation can
have a reasonable compute time and awful tail waiting. Rejections can lower an
all-response latency average, so always inspect status/error rates too. Successful
latency and accepted throughput matter alongside admission rejection counts.

## SLI, SLO and error budget

An **SLI** is a measured service indicator, such as the proportion of valid
accepted requests that finish within a chosen duration. An **SLO** is a target
for that indicator over a time window. The **error budget** is the permitted
fraction that can fail that target. A local short benchmark supports a proposal,
not a production promise. Phase 8 now proposes measured local targets and explains
which rejected/failed requests count in [SLO.md](../SLO.md). The Phase 5 rule
initially had no target; measured recording series now enable that comparison.

## Little's Law and labels

For a stable population, average work present L = throughput lambda × average
time W. Specify the boundary: Redis undelivered backlog uses queue waiting time;
whole-system in-flight uses whole-system time. Burst growth and timeouts violate
the steady-state assumptions if treated as one stable interval. Phase 8 predicts
then measures a small stable workload rather than extrapolating a universal limit.

**Cardinality** is the number of distinct label combinations. Request IDs and
input strings create unbounded combinations. Our labels use configured model
names, canonical route templates and bounded statuses/reasons. Prometheus adds
instance to separate processes; request/trace IDs belong in logs. Metrics carry
seconds, not mixed milliseconds; API metadata retains its explicit _ms fields.

## Read the evidence

The exporter uses XLEN minus pending for backlog and XRANGE after last-delivered-id
for undelivered head age. Polls are approximate live snapshots. A stale exporter
can leave old values visible; its last-success timestamp has a separate alert.
A missing worker scrape is not the same as an idle worker. up, targets and exporter
freshness must be checked before trusting an apparently empty graph.

[Dashboard queries](../dashboards.md), [lesson 05](../learning/05-observability.md),
and [ADR-0008](../adr/0008-metrics.md) connect these concepts to code. Primary
reference: [Prometheus histograms and summaries](https://prometheus.io/docs/practices/histograms/).
The current official guidance favors native histograms where supported; this
project deliberately uses classic client histograms with explicit buckets.
