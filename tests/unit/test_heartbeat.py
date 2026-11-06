import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from infer_platform.worker.heartbeat import Heartbeats


class Clock:
    now = 0.0

    def monotonic(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.now += seconds


async def test_busy_window_includes_active_compute_and_stops_accepting() -> None:
    clock = Clock()
    redis = AsyncMock()
    batcher = SimpleNamespace(busy_seconds=0.0, busy_started=None, outstanding=0)
    stop = asyncio.Event()
    heartbeat = Heartbeats(redis, "fake", "id", "fake", "cpu", 1000, batcher, clock, stop)
    await heartbeat.write()
    batcher.busy_started, batcher.outstanding = 1.0, 3
    clock.now = 2.0
    await heartbeat.write()
    payload = json.loads(redis.set.call_args.args[1])
    assert payload["busy_ratio"] == pytest.approx(0.5)
    assert payload["in_flight"] == 3
    batcher.busy_started, batcher.busy_seconds = None, 2.0
    clock.now = 4.0
    stop.set()
    await heartbeat.write()
    payload = json.loads(redis.set.call_args.args[1])
    assert payload["busy_ratio"] == pytest.approx(0.5)
    assert not payload["accepting"]
