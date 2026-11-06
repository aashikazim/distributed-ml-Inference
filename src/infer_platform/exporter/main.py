import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI
from fastapi.responses import Response
from prometheus_client import CollectorRegistry, Gauge, generate_latest
from redis.asyncio import Redis
from redis.exceptions import RedisError

from infer_platform.common.clock import Clock, SystemClock
from infer_platform.common.logging import configure_logging
from infer_platform.common.queue_stats import snapshot
from infer_platform.common.redis import create_redis
from infer_platform.common.settings import Settings


class Exporter:
    def __init__(self, redis: Redis, models: list[str], clock: Clock) -> None:
        self.redis, self.models, self.clock = redis, models, clock
        self.registry = CollectorRegistry()
        self.gauges = {
            name: Gauge(name, help, ["model"], registry=self.registry)
            for name, help in {
                "queue_backlog": "Undelivered retained work",
                "queue_pending": "Unacknowledged deliveries",
                "queue_head_age_seconds": "Age of oldest undelivered entry",
                "workers_alive": "Accepting TTL registrations",
                "queue_dlq_entries": "Retained DLQ evidence",
            }.items()
        }
        self.success = Gauge(
            "exporter_last_success_timestamp_seconds",
            "Last complete Redis poll",
            registry=self.registry,
        )

    async def poll(self) -> None:
        for model in self.models:
            state = await snapshot(self.redis, model)
            for name, value in {
                "queue_backlog": state.backlog,
                "queue_pending": state.pending,
                "queue_head_age_seconds": state.head_age,
                "workers_alive": state.workers,
                "queue_dlq_entries": await self.redis.xlen("dlq:" + model),
            }.items():
                self.gauges[name].labels(model).set(value)
        seconds, micros = await self.redis.time()
        self.success.set(seconds + micros / 1e6)

    async def run(self, interval: float) -> None:
        while True:
            try:
                await self.poll()
            except RedisError:
                structlog.get_logger().warning("exporter_poll_failed", component="exporter")
            await self.clock.sleep(interval)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
        configure_logging()
        redis = create_redis(settings)
        exporter = Exporter(redis, list(settings.load_models()), SystemClock())
        app.state.exporter = exporter
        task = asyncio.create_task(exporter.run(settings.exporter_interval_s))
        try:
            yield
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await redis.aclose()

    app = FastAPI(lifespan=lifespan)

    @app.get("/healthz")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/metrics")
    async def metrics() -> Response:
        return Response(
            generate_latest(app.state.exporter.registry), media_type="text/plain; version=0.0.4"
        )

    return app
