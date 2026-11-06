import json
import time
from uuid import uuid4

import structlog
from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from infer_platform.common.metrics import LATENCY_BUCKETS


class GatewayMetrics:
    def __init__(self) -> None:
        self.registry = CollectorRegistry()
        self.requests = Counter(
            "gateway_requests_total",
            "HTTP responses including rejections",
            ["model", "endpoint", "status"],
            registry=self.registry,
        )
        self.duration = Histogram(
            "gateway_request_duration_seconds",
            "Full HTTP handler/stream lifetime",
            ["model", "endpoint"],
            buckets=LATENCY_BUCKETS,
            registry=self.registry,
        )
        self.inflight = Gauge(
            "gateway_inflight_requests", "Active HTTP inference handlers", registry=self.registry
        )
        self.rejected = Counter(
            "gateway_rejected_total", "Rejected requests", ["reason"], registry=self.registry
        )
        self.queue = Histogram(
            "gateway_queue_wait_seconds",
            "Worker-reported Redis queue time",
            ["model"],
            buckets=LATENCY_BUCKETS,
            registry=self.registry,
        )
        self.ttft = Histogram(
            "gateway_ttft_seconds",
            "Request to first SSE content",
            ["model"],
            buckets=LATENCY_BUCKETS,
            registry=self.registry,
        )


class ObserveHTTP:
    """Observe complete SSE lifetime without buffering a streaming response."""

    def __init__(self, app: ASGIApp, metrics: GatewayMetrics, models: set[str]) -> None:
        self.app, self.metrics, self.models = app, metrics, models

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not scope["path"].startswith("/v1/"):
            await self.app(scope, receive, send)
            return
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(
            component="gateway", worker_id=None, request_id=uuid4().hex, trace_id=uuid4().hex
        )
        started = time.monotonic()
        status = 500
        payload = bytearray()
        streaming = False
        self.metrics.inflight.inc()

        async def observe(message: Message) -> None:
            nonlocal status, streaming
            if message["type"] == "http.response.start":
                status = message["status"]
                streaming = any(
                    k == b"content-type" and b"text/event-stream" in v
                    for k, v in message.get("headers", [])
                )
            elif message["type"] == "http.response.body" and not streaming and len(payload) < 65536:
                payload.extend(message.get("body", b"")[: 65536 - len(payload)])
            await send(message)

        try:
            await self.app(scope, receive, observe)
        finally:
            path = scope["path"]
            endpoint = (
                "/v1/infer/{model}"
                if path.startswith("/v1/infer/")
                else "/v1/results/{request_id}"
                if path.startswith("/v1/results/")
                else "/v1/chat/completions"
                if path == "/v1/chat/completions"
                else "/v1/models"
                if path == "/v1/models"
                else "unknown"
            )
            candidate = scope.get(
                "metric_model",
                path.rsplit("/", 1)[-1] if endpoint == "/v1/infer/{model}" else "unknown",
            )
            model = candidate if candidate in self.models else "unknown"
            elapsed = time.monotonic() - started
            self.metrics.inflight.dec()
            self.metrics.requests.labels(model, endpoint, str(status)).inc()
            self.metrics.duration.labels(model, endpoint).observe(elapsed)
            try:
                data = json.loads(payload) if payload else {}
            except (ValueError, UnicodeError):
                data = {}
            if status >= 400:
                self.metrics.rejected.labels(data.get("error", {}).get("code", "HTTP_ERROR")).inc()
            meta = scope.get("metric_meta", data.get("meta", {}))
            if meta:
                self.metrics.queue.labels(model).observe(meta.get("queue_ms", 0) / 1000)
            structlog.get_logger().info(
                "http_finished", model=model, endpoint=endpoint, status=status, duration_s=elapsed
            )
            structlog.contextvars.clear_contextvars()
