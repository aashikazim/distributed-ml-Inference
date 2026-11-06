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
from infer_platform.common.keys import GROUP, request_stream
from infer_platform.common.logging import configure_logging
from infer_platform.common.redis import create_redis, server_ms
from infer_platform.common.schemas import InboxMessage, InferInput, Meta, RequestMessage
from infer_platform.common.settings import ModelConfig, Settings
from infer_platform.worker.batcher import BatchItem, BatchResult, DynamicBatcher, ItemRejected
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
    ) -> None:
        self.redis, self.model, self.config = redis, model, config
        self.clock = clock or SystemClock()
        self.worker_id = f"{socket.gethostname()}-{uuid4().hex[:8]}"
        if runner is None:
            if config.backend != "fake":
                raise ValueError("Torch worker requires an explicitly configured runner")
            runner = FakeRunner(config.fake)
        self.runner = runner
        self.stop = asyncio.Event()
        self.ready = asyncio.Event()
        self.batcher: DynamicBatcher | None = None

    async def setup(self) -> None:
        try:
            await self.redis.xgroup_create(request_stream(self.model), GROUP, id="0", mkstream=True)
        except ResponseError as exc:
            if "BUSYGROUP" not in str(exc):
                raise

    async def run(self) -> None:
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
            self.ready.set()
            log.info(
                "worker_ready",
                component="worker",
                worker_id=self.worker_id,
                model=self.model,
                backend=self.config.backend,
            )
            async with asyncio.TaskGroup() as tasks:
                tasks.create_task(self.consume(self.batcher))
                tasks.create_task(self.batcher.run())
        finally:
            self.ready.clear()
            # A cancelled executor future cannot interrupt model code. Wait off the event loop.
            await asyncio.to_thread(executor.shutdown, wait=True, cancel_futures=True)

    async def consume(self, batcher: DynamicBatcher) -> None:
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
                streams = cast(
                    list[tuple[str, list[tuple[str, dict[str, str]]]]],
                    await self.redis.xreadgroup(
                        GROUP,
                        self.worker_id,
                        {request_stream(self.model): ">"},
                        count=batcher.free_capacity,
                        block=1000,
                    ),
                )
                if streams:
                    now_ms = await server_ms(self.redis)
                    received_at = self.clock.monotonic()
                    for _, entries in streams:
                        for entry_id, fields in entries:
                            message = RequestMessage.model_validate(fields)
                            delivery = Delivery(
                                entry_id,
                                message,
                                max(0, now_ms - int(entry_id.split("-")[0])),
                            )
                            await batcher.submit(
                                BatchItem(
                                    message.request_id,
                                    message.payload,
                                    received_at,
                                    delivery,
                                )
                            )
        finally:
            batcher.close()

    async def prepare(self, item: BatchItem) -> dict[str, Any]:
        delivery: Delivery = item.context
        age_ms = await server_ms(self.redis) - int(delivery.entry_id.split("-")[0])
        if age_ms >= delivery.message.timeout_ms:
            raise ItemRejected("EXPIRED")
        if await self.redis.exists(f"cancel:{item.request_id}"):
            raise ItemRejected("CANCELLED")
        try:
            body = InferInput.model_validate_json(item.payload)
        except ValidationError as exc:
            raise ItemRejected("INVALID_INPUT") from exc
        if len(body.input) > self.config.max_input_chars:
            raise ItemRejected("INVALID_INPUT")
        return body.model_dump()

    async def finish(self, item: BatchItem, result: BatchResult) -> None:
        delivery: Delivery = item.context
        meta = Meta(
            worker_id=self.worker_id,
            batch_size=result.batch_size,
            queue_ms=delivery.queue_ms,
            batch_wait_ms=result.batch_wait_ms,
            inference_ms=result.inference_ms,
        )
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
        # Reply before ACK. Transport failures leave the request pending for future reclaim.
        await self.redis.xadd(
            delivery.message.reply_to,
            {key: str(value) for key, value in response.model_dump().items()},
            maxlen=10000,
            approximate=True,
        )
        await self.redis.expire(delivery.message.reply_to, 120)
        await self.redis.xack(request_stream(self.model), GROUP, delivery.entry_id)
        await self.redis.xdel(request_stream(self.model), delivery.entry_id)
        log.info(
            "request_finished",
            component="worker",
            request_id=item.request_id,
            trace_id=delivery.message.trace_id,
            worker_id=self.worker_id,
            model=self.model,
            batch_size=result.batch_size,
            outcome=result.error_code or "SUCCEEDED",
        )


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
    worker = Worker(redis, settings.worker_model, config, runner=make_runner(config, settings))
    for sig in (signal.SIGTERM, signal.SIGINT):
        asyncio.get_running_loop().add_signal_handler(sig, worker.stop.set)
    try:
        await worker.run()
    finally:
        await redis.aclose()


if __name__ == "__main__":
    asyncio.run(main())
