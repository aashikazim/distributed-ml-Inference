LATENCY_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60)


class AutoscalerMetrics:
    def __init__(self) -> None:
        from prometheus_client import CollectorRegistry, Counter, Gauge

        self.registry = CollectorRegistry()
        self.desired = Gauge(
            "autoscaler_desired_replicas",
            "Latest recommendation",
            ["model"],
            registry=self.registry,
        )
        self.current = Gauge(
            "autoscaler_current_replicas",
            "Observed deployment desired replicas",
            ["model"],
            registry=self.registry,
        )
        self.decisions = Counter(
            "autoscaler_decisions_total",
            "Controller decisions",
            ["model", "direction"],
            registry=self.registry,
        )
