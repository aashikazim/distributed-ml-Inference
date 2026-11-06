import asyncio
from typing import cast

import httpx
import structlog
from pydantic import ValidationError
from redis.asyncio import Redis

from infer_platform.common.clock import Clock
from infer_platform.common.keys import GROUP, cancel_key, request_stream, result_key, worker_key
from infer_platform.common.redis import server_ms
from infer_platform.common.results import mark_running
from infer_platform.common.schemas import (
    ChatChunk,
    ChatRequest,
    ChunkBundle,
    InboxMessage,
    LLMMeta,
    RequestMessage,
    ResultRecord,
)
from infer_platform.common.settings import ModelConfig, Settings
from infer_platform.worker.batcher import BatchItem, BatchResult
from infer_platform.worker.heartbeat import Heartbeats
from infer_platform.worker.llm_adapter import LLMAdapter, LLMError
from infer_platform.worker.main import Delivery, Worker
from infer_platform.worker.reclaim import Reclaimer

log = structlog.get_logger()


class LLMWorker(Worker):
    """One semaphore slot per sequence; never feed LLMs through DynamicBatcher."""

    def __init__(
        self,
        redis: Redis,
        model: str,
        config: ModelConfig,
        settings: Settings,
        client: httpx.AsyncClient | None = None,
        clock: Clock | None = None,
    ) -> None:
        super().__init__(redis, model, config, settings=settings, clock=clock)
        assert config.llm is not None
        self.llm = (
            config.llm.model_copy(update={"base_url": settings.llm_base_url})
            if settings.llm_base_url
            else config.llm
        )
        self.client = client
        self.runtime = settings
        self.slots = asyncio.Semaphore(self.llm.max_concurrent)
        self.changed = asyncio.Event()
        self.busy_seconds = 0.0
        self.busy_started: float | None = None
        self.jobs: set[asyncio.Task[None]] = set()

    @property
    def outstanding(self) -> int:
        return len(self.active)

    async def run(self) -> None:
        owns_client = self.client is None
        client = self.client or httpx.AsyncClient(
            timeout=httpx.Timeout(self.llm.http_timeout_s, connect=5, write=5, pool=5),
            limits=httpx.Limits(max_connections=self.llm.max_concurrent),
        )
        try:
            adapter = LLMAdapter(client, self.llm, self.clock)
            await adapter.ready()
            await self.setup()
            heartbeat = Heartbeats(
                self.redis,
                self.model,
                self.worker_id,
                "llm",
                "upstream",
                await server_ms(self.redis),
                self,
                self.clock,
                self.stop,
                self.runtime.heartbeat_interval_s,
                self.runtime.heartbeat_ttl_s,
                self.metrics.busy.labels(self.model).set,
            )
            await heartbeat.write()
            self.ready.set()
            log.info(
                "worker_ready",
                component="worker",
                model=self.model,
                worker_id=self.worker_id,
                backend="llm",
            )
            monitor = asyncio.create_task(heartbeat.run())
            service = asyncio.create_task(self.serve(adapter))
            try:
                done, _ = await asyncio.wait(
                    [monitor, service], return_when=asyncio.FIRST_COMPLETED
                )
                for task in done:
                    await task
            finally:
                for task in (monitor, service, *self.jobs):
                    task.cancel()
                await asyncio.gather(monitor, service, *self.jobs, return_exceptions=True)
        finally:
            self.stop.set()
            self.ready.clear()
            try:
                await self.redis.delete(worker_key(self.model, self.worker_id))
            finally:
                if owns_client:
                    await client.aclose()

    async def serve(self, adapter: LLMAdapter) -> None:
        reclaimer = Reclaimer(self.redis, self.model, self.worker_id, self.config.reclaim_idle_ms)
        next_reclaim = self.clock.monotonic()
        while True:
            self.changed.clear()
            for task in list(self.jobs):
                if task.done():
                    await task
                    self.jobs.remove(task)
            if self.stop.is_set():
                if not self.jobs:
                    return
                await self.changed.wait()
                continue
            capacity = self.llm.max_concurrent - len(self.active)
            if not capacity:
                changed, stopped = (
                    asyncio.create_task(self.changed.wait()),
                    asyncio.create_task(self.stop.wait()),
                )
                try:
                    await asyncio.wait([changed, stopped], return_when=asyncio.FIRST_COMPLETED)
                finally:
                    for waiter in (changed, stopped):
                        waiter.cancel()
                    await asyncio.gather(changed, stopped, return_exceptions=True)
                continue
            if self.clock.monotonic() >= next_reclaim:
                for entry_id, fields, deliveries in await reclaimer.claim(capacity):
                    if entry_id not in self.active:
                        await self.schedule(adapter, entry_id, fields, deliveries)
                next_reclaim = self.clock.monotonic() + self.config.reclaim_interval_ms / 1000
                capacity = self.llm.max_concurrent - len(self.active)
                if not capacity:
                    continue
            streams = cast(
                list[tuple[str, list[tuple[str, dict[str, str]]]]],
                await self.redis.xreadgroup(
                    GROUP,
                    self.worker_id,
                    {request_stream(self.model): ">"},
                    count=capacity,
                    block=100,
                ),
            )
            for _, entries in streams:
                for entry_id, fields in entries:
                    await self.schedule(adapter, entry_id, fields, 1)

    async def schedule(
        self, adapter: LLMAdapter, entry_id: str, fields: dict[str, str], deliveries: int
    ) -> None:
        if deliveries > 1:
            self.metrics.reclaimed.labels(self.model).inc()
        try:
            message = RequestMessage.model_validate(fields)
        except ValidationError:
            if deliveries > self.config.max_deliveries:
                await self.dead_letter(entry_id, fields, deliveries, "INVALID_MESSAGE")
            return
        cached = await self.redis.get(result_key(message.request_id))
        if cached:
            record = ResultRecord.model_validate_json(cached)
            if record.response is not None:
                await self.publish(entry_id, message, record.response, record)
                return
            if record.stream_started and deliveries > 1:
                item = BatchItem(
                    message.request_id,
                    message.payload,
                    self.clock.monotonic(),
                    Delivery(entry_id, message, 0),
                )
                await self.finish(item, BatchResult(error_code="STREAM_INTERRUPTED"))
                return
        if deliveries > self.config.max_deliveries:
            await self.dead_letter(entry_id, fields, deliveries, "MAX_DELIVERIES", message)
            return
        await self.slots.acquire()
        if not self.active:
            self.busy_started = self.clock.monotonic()
        self.active.add(entry_id)
        self.metrics.inflight.labels(self.model).inc()
        received_ms = await server_ms(self.redis)
        item = BatchItem(
            message.request_id,
            message.payload,
            self.clock.monotonic(),
            Delivery(
                entry_id,
                message,
                max(0, received_ms - int(entry_id.split("-")[0])),
            ),
        )
        task = asyncio.create_task(self.handle(adapter, item))
        self.jobs.add(task)

    async def handle(self, adapter: LLMAdapter, item: BatchItem) -> None:
        delivery: Delivery = item.context
        message = delivery.message
        structlog.contextvars.bind_contextvars(
            component="worker",
            model=self.model,
            worker_id=self.worker_id,
            request_id=item.request_id,
            trace_id=message.trace_id,
        )
        started = self.clock.monotonic()
        record = ResultRecord(
            status="RUNNING",
            key_id=message.key_id,
            deadline_ms=int(delivery.entry_id.split("-")[0]) + message.timeout_ms,
        )
        try:
            body = ChatRequest.model_validate_json(message.payload)
            if (
                message.kind != "chat"
                or body.max_tokens > self.llm.max_tokens_cap
                or sum(len(m.content) for m in body.messages) > self.config.max_input_chars
            ):
                raise LLMError("INVALID_INPUT")
            remaining = (record.deadline_ms - await server_ms(self.redis)) / 1000
            if remaining <= 0:
                raise LLMError("EXPIRED")
            if await self.redis.exists(cancel_key(item.request_id)):
                raise LLMError("CANCELLED")
            existing = await mark_running(
                self.redis,
                result_key(item.request_id),
                record,
                self.result_ttl,
            )
            if existing and existing.response:
                await self.publish(delivery.entry_id, message, existing.response, existing)
                return

            async def cancelled() -> bool:
                return bool(await self.redis.exists(cancel_key(item.request_id)))

            async def emit(chunks: list[ChatChunk]) -> None:
                if not record.stream_started:
                    record.stream_started = True
                    await mark_running(
                        self.redis, result_key(item.request_id), record, self.result_ttl
                    )
                chunk = InboxMessage(
                    request_id=item.request_id,
                    type="chunk",
                    data=ChunkBundle(chunks=chunks).model_dump_json(),
                    meta=LLMMeta(
                        worker_id=self.worker_id, queue_ms=delivery.queue_ms
                    ).model_dump_json(),
                )
                await self.redis.xadd(
                    message.reply_to,
                    {key: value for key, value in chunk.fields().items()},
                    maxlen=10000,
                    approximate=True,
                )
                await self.redis.expire(message.reply_to, 120)

            async with asyncio.timeout(remaining):
                completion, stats = await adapter.generate(
                    body,
                    cancelled,
                    emit if message.stream == "1" else None,
                    item.enqueued_at,
                )
            if await cancelled():
                raise LLMError("CANCELLED")
            meta = LLMMeta(
                worker_id=self.worker_id,
                queue_ms=delivery.queue_ms,
                inference_ms=(self.clock.monotonic() - started) * 1000,
                ttft_ms=stats.ttft_ms,
                tpot_ms=stats.tpot_ms,
                completion_tokens=stats.completion_tokens,
                chunk_writes=stats.chunk_writes,
            )
            self.metrics.invocation(self.model, 1, meta.inference_ms / 1000)
            response = InboxMessage(
                request_id=item.request_id,
                type="final",
                data=completion.model_dump_json(),
                meta=meta.model_dump_json(),
            )
            final = record.model_copy(update={"status": "SUCCEEDED", "response": response})
            await self.publish(delivery.entry_id, message, response, final)
            if stats.ttft_ms is not None:
                self.metrics.ttft.labels(self.model).observe(stats.ttft_ms / 1000)
            if stats.tpot_ms is not None:
                self.metrics.tpot.labels(self.model).observe(stats.tpot_ms / 1000)
            if stats.completion_tokens is not None:
                self.metrics.tokens.labels(self.model).inc(stats.completion_tokens)
            log.info(
                "chat_finished",
                component="worker",
                model=self.model,
                worker_id=self.worker_id,
                request_id=item.request_id,
                trace_id=message.trace_id,
                completion_tokens=stats.completion_tokens,
                chunk_writes=stats.chunk_writes,
            )
        except (LLMError, ValidationError, TimeoutError) as exc:
            code = (
                exc.code
                if isinstance(exc, LLMError)
                else "EXPIRED"
                if isinstance(exc, TimeoutError)
                else "INVALID_INPUT"
            )
            await self.finish(item, BatchResult(error_code=code))
        finally:
            self.active.discard(delivery.entry_id)
            self.metrics.inflight.labels(self.model).dec()
            if not self.active and self.busy_started is not None:
                self.busy_seconds += self.clock.monotonic() - self.busy_started
                self.busy_started = None
            self.slots.release()
            self.changed.set()
