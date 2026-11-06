import asyncio

from redis.asyncio import Redis

from infer_platform.common.clock import Clock
from infer_platform.common.keys import worker_key
from infer_platform.common.schemas import Heartbeat
from infer_platform.worker.batcher import DynamicBatcher


class Heartbeats:
    def __init__(
        self,
        redis: Redis,
        model: str,
        worker_id: str,
        backend: str,
        device: str,
        started_at: float,
        batcher: DynamicBatcher,
        clock: Clock,
        stop: asyncio.Event,
        interval: float = 3,
        ttl: int = 10,
    ) -> None:
        self.redis, self.key = redis, worker_key(model, worker_id)
        self.worker_id, self.backend, self.device = worker_id, backend, device
        self.started_at, self.batcher, self.clock = started_at, batcher, clock
        self.stop, self.interval, self.ttl = stop, interval, ttl
        self.previous_time = clock.monotonic()
        self.previous_busy = 0.0

    async def write(self) -> None:
        now = self.clock.monotonic()
        busy = self.batcher.busy_seconds
        if self.batcher.busy_started is not None:
            busy += now - self.batcher.busy_started
        elapsed = now - self.previous_time
        ratio = min(1.0, max(0.0, (busy - self.previous_busy) / elapsed)) if elapsed > 0 else 0
        heartbeat = Heartbeat(
            worker_id=self.worker_id,
            backend=self.backend,
            device=self.device,
            started_at=self.started_at,
            in_flight=self.batcher.outstanding,
            busy_ratio=ratio,
            accepting=not self.stop.is_set(),
        )
        await self.redis.set(self.key, heartbeat.model_dump_json(), ex=self.ttl)
        self.previous_time, self.previous_busy = now, busy

    async def run(self) -> None:
        while True:
            await self.clock.sleep(self.interval)
            await self.write()
