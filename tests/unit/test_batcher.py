import asyncio
import threading
from collections.abc import AsyncIterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from typing import Any

from infer_platform.common.schemas import InferInput
from infer_platform.worker.batcher import BatchItem, BatchResult, DynamicBatcher, ItemRejected


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleepers: list[tuple[float, asyncio.Future[None]]] = []
        self.armed = asyncio.Event()

    def monotonic(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        future: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        pair = (self.now + seconds, future)
        self.sleepers.append(pair)
        self.armed.set()
        try:
            await future
        finally:
            self.sleepers.remove(pair)

    def advance(self, seconds: float) -> None:
        self.now += seconds
        for deadline, future in list(self.sleepers):
            if deadline <= self.now and not future.done():
                future.set_result(None)


class RecordingRunner:
    def __init__(self, block_first: bool = False) -> None:
        self.loop = asyncio.get_running_loop()
        self.calls: asyncio.Queue[list[str]] = asyncio.Queue()
        self.gate = threading.Event()
        self.block_first = block_first
        self.count = 0

    def load(self) -> None:
        pass

    def warmup(self) -> None:
        pass

    def predict_batch(self, inputs: list[dict[str, Any]]) -> list[dict[str, Any]]:
        self.count += 1
        texts = [item["input"] for item in inputs]
        self.loop.call_soon_threadsafe(self.calls.put_nowait, texts)
        if self.block_first and self.count == 1 and not self.gate.wait(5):
            raise RuntimeError("Test did not release runner")
        if "poison" in texts:
            raise ValueError("poison")
        return [{"echo": text} for text in texts]


class Harness:
    def __init__(self, size: int, wait: float, bound: int | None, block: bool) -> None:
        self.clock = FakeClock()
        self.runner = RecordingRunner(block)
        self.executor = ThreadPoolExecutor(max_workers=1)
        self.results: asyncio.Queue[tuple[str, BatchResult]] = asyncio.Queue()
        self.rejected: dict[str, str] = {}
        self.batcher = DynamicBatcher(
            self.runner,
            self.executor,
            self.prepare,
            self.finish,
            size,
            wait,
            bound,
            self.clock,
        )

    async def prepare(self, item: BatchItem) -> dict[str, Any]:
        if item.request_id in self.rejected:
            raise ItemRejected(self.rejected[item.request_id])
        try:
            return InferInput.model_validate_json(item.payload).model_dump()
        except ValueError as exc:
            raise ItemRejected("INVALID_INPUT") from exc

    async def finish(self, item: BatchItem, result: BatchResult) -> None:
        self.results.put_nowait((item.request_id, result))

    async def add(self, rid: str, text: str | None = None) -> None:
        await self.batcher.submit(
            BatchItem(
                rid,
                InferInput(input=text or rid).model_dump_json(),
                self.clock.monotonic(),
            )
        )


@asynccontextmanager
async def harness(
    size: int = 2, wait: float = 10, bound: int | None = None, block: bool = False
) -> AsyncIterator[Harness]:
    h = Harness(size, wait, bound, block)
    task = asyncio.create_task(h.batcher.run())
    try:
        async with asyncio.timeout(5):
            yield h
    finally:
        h.runner.gate.set()
        h.batcher.close()
        await asyncio.wait_for(task, 5)
        await asyncio.to_thread(h.executor.shutdown, wait=True)


async def test_full_batch_flushes_without_timer_and_preserves_identity() -> None:
    async with harness() as h:
        await h.add("a", "same")
        await h.add("b", "same")
        assert await asyncio.wait_for(h.runner.calls.get(), 5) == ["same", "same"]
        first, second = await h.results.get(), await h.results.get()
        assert [first[0], second[0]] == ["a", "b"]
        assert first[1].batch_size == second[1].batch_size == 2
        assert h.clock.monotonic() == 0
        assert h.batcher.outstanding == 0


async def test_first_item_deadline_and_no_idle_timer() -> None:
    async with harness(wait=10) as h:
        assert not h.clock.sleepers
        await h.add("a")
        await h.clock.armed.wait()
        h.clock.advance(0.009)
        assert h.runner.calls.empty()
        h.clock.advance(0.0011)
        assert await asyncio.wait_for(h.runner.calls.get(), 5) == ["a"]
        _, result = await h.results.get()
        assert result.batch_wait_ms >= 10


async def test_elapsed_deadline_keeps_accepting_items_while_runner_busy() -> None:
    async with harness(size=4, wait=10, block=True) as h:
        await h.add("first")
        await h.clock.armed.wait()
        h.clock.advance(0.011)
        assert await asyncio.wait_for(h.runner.calls.get(), 5) == ["first"]
        await h.add("second")
        h.clock.advance(0.050)
        await h.add("third")
        await h.add("fourth")
        h.runner.gate.set()
        assert await asyncio.wait_for(h.runner.calls.get(), 5) == ["second", "third", "fourth"]
        results = [await h.results.get() for _ in range(4)]
        assert [rid for rid, _ in results] == ["first", "second", "third", "fourth"]
        assert results[1][1].batch_size == 3


async def test_expired_cancelled_and_invalid_items_do_not_reach_runner() -> None:
    async with harness(size=4) as h:
        h.rejected = {"expired": "EXPIRED", "cancelled": "CANCELLED"}
        await h.add("expired")
        await h.add("cancelled")
        await h.batcher.submit(BatchItem("invalid", "{}", 0))
        await h.add("valid")
        assert await asyncio.wait_for(h.runner.calls.get(), 5) == ["valid"]
        results = dict([await h.results.get() for _ in range(4)])
        assert results["expired"].error_code == "EXPIRED"
        assert results["cancelled"].error_code == "CANCELLED"
        assert results["invalid"].error_code == "INVALID_INPUT"
        assert results["valid"].output == {"echo": "valid"}
        assert results["valid"].batch_size == 1


async def test_poison_batch_retries_each_item_once() -> None:
    async with harness(size=3) as h:
        for text in ("good", "poison", "also good"):
            await h.add(text)
        results = dict([await asyncio.wait_for(h.results.get(), 5) for _ in range(3)])
        assert results["good"].output == {"echo": "good"}
        assert results["poison"].error_code == "INFERENCE_FAILED"
        assert results["also good"].output == {"echo": "also good"}
        calls = [await h.runner.calls.get() for _ in range(4)]
        assert calls == [["good", "poison", "also good"], ["good"], ["poison"], ["also good"]]
        assert h.runner.calls.empty()


async def test_bound_counts_running_work_and_blocks_submit() -> None:
    async with harness(size=2, bound=2, block=True) as h:
        await h.add("a")
        await h.add("b")
        await asyncio.wait_for(h.runner.calls.get(), 5)
        assert h.batcher.free_capacity == 0
        blocked = asyncio.create_task(h.add("c"))
        # Inspect scheduling without advancing elapsed time.
        await asyncio.sleep(0)
        assert not blocked.done()
        h.runner.gate.set()
        await asyncio.wait_for(blocked, 5)
        h.batcher.close()
        results = [await asyncio.wait_for(h.results.get(), 5) for _ in range(3)]
        assert {rid for rid, _ in results} == {"a", "b", "c"}
        assert h.batcher.free_capacity == 2


async def test_all_rejected_and_close_empty_do_not_run_empty_batch() -> None:
    async with harness() as h:
        h.rejected = {"a": "CANCELLED", "b": "EXPIRED"}
        await h.add("a")
        await h.add("b")
        await asyncio.wait_for(h.results.get(), 5)
        await h.results.get()
        assert h.runner.calls.empty()
    async with harness() as empty:
        empty.batcher.close()
        assert empty.runner.calls.empty()


async def test_slow_result_delivery_does_not_hold_idle_runner() -> None:
    async with harness(size=2) as h:
        delivering = asyncio.Event()
        release = asyncio.Event()
        original = h.batcher.finish

        async def slow_finish(item: BatchItem, result: BatchResult) -> None:
            if item.request_id == "a":
                delivering.set()
                await release.wait()
            await original(item, result)

        h.batcher.finish = slow_finish
        await h.add("a")
        await h.add("b")
        await asyncio.wait_for(h.runner.calls.get(), 5)
        await delivering.wait()
        await h.add("c")
        await h.add("d")
        try:
            assert await asyncio.wait_for(h.runner.calls.get(), 5) == ["c", "d"]
        finally:
            release.set()
        results = [await h.results.get() for _ in range(4)]
        assert {rid for rid, _ in results} == {"a", "b", "c", "d"}
