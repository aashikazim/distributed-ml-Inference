import asyncio
from uuid import uuid4

import structlog
from redis.asyncio import Redis

from infer_platform.autoscaler.policy import History, Policy, State, decide
from infer_platform.autoscaler.scalers import Scaler
from infer_platform.common.clock import Clock
from infer_platform.common.metrics import AutoscalerMetrics
from infer_platform.common.queue_stats import snapshot

RENEW = """if redis.call('GET',KEYS[1]) == ARGV[1] then
return redis.call('PEXPIRE',KEYS[1],ARGV[2]) else return 0 end"""
RELEASE = """if redis.call('GET',KEYS[1]) == ARGV[1] then
return redis.call('DEL',KEYS[1]) else return 0 end"""


class Controller:
    def __init__(
        self,
        redis: Redis,
        policies: dict[str, Policy],
        scaler: Scaler,
        clock: Clock,
        interval: float = 5,
        lock_ttl_s: float = 30,
    ) -> None:
        self.redis, self.policies, self.scaler, self.clock = redis, policies, scaler, clock
        self.interval, self.lock_ttl_s = interval, lock_ttl_s
        self.token = uuid4().hex
        self.history: dict[str, History] = {}
        self.metrics = AutoscalerMetrics()
        self.stop = asyncio.Event()

    async def acquire(self) -> bool:
        return bool(
            await self.redis.set(
                "autoscaler:lock", self.token, nx=True, px=int(self.lock_ttl_s * 1000)
            )
        )

    async def renew(self) -> bool:
        return bool(
            await self.redis.eval(
                RENEW, 1, "autoscaler:lock", self.token, int(self.lock_ttl_s * 1000)
            )
        )

    async def cycle(self) -> None:
        for model, policy in self.policies.items():
            signals = await snapshot(self.redis, model)
            current = await self.scaler.current(model, signals.workers)
            state = State(
                current, signals.backlog, signals.busy_ratio, signals.head_age, signals.workers
            )
            decision = decide(
                state, policy, self.history.get(model, History()), self.clock.monotonic()
            )
            # Recheck ownership immediately before acting. A stalled API call may still
            # outlive its lease: Redis locking is not Kubernetes fencing.
            if not await self.renew():
                raise RuntimeError("Autoscaler leadership lost")
            if decision.desired != current:
                await self.scaler.scale(model, decision.desired)
            self.history[model] = decision.history
            direction = (
                "up"
                if decision.desired > current
                else "down"
                if decision.desired < current
                else "hold"
            )
            self.metrics.current.labels(model).set(current)
            self.metrics.desired.labels(model).set(decision.desired)
            self.metrics.decisions.labels(model, direction).inc()
            structlog.get_logger().info(
                "autoscaler_decision",
                component="autoscaler",
                model=model,
                current=current,
                desired=decision.desired,
                reason=decision.reason,
                backlog=signals.backlog,
                head_age_s=signals.head_age,
                workers_alive=signals.workers,
            )

    async def run(self) -> None:
        async def heartbeat() -> None:
            while True:
                await self.clock.sleep(self.lock_ttl_s / 3)
                if not await self.renew():
                    raise RuntimeError("Autoscaler leadership lost")

        try:
            while not self.stop.is_set():
                if not await self.acquire():
                    await self.clock.sleep(self.interval)
                    continue
                # A newly elected controller has no old demand history: avoid an
                # immediate downscale while collecting its stabilization window.
                now = self.clock.monotonic()
                for model in self.policies:
                    state = await snapshot(self.redis, model)
                    count = await self.scaler.current(model, state.workers)
                    self.history[model] = History(((now, count),), now)
                async with asyncio.TaskGroup() as group:
                    lease = group.create_task(heartbeat())
                    try:
                        while not self.stop.is_set():
                            await self.cycle()
                            await self.clock.sleep(self.interval)
                    finally:
                        lease.cancel()
                break
        finally:
            await self.redis.eval(RELEASE, 1, "autoscaler:lock", self.token)
