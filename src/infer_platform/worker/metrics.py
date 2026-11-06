from prometheus_client import Counter, Gauge, Histogram

from infer_platform.common.metrics import LATENCY_BUCKETS
from infer_platform.worker.llm_metrics import LLMMetrics


class WorkerMetrics(LLMMetrics):
    def __init__(self) -> None:
        super().__init__()
        self.batch = Histogram(
            "worker_batch_size",
            "Actual model invocation size",
            ["model"],
            buckets=(1, 2, 4, 8, 16, 32, 64),
            registry=self.registry,
        )
        self.wait = Histogram(
            "worker_batch_wait_seconds",
            "Per-item local wait",
            ["model"],
            buckets=LATENCY_BUCKETS,
            registry=self.registry,
        )
        self.inference = Histogram(
            "worker_inference_seconds",
            "Actual model invocation duration",
            ["model"],
            buckets=LATENCY_BUCKETS,
            registry=self.registry,
        )
        self.items = Counter(
            "worker_items_total",
            "Published outcomes, including replay attempts",
            ["model", "outcome"],
            registry=self.registry,
        )
        self.busy = Gauge(
            "worker_busy_ratio",
            "Recent fraction of wall time busy",
            ["model"],
            registry=self.registry,
        )
        self.reclaimed = Counter(
            "worker_reclaimed_total", "Claimed delivery attempts", ["model"], registry=self.registry
        )
        self.dlq = Counter(
            "worker_dlq_total", "Quarantine attempts", ["model"], registry=self.registry
        )

    def invocation(self, model: str, size: int, seconds: float) -> None:
        self.batch.labels(model).observe(size)
        self.inference.labels(model).observe(seconds)
