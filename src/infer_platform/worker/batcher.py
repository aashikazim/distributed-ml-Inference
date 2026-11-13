import asyncio
from collections.abc import Awaitable, Callable
from concurrent.futures import Executor
from dataclasses import dataclass
from typing import Any, Literal

from infer_platform.common.clock import Clock, SystemClock
from infer_platform.worker.runners.base import ModelRunner


@dataclass
class BatchItem:
    request_id: str
    payload: str
    enqueued_at: float
    context: Any = None
    # Runner-defined cost (e.g. estimated tokens); only used when max_batch_cost is set.
    cost: float = 1.0


CostMode = Literal["padded", "sum"]


def batch_cost(costs: list[float], mode: CostMode) -> float:
    """Padded tensors cost n x longest; ragged/sequential work costs the sum."""
    if not costs:
        return 0.0
    return len(costs) * max(costs) if mode == "padded" else sum(costs)


@dataclass
class BatchResult:
    output: dict[str, Any] | None = None
    error_code: str | None = None
    batch_size: int = 0
    batch_wait_ms: float = 0
    inference_ms: float = 0


class ItemRejected(Exception):
    def __init__(self, code: str) -> None:
        self.code = code


Prepare = Callable[[BatchItem], Awaitable[dict[str, Any]]]
Finish = Callable[[BatchItem, BatchResult], Awaitable[None]]


class DynamicBatcher:
    """Collect the next batch while compute runs; an elapsed timer never closes seats.

    Total outstanding items (active + staged + queued) are bounded by prefetch_bound.
    At most one predict call is active; only one additional batch is staged.
    """

    def __init__(
        self,
        runner: ModelRunner,
        executor: Executor,
        prepare: Prepare,
        finish: Finish,
        max_batch_size: int,
        max_wait_ms: float,
        prefetch_bound: int | None = None,
        clock: Clock | None = None,
        observe: Callable[[int, float], None] | None = None,
        max_batch_cost: float | None = None,
        cost_mode: CostMode = "padded",
    ) -> None:
        bound = prefetch_bound if prefetch_bound is not None else 2 * max_batch_size
        if max_batch_size < 1 or max_wait_ms < 0 or bound < max_batch_size:
            raise ValueError("Invalid batch limits")
        self.runner, self.executor = runner, executor
        self.prepare, self.finish = prepare, finish
        self.max_batch_size, self.max_wait_ms = max_batch_size, max_wait_ms
        self.prefetch_bound = bound
        self.clock = clock or SystemClock()
        self.queue: asyncio.Queue[BatchItem] = asyncio.Queue(maxsize=bound)
        self.slots = asyncio.Semaphore(bound)
        self.outstanding = 0
        self.changed = asyncio.Event()
        self.capacity_changed = asyncio.Event()
        self.busy_seconds = 0.0
        self.busy_started: float | None = None
        self.observe = observe
        self.closed = False
        self.deliveries: set[asyncio.Task[None]] = set()
        self.max_batch_cost, self.cost_mode = max_batch_cost, cost_mode
        # An item that did not fit the current batch's cost budget leads the next one.
        self.carry: BatchItem | None = None

    @property
    def free_capacity(self) -> int:
        return self.prefetch_bound - self.outstanding

    async def wait_capacity(self) -> None:
        while self.free_capacity == 0:
            await self.capacity_changed.wait()
            self.capacity_changed.clear()

    async def submit(self, item: BatchItem) -> None:
        await self.slots.acquire()
        if self.closed:
            self.slots.release()
            raise RuntimeError("Batcher is closed")
        self.outstanding += 1
        self.queue.put_nowait(item)
        self.changed.set()

    def close(self) -> None:
        self.closed = True
        self.changed.set()

    async def run(self) -> None:
        staged: list[BatchItem] = []
        running: asyncio.Task[None] | None = None
        try:
            while True:
                self.changed.clear()
                for delivery in list(self.deliveries):
                    if delivery.done():
                        await delivery
                        self.deliveries.remove(delivery)
                if running is not None and running.done():
                    await running  # Propagate reply/transport errors; never ACK unseen results.
                    running = None
                cost_full = self.stage(staged)
                if not staged and running is None and not self.deliveries and self.closed:
                    return
                deadline = staged[0].enqueued_at + self.max_wait_ms / 1000 if staged else None
                if (
                    staged
                    and running is None
                    and (
                        len(staged) == self.max_batch_size
                        or cost_full
                        or self.closed
                        or (deadline is not None and self.clock.monotonic() >= deadline)
                    )
                ):
                    batch, staged = staged, []
                    running = asyncio.create_task(self.execute(batch))
                    continue
                # No timer while compute is busy: keep filling until the runner is free.
                event = asyncio.create_task(self.changed.wait())
                owned: list[asyncio.Task[Any]] = [event]
                if running is None and deadline is not None:
                    owned.append(
                        asyncio.create_task(
                            self.clock.sleep(max(0, deadline - self.clock.monotonic()))
                        )
                    )
                try:
                    await asyncio.wait(
                        [*owned, *self.deliveries, *([running] if running is not None else [])],
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                finally:
                    for task in owned:
                        task.cancel()
                    await asyncio.gather(*owned, return_exceptions=True)
        finally:
            unfinished = [*self.deliveries, *([running] if running is not None else [])]
            for task in unfinished:
                task.cancel()
            await asyncio.gather(*unfinished, return_exceptions=True)

    def stage(self, staged: list[BatchItem]) -> bool:
        """Fill ``staged`` up to the size and cost limits; return True if cost-full.

        The first item is always admitted, so one oversized item still runs alone
        instead of blocking the queue forever.
        """
        while len(staged) < self.max_batch_size and (
            self.carry is not None or not self.queue.empty()
        ):
            item = self.carry if self.carry is not None else self.queue.get_nowait()
            self.carry = None
            if self.max_batch_cost is not None and staged:
                costs = [*(staged_item.cost for staged_item in staged), item.cost]
                if batch_cost(costs, self.cost_mode) > self.max_batch_cost:
                    self.carry = item
                    return True
            staged.append(item)
        if self.max_batch_cost is None:
            return False
        return batch_cost([item.cost for item in staged], self.cost_mode) >= self.max_batch_cost

    async def predict(self, inputs: list[dict[str, Any]]) -> list[dict[str, Any]]:
        self.busy_started = self.clock.monotonic()
        try:
            outputs = await asyncio.get_running_loop().run_in_executor(
                self.executor,
                self.runner.predict_batch,
                inputs,
            )
        finally:
            elapsed = self.clock.monotonic() - self.busy_started
            self.busy_seconds += elapsed
            if self.observe is not None:
                self.observe(len(inputs), elapsed)
            self.busy_started = None
        if len(outputs) != len(inputs):
            raise ValueError("Runner output count differs from input count")
        return outputs

    async def execute(self, items: list[BatchItem]) -> None:
        valid: list[tuple[BatchItem, dict[str, Any]]] = []
        results: dict[str, BatchResult] = {}
        for item in items:
            try:
                valid.append((item, await self.prepare(item)))
            except ItemRejected as exc:
                results[item.request_id] = BatchResult(
                    error_code=exc.code,
                    batch_wait_ms=(self.clock.monotonic() - item.enqueued_at) * 1000,
                )
        if valid:
            started = self.clock.monotonic()
            try:
                outputs = await self.predict([payload for _, payload in valid])
                elapsed = (self.clock.monotonic() - started) * 1000
                for (item, _), output in zip(valid, outputs, strict=True):
                    results[item.request_id] = BatchResult(
                        output=output,
                        batch_size=len(valid),
                        batch_wait_ms=(started - item.enqueued_at) * 1000,
                        inference_ms=elapsed,
                    )
            except Exception:
                # Retry each item once, isolating poison without recursively retrying forever.
                failed_ms = (self.clock.monotonic() - started) * 1000
                for item, payload in valid:
                    retry_started = self.clock.monotonic()
                    result = BatchResult(
                        batch_size=1,
                        batch_wait_ms=(retry_started - item.enqueued_at) * 1000 - failed_ms,
                    )
                    try:
                        result.output = (await self.predict([payload]))[0]
                    except Exception:
                        result.error_code = "INFERENCE_FAILED"
                    result.inference_ms = (
                        failed_ms + (self.clock.monotonic() - retry_started) * 1000
                    )
                    results[item.request_id] = result
        # Slow reply I/O must not keep an idle model from starting the next batch.
        # Outstanding capacity remains reserved until delivery completes.
        delivery = asyncio.create_task(self.deliver(items, results))
        self.deliveries.add(delivery)

    async def deliver(self, items: list[BatchItem], results: dict[str, BatchResult]) -> None:
        for item in items:
            await self.finish(item, results[item.request_id])
            self.outstanding -= 1
            self.slots.release()
            self.capacity_changed.set()
            self.queue.task_done()
