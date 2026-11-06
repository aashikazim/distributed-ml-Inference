# Dashboard reading guide

Provisioned datasource UID: prometheus. Grafana: localhost:3000; Prometheus:
localhost:9090. The development stack exposes anonymous read-only Grafana on
localhost. Panels refresh every five seconds; rates use a one-minute window.
Allow several scrapes before interpreting them. Empty is not necessarily zero.

Read Overview first (RED: rate/errors/duration), then Batching (utilization and
saturation), then LLM or Autoscaler if that backend is active. Check scrape target
health and exporter freshness before trusting a quiet graph. No screenshots were
captured; every panel and exact query is documented below and tested through APIs.

## Autoscaler

**Desired replicas**

```promql
autoscaler_desired_replicas
```

**Current replicas**

```promql
autoscaler_current_replicas
```

**Backlog**

```promql
queue_backlog
```

**Decisions / second**

```promql
sum by (model,direction) (rate(autoscaler_decisions_total[1m]))
```


## Batching

**Batch size mean**

```promql
sum by (model) (rate(worker_batch_size_sum[1m])) / sum by (model) (rate(worker_batch_size_count[1m]))
```

**Batch size distribution**

```promql
sum by (le,model) (rate(worker_batch_size_bucket[1m]))
```

**Batch wait mean**

```promql
sum by (model) (rate(worker_batch_wait_seconds_sum[1m])) / sum by (model) (rate(worker_batch_wait_seconds_count[1m]))
```

**Inference mean**

```promql
sum by (model) (rate(worker_inference_seconds_sum[1m])) / sum by (model) (rate(worker_inference_seconds_count[1m]))
```

**Busy ratio**

```promql
worker_busy_ratio
```

**Outcomes / second**

```promql
sum by (model,outcome) (rate(worker_items_total[1m]))
```

**Reclaims**

```promql
sum by (model) (increase(worker_reclaimed_total[5m]))
```


## LLM

**Worker TTFT p95**

```promql
histogram_quantile(0.95, sum by (le,model) (rate(worker_ttft_seconds_bucket[1m])))
```

**Gateway TTFT p95**

```promql
histogram_quantile(0.95, sum by (le,model) (rate(gateway_ttft_seconds_bucket[1m])))
```

**TPOT mean**

```promql
sum by (model) (rate(worker_tpot_seconds_sum[1m])) / sum by (model) (rate(worker_tpot_seconds_count[1m]))
```

**Completion tokens / second**

```promql
sum by (model) (rate(worker_tokens_generated_total[1m]))
```

**Active sequences**

```promql
worker_inflight_sequences
```

**Engine active sequences**

```promql
vllm:num_requests_running
```


## Overview

**RPS**

```promql
sum by (model,status) (rate(gateway_requests_total[1m]))
```

**Latency p50**

```promql
histogram_quantile(0.5, sum by (le,model) (rate(gateway_request_duration_seconds_bucket[1m])))
```

**Latency p95**

```promql
histogram_quantile(0.95, sum by (le,model) (rate(gateway_request_duration_seconds_bucket[1m])))
```

**Latency p99**

```promql
histogram_quantile(0.99, sum by (le,model) (rate(gateway_request_duration_seconds_bucket[1m])))
```

**Errors / second**

```promql
sum by (model,status) (rate(gateway_requests_total{status=~"4..|5.."}[1m]))
```

**Backlog**

```promql
queue_backlog
```

**Workers alive**

```promql
workers_alive
```

**Queue wait mean**

```promql
sum by (model) (rate(gateway_queue_wait_seconds_sum[1m])) / sum by (model) (rate(gateway_queue_wait_seconds_count[1m]))
```

**Head age**

```promql
queue_head_age_seconds
```

**Pending**

```promql
queue_pending
```

**DLQ entries**

```promql
queue_dlq_entries
```


## How to interpret and diagnose

Overview RPS and errors distinguish completed traffic from fast rejection. p50,
p95 and p99 describe different points of the histogram; they are approximate,
especially with wide buckets or few samples. Queue wait is the worker-reported
Redis wait, while head age describes currently undelivered work. Pending measures
accepted deliveries, not undelivered backlog. Workers alive counts accepting TTL
registrations, not historical consumers.

Batching shows actual invocation size and cumulative size buckets. Batch wait is
local per-item collection/staging time; inference mean is actual invocation time.
A busy fraction near one, batches near one and growing queue wait suggest low
amortized capacity. Healthy compute time plus growing queue age suggests too few
workers or an insufficient batching policy, not automatically a slower model.
Outcomes include published replay attempts; reclaims show delivery recovery.

LLM separates worker TTFT (after dequeue) from gateway TTFT (including queueing
and coalescing). TPOT is an HTTP observation estimate using final usage. Token
rate omits partial streams without final reported usage. Engine active sequences
are real with vLLM, simulated with fake-openai; check the enabled profile.

Autoscaler desired/current are deployment recommendations and observed spec
replicas, not ready pod counts. Read Workers alive alongside them. Decisions are
bounded up/down/hold directions. These panels are populated by the implemented controller; E13 verifies real kind changes.

E7 diagnosis uses only saved Overview/Batching expressions and their Prometheus
responses. See [the measured record](EXPERIMENTS.md) and
[raw evidence](results/e7-monitoring.json). Do not infer an exact root cause from
one panel: correlate arrival rate, busy fraction, batch size, compute duration,
queue age and errors. A slow Redis or missing scrape can produce superficially
similar symptoms; the freshness and target checks separate those cases.

## Alerts

BacklogGrowing requires positive five-minute backlog slope for five minutes.
NoWorkersAlive identifies configured models without accepting heartbeats for
30 seconds; partial dev profiles intentionally leave some configured models idle.
DeadLetterQueueNonEmpty signals retained evidence, not necessarily a current
request storm. ExporterStale warns about frozen gauges. InferenceLatencyAboveMeasuredSLO
compares p95 against platform_latency_slo_seconds for five minutes; Phase 8 now supplies
measured local candidate series, 250 ms for the tested classifier scopes; see SLO.md.
Alert rules evaluate in Prometheus; an external notification receiver is not
configured. Diagnosis still requires the panels, not just the alert name.


Phase 8 adds InferenceFailureBudgetExceeded for sustained valid-submission
429/5xx fraction above the local candidate budget. Histogram duration includes
errors too, so inspect this alongside latency. Promtool synthetic-series tests
verify all sustained alerts, including no early five-minute firing. The target
series and alert pair are an operational approximation of the scoped proposal;
no 30-day reliability window or notification delivery was verified.


## Inspect the verified kind stack

When the dedicated cluster is running, these executed commands expose its
monitoring services on localhost. Use separate terminals and avoid starting a
second forward if that port is already active:

```bash
kubectl --context kind-infer-platform -n infer-platform port-forward service/grafana 13000:3000
kubectl --context kind-infer-platform -n infer-platform port-forward service/prometheus 19090:9090
```

Use KUBECONFIG=/tmp/infer-kind-kubeconfig as in the deployment lesson. A Pod
replacement can invalidate an existing port-forward; restart the forward if it
reports a lost Pod connection. This is local access tooling, not a production
ingress or availability mechanism.
