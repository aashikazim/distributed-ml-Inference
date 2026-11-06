from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram

from infer_platform.common.metrics import LATENCY_BUCKETS


class LLMMetrics:
    def __init__(self) -> None:
        self.registry = CollectorRegistry()
        self.ttft = Histogram(
            "worker_ttft_seconds",
            "Dequeue to first content",
            ["model"],
            buckets=LATENCY_BUCKETS,
            registry=self.registry,
        )
        self.tpot = Histogram(
            "worker_tpot_seconds",
            "Observed inter-token estimate",
            ["model"],
            buckets=LATENCY_BUCKETS,
            registry=self.registry,
        )
        self.tokens = Counter(
            "worker_tokens_generated_total",
            "Upstream completion usage",
            ["model"],
            registry=self.registry,
        )
        self.inflight = Gauge(
            "worker_inflight_sequences",
            "Active upstream sequences",
            ["model"],
            registry=self.registry,
        )
