import asyncio
import json
import signal
import socket
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any, Literal, cast
from uuid import uuid4

import structlog
from pydantic import ValidationError
from redis.asyncio import Redis
from redis.exceptions import ResponseError

from infer_platform.common.clock import Clock, SystemClock
from infer_platform.common.keys import (
    GROUP,
    cancel_key,
    dlq_key,
    request_stream,
    result_key,
    worker_key,
)
from infer_platform.common.logging import configure_logging
from infer_platform.common.redis import create_redis, server_ms
from infer_platform.common.results import mark_running
from infer_platform.common.schemas import (
    InboxMessage,
    InferInput,
    Meta,
    RequestMessage,
    ResultRecord,
)
from infer_platform.common.settings import ModelConfig, Settings
from infer_platform.worker.batcher import BatchItem, BatchResult, DynamicBatcher, ItemRejected
from infer_platform.worker.heartbeat import Heartbeats
from infer_platform.worker.reclaim import Reclaimer
from infer_platform.worker.runners.base import ModelRunner
from infer_platform.worker.runners.fake import FakeRunner

log = structlog.get_logger()


@dataclass
class Delivery:
    entry_id: str
    message: RequestMessage
    queue_ms: float


class Worker:
    def __init__(
        self,
        redis: Redis,
        model: str,
        config: ModelConfig,
        clock: Clock | None = None,
        runner: ModelRunner | None = None,
        settings: Settings | None = None,
    ) -> None:
        self.redis, self.model, self.config = redis, model, config
        self.clock = clock or SystemClock()
        self.worker_id = f"{socket.gethostname()}-{uuid4().hex[:8]}"
        if runner is None:
            if config.backend == "torch":
                raise ValueError("Torch worker requires an explicitly configured runner")
            if config.backend == "fake":
                runner = FakeRunner(config.fake)
        self.runner = runner
        self.stop = asyncio.Event()
        self.ready = asyncio.Event()
        self.batcher: DynamicBatcher | None = None
        self.active: set[str] = set()
        self.settings = settings
        self.result_ttl = settings.result_ttl_s if settings else 300

    async def setup(self) -> None:
        try:
            await self.redis.xgroup_create(request_stream(self.model), GROUP, id="0", mkstream=True)
        except ResponseError as exc:
            if "BUSYGROUP" not in str(exc):
                raise

    async def run(self) -> None:
        assert self.runner is not None, "LLM workers use their adapter lifecycle"
        executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="inference")
        try:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(executor, self.runner.load)
            await loop.run_in_executor(executor, self.runner.warmup)
            await self.setup()
            self.batcher = DynamicBatcher(
                self.runner,
                executor,
                self.prepare,
                self.finish,
                self.config.max_batch_size,
                self.config.max_wait_ms,
                self.config.prefetch_bound,
                self.clock,
            )
            heartbeat = Heartbeats(
                self.redis,
                self.model,
                self.worker_id,
                self.config.backend,
                getattr(self.runner, "device", "cpu"),
                await server_ms(self.redis),
                self.batcher,
                self.clock,
                self.stop,
                self.settings.heartbeat_interval_s if self.settings else 3,
                self.settings.heartbeat_ttl_s if self.settings else 10,
            )
            batcher = self.batcher
            await heartbeat.write()
            self.ready.set()
            log.info(
                "worker_ready",
                component="worker",
                worker_id=self.worker_id,
                model=self.model,
                backend=self.config.backend,
            )
            async with asyncio.TaskGroup() as tasks:
                monitor = tasks.create_task(heartbeat.run())

                async def serve() -> None:
                    try:
                        async with asyncio.TaskGroup() as work:
                            work.create_task(self.consume(batcher))
                            work.create_task(batcher.run())
                    finally:
                        monitor.cancel()

                tasks.create_task(serve())
        finally:
            self.stop.set()
            self.ready.clear()
            try:
                await self.redis.delete(worker_key(self.model, self.worker_id))
            finally:
                # CPU code cannot be interrupted by cancelling its asyncio Future.
                await asyncio.to_thread(executor.shutdown, wait=True, cancel_futures=True)

    async def consume(self, batcher: DynamicBatcher) -> None:
        reclaimer = Reclaimer(self.redis, self.model, self.worker_id, self.config.reclaim_idle_ms)
        next_reclaim = self.clock.monotonic()
        try:
            while not self.stop.is_set():
                if not batcher.free_capacity:
                    capacity = asyncio.create_task(batcher.wait_capacity())
                    stopped = asyncio.create_task(self.stop.wait())
                    try:
                        await asyncio.wait([capacity, stopped], return_when=asyncio.FIRST_COMPLETED)
                    finally:
                        for task in (capacity, stopped):
                            task.cancel()
                        await asyncio.gather(capacity, stopped, return_exceptions=True)
                    continue
                if self.clock.monotonic() >= next_reclaim:
                    for entry_id, fields, deliveries in await reclaimer.claim(
                        batcher.free_capacity
                    ):
                        if entry_id in self.active:
                            continue
                        await self.accept(entry_id, fields, deliveries, batcher)
                    next_reclaim = self.clock.monotonic() + self.config.reclaim_interval_ms / 1000
                    if not batcher.free_capacity:
                        continue
                block = max(1, min(1000, int((next_reclaim - self.clock.monotonic()) * 1000)))
                streams = cast(
                    list[tuple[str, list[tuple[str, dict[str, str]]]]],
                    await self.redis.xreadgroup(
                        GROUP,
                        self.worker_id,
                        {request_stream(self.model): ">"},
                        count=batcher.free_capacity,
                        block=block,
                    ),
                )
                for _, entries in streams:
                    for entry_id, fields in entries:
                        await self.accept(entry_id, fields, 1, batcher)
        finally:
            batcher.close()

    async def accept(
        self, entry_id: str, fields: dict[str, str], deliveries: int, batcher: DynamicBatcher
    ) -> None:
        try:
            message = RequestMessage.model_validate(fields)
        except ValidationError:
            if deliveries > self.config.max_deliveries:
                await self.dead_letter(entry_id, fields, deliveries, "INVALID_MESSAGE")
            return  # Remain pending; malformed envelopes cannot safely address a reply.
        cached = await self.redis.get(result_key(message.request_id))
        if cached:
            record = ResultRecord.model_validate_json(cached)
            if record.response is not None and record.key_id == message.key_id:
                await self.publish(entry_id, message, record.response, record)
                return
        if deliveries > self.config.max_deliveries:
            last_error = ResultRecord.model_validate_json(cached).last_error if cached else None
            await self.dead_letter(
                entry_id,
                fields,
                deliveries,
                last_error or "MAX_DELIVERIES",
                message,
            )
            return
        received = self.clock.monotonic()
        queue_ms = max(0, await server_ms(self.redis) - int(entry_id.split("-")[0]))
        self.active.add(entry_id)
        await batcher.submit(
            BatchItem(
                message.request_id,
                message.payload,
                received,
                Delivery(entry_id, message, queue_ms),
            )
        )
        if deliveries > 1:
            log.info(
                "request_reclaimed",
                component="worker",
                worker_id=self.worker_id,
                model=self.model,
                request_id=message.request_id,
                delivery_count=deliveries,
            )

    async def dead_letter(
        self,
        entry_id: str,
        fields: dict[str, str],
        deliveries: int,
        last_error: str,
        message: RequestMessage | None = None,
    ) -> None:
        await self.redis.xadd(
            dlq_key(self.model),
            {
                "entry_id": entry_id,
                "request_id": fields.get("request_id", ""),
                "last_error": last_error,
                "delivery_count": str(deliveries),
                "message": json.dumps(fields),
            },
            maxlen=10000,
            approximate=True,
        )
        if message is not None:
            item = BatchItem(
                message.request_id,
                message.payload,
                self.clock.monotonic(),
                Delivery(entry_id, message, 0),
            )
            await self.finish(item, BatchResult(error_code="DEAD_LETTERED"))
        else:
            # Salvage only an explicitly addressed envelope; never invent a reply destination.
            rid, reply = fields.get("request_id"), fields.get("reply_to")
            if rid and reply and reply.startswith("inbox:"):
                response = InboxMessage(
                    request_id=rid,
                    type="error",
                    data=json.dumps(
                        {"code": "DEAD_LETTERED", "message": "Invalid stream envelope"}
                    ),
                    meta=Meta(worker_id=self.worker_id, batch_size=0).model_dump_json(),
                )
                record = ResultRecord(
                    status="DEAD_LETTERED",
                    key_id=fields.get("key_id", ""),
                    deadline_ms=0,
                    response=response,
                    last_error=last_error,
                )
                # Publish uses only the routing fields for this quarantined malformed entry.
                await self.redis.xadd(
                    reply,
                    {key: str(value) for key, value in response.model_dump().items()},
                    maxlen=10000,
                    approximate=True,
                )
                await self.redis.expire(reply, 120)
                await self.redis.set(result_key(rid), record.model_dump_json(), ex=self.result_ttl)
            await self.ack_delete(entry_id)
        log.warning(
            "request_dead_lettered",
            component="worker",
            model=self.model,
            worker_id=self.worker_id,
            delivery_count=deliveries,
            reason=last_error,
        )

    async def prepare(self, item: BatchItem) -> dict[str, Any]:
        delivery: Delivery = item.context
        age_ms = await server_ms(self.redis) - int(delivery.entry_id.split("-")[0])
        if age_ms >= delivery.message.timeout_ms:
            raise ItemRejected("EXPIRED")
        if await self.redis.exists(cancel_key(item.request_id)):
            raise ItemRejected("CANCELLED")
        try:
            body = InferInput.model_validate_json(item.payload)
        except ValidationError as exc:
            raise ItemRejected("INVALID_INPUT") from exc
        if len(body.input) > self.config.max_input_chars:
            raise ItemRejected("INVALID_INPUT")
        record = ResultRecord(
            status="RUNNING",
            key_id=delivery.message.key_id,
            deadline_ms=int(delivery.entry_id.split("-")[0]) + delivery.message.timeout_ms,
        )
        existing = await mark_running(
            self.redis, result_key(item.request_id), record, self.result_ttl
        )
        if existing is not None:
            raise ItemRejected("ALREADY_FINISHED")
        return body.model_dump()

    async def finish(self, item: BatchItem, result: BatchResult) -> None:
        delivery: Delivery = item.context
        if result.error_code == "ALREADY_FINISHED":
            cached = await self.redis.get(result_key(item.request_id))
            if cached:
                record = ResultRecord.model_validate_json(cached)
                if record.response is not None:
                    await self.publish(delivery.entry_id, delivery.message, record.response, record)
                    self.active.discard(delivery.entry_id)
                    return
            raise RuntimeError("Cached final outcome expired during replay")
        meta = Meta(
            worker_id=self.worker_id,
            batch_size=result.batch_size,
            queue_ms=delivery.queue_ms,
            batch_wait_ms=result.batch_wait_ms,
            inference_ms=result.inference_ms,
        )
        age_ms = await server_ms(self.redis) - int(delivery.entry_id.split("-")[0])
        if result.error_code != "DEAD_LETTERED":
            if age_ms >= delivery.message.timeout_ms:
                result.error_code, result.output = "EXPIRED", None
            elif await self.redis.exists(cancel_key(item.request_id)):
                result.error_code, result.output = "CANCELLED", None
        if result.error_code == "INFERENCE_FAILED":
            record = ResultRecord(
                status="RUNNING",
                key_id=delivery.message.key_id,
                deadline_ms=int(delivery.entry_id.split("-")[0]) + delivery.message.timeout_ms,
                last_error=result.error_code,
            )
            await mark_running(self.redis, result_key(item.request_id), record, self.result_ttl)
            self.active.discard(delivery.entry_id)
            return  # Keep pending for the reclaim loop, not an unbounded immediate retry.
        kind: Literal["final", "error"] = "error" if result.error_code else "final"
        response = InboxMessage(
            request_id=item.request_id,
            type=kind,
            data=json.dumps(
                {"code": result.error_code, "message": "Request could not complete"}
                if result.error_code
                else result.output
            ),
            meta=meta.model_dump_json(),
        )
        status: Any = (
            result.error_code
            if result.error_code in {"EXPIRED", "CANCELLED", "DEAD_LETTERED"}
            else "FAILED"
            if result.error_code
            else "SUCCEEDED"
        )
        record = ResultRecord(
            status=status,
            key_id=delivery.message.key_id,
            deadline_ms=int(delivery.entry_id.split("-")[0]) + delivery.message.timeout_ms,
            response=response,
            last_error=result.error_code,
        )
        await self.publish(delivery.entry_id, delivery.message, response, record)
        self.active.discard(delivery.entry_id)
        log.info(
            "request_finished",
            component="worker",
            request_id=item.request_id,
            trace_id=delivery.message.trace_id,
            worker_id=self.worker_id,
            model=self.model,
            batch_size=result.batch_size,
            outcome=record.status,
        )

    async def publish(
        self, entry_id: str, message: RequestMessage, response: InboxMessage, record: ResultRecord
    ) -> None:
        # Ordered durable outcome: response -> result cache -> ACK -> delete.
        await self.redis.xadd(
            message.reply_to,
            {key: str(value) for key, value in response.model_dump().items()},
            maxlen=10000,
            approximate=True,
        )
        await self.redis.expire(message.reply_to, 120)
        await self.redis.set(
            result_key(message.request_id), record.model_dump_json(), ex=self.result_ttl
        )
        await self.ack_delete(entry_id)

    async def ack_delete(self, entry_id: str) -> None:
        # Atomic cleanup avoids an ACKed-but-retained entry permanently consuming admission depth.
        async with self.redis.pipeline(transaction=True) as pipe:
            pipe.xack(request_stream(self.model), GROUP, entry_id)
            pipe.xdel(request_stream(self.model), entry_id)
            await pipe.execute()


def make_runner(config: ModelConfig, settings: Settings) -> ModelRunner:
    if config.backend == "fake":
        return FakeRunner(config.fake)
    from infer_platform.worker.runners.torch_classifier import TorchClassifierRunner

    assert config.hf_id is not None
    return TorchClassifierRunner(
        config.hf_id,
        config.revision,
        settings.device,
        settings.torch_threads,
        settings.torch_fp16,
        settings.model_cache,
        settings.model_local_files_only,
    )


async def main() -> None:
    configure_logging()
    settings = Settings()
    redis = create_redis(settings)
    config = settings.load_models()[settings.worker_model]
    worker: Worker
    metrics_registry = None
    if config.backend == "llm":
        from infer_platform.worker.llm_worker import LLMWorker

        llm_worker = LLMWorker(redis, settings.worker_model, config, settings)
        worker = llm_worker
        metrics_registry = llm_worker.metrics.registry
    else:
        worker = Worker(
            redis,
            settings.worker_model,
            config,
            runner=make_runner(config, settings),
            settings=settings,
        )
    for sig in (signal.SIGTERM, signal.SIGINT):
        asyncio.get_running_loop().add_signal_handler(sig, worker.stop.set)
    metrics_server = None
    if metrics_registry is not None:
        from prometheus_client import start_http_server

        metrics_server, _ = await asyncio.to_thread(
            start_http_server,
            settings.worker_metrics_port,
            registry=metrics_registry,
        )
    try:
        await worker.run()
    finally:
        if metrics_server:
            await asyncio.to_thread(metrics_server.shutdown)
            metrics_server.server_close()
        await redis.aclose()


if __name__ == "__main__":
    asyncio.run(main())
