"""Shared real-process drill helpers; fake compute, real Redis and process signals."""

import asyncio
import json
import os
import subprocess
import sys
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
import yaml
from pydantic import SecretStr
from redis.asyncio import Redis

from infer_platform.common.registry import live_workers
from infer_platform.common.settings import Settings


async def eventually(check: Callable[[], Awaitable[Any]], seconds: float = 10) -> Any:
    async with asyncio.timeout(seconds):
        while True:
            value = await check()
            if value:
                return value
            await asyncio.sleep(0.01)


def settings_for(
    root: Path,
    url: str,
    *,
    model: str | None = None,
    base_ms: float = 200,
    idle_ms: int = 500,
    deadline_ms: int = 6000,
    depth: int = 100,
    batch: int = 4,
    **overrides: Any,
) -> Settings:
    model = model or "drill-" + uuid4().hex
    path = root / f"{uuid4().hex}.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "models": {
                    model: {
                        "backend": "fake",
                        "fake": {"base_ms": base_ms, "per_item_ms": 1, "jitter_ms": 0},
                        "max_batch_size": batch,
                        "max_wait_ms": 20,
                        "prefetch_bound": batch,
                        "request_timeout_ms": deadline_ms,
                        "max_queue_depth": depth,
                        "reclaim_idle_ms": idle_ms,
                        "reclaim_interval_ms": 50,
                        "max_deliveries": 3,
                    }
                }
            }
        )
    )
    defaults = dict(
        redis_url=url,
        api_keys=[SecretStr("drill-" + uuid4().hex)],
        models_path=path,
        worker_model=model,
        heartbeat_interval_s=0.02,
        heartbeat_ttl_s=1,
        rate_limit_per_s=100000,
        rate_limit_burst=100000,
    )
    defaults.update(overrides)
    return Settings(**defaults)


class WorkerProcess:
    def __init__(self, settings: Settings, root: Path) -> None:
        self.log_path = root / f"worker-{uuid4().hex}.log"
        env = {
            **os.environ,
            "INFER_WORKER_METRICS_PORT": "0",
            "INFER_REDIS_URL": settings.redis_url.get_secret_value(),
            "INFER_API_KEYS": json.dumps([key.get_secret_value() for key in settings.api_keys]),
            "INFER_MODELS_PATH": str(settings.models_path),
            "INFER_WORKER_MODEL": settings.worker_model,
            "INFER_HEARTBEAT_INTERVAL_S": str(settings.heartbeat_interval_s),
            "INFER_HEARTBEAT_TTL_S": str(settings.heartbeat_ttl_s),
            "INFER_RESULT_TTL_S": str(settings.result_ttl_s),
        }
        with self.log_path.open("wb") as output:
            self.process = subprocess.Popen(
                [sys.executable, "-m", "infer_platform.worker.main"],
                env=env,
                stdout=output,
                stderr=subprocess.STDOUT,
            )

    def events(self) -> list[dict[str, Any]]:
        events = []
        for line in self.log_path.read_text().splitlines():
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return events

    async def stop(self, kill: bool = False) -> int:
        if self.process.poll() is None:
            self.process.kill() if kill else self.process.terminate()
        return await asyncio.to_thread(self.process.wait, 10)

    async def ready(self, redis: Redis, model: str) -> str:
        async def observed() -> str | None:
            if self.process.poll() is not None:
                raise RuntimeError(f"Worker exited: {self.log_path.read_text()}")
            ready = [e for e in self.events() if e.get("event") == "worker_ready"]
            return ready[0]["worker_id"] if ready else None

        worker_id = await eventually(observed)

        async def registered() -> bool:
            return any(
                h.worker_id == worker_id for h in (await live_workers(redis, model)).values()
            )

        await eventually(registered)
        return str(worker_id)


async def busy(redis: Redis, model: str, worker_id: str, items: int) -> None:
    async def active() -> bool:
        return any(
            h.worker_id == worker_id and h.in_flight >= items and h.busy_ratio > 0
            for h in (await live_workers(redis, model)).values()
        )

    await eventually(active)


def client_for(app: Any, settings: Settings) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://drill",
        timeout=15,
        headers={"X-API-Key": settings.api_keys[0].get_secret_value()},
    )
