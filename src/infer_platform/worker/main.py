import asyncio
import json
import signal
import socket
from concurrent.futures import ThreadPoolExecutor
from typing import Literal, cast
from uuid import uuid4

import structlog
from redis.asyncio import Redis
from redis.exceptions import ResponseError

from infer_platform.common.clock import Clock, SystemClock
from infer_platform.common.keys import GROUP, request_stream
from infer_platform.common.logging import configure_logging
from infer_platform.common.redis import create_redis, server_ms
from infer_platform.common.schemas import InboxMessage, InferInput, Meta, RequestMessage
from infer_platform.common.settings import ModelConfig, Settings
from infer_platform.worker.runners.fake import FakeRunner

log = structlog.get_logger()


class Worker:
    def __init__(
        self, redis: Redis, model: str, config: ModelConfig, clock: Clock | None = None
    ) -> None:
        self.redis = redis
        self.model = model
        self.config = config
        self.clock = clock or SystemClock()
        self.worker_id = f"{socket.gethostname()}-{uuid4().hex[:8]}"
        self.runner = FakeRunner(config.fake)
        self.stop = asyncio.Event()

    async def setup(self) -> None:
        try:
            await self.redis.xgroup_create(request_stream(self.model), GROUP, id="0", mkstream=True)
        except ResponseError as exc:
            if "BUSYGROUP" not in str(exc):
                raise

    async def run(self) -> None:
        await self.setup()
        # Dedicated single thread: no event-loop blocking, no unbounded executor queue.
        with ThreadPoolExecutor(max_workers=1, thread_name_prefix="inference") as executor:
            while not self.stop.is_set():
                streams = cast(
                    list[tuple[str, list[tuple[str, dict[str, str]]]]],
                    await self.redis.xreadgroup(
                        GROUP,
                        self.worker_id,
                        {request_stream(self.model): ">"},
                        count=1,
                        block=1000,
                    ),
                )
                for _, entries in streams:
                    for entry_id, fields in entries:
                        await self.process(entry_id, fields, executor)

    async def process(
        self, entry_id: str, fields: dict[str, str], executor: ThreadPoolExecutor
    ) -> None:
        message = RequestMessage.model_validate(fields)
        queue_ms = max(0, await server_ms(self.redis) - int(entry_id.split("-")[0]))
        meta = Meta(worker_id=self.worker_id, queue_ms=queue_ms)
        kind: Literal["final", "error"] = "final"
        try:
            if queue_ms >= message.timeout_ms:
                kind = "error"
                output = {"code": "EXPIRED", "message": "Request expired in queue"}
            else:
                body = InferInput.model_validate_json(message.payload)
                started = self.clock.monotonic()
                result = await asyncio.get_running_loop().run_in_executor(
                    executor,
                    self.runner.predict_batch,
                    [body.model_dump()],
                )
                meta.inference_ms = (self.clock.monotonic() - started) * 1000
                output = result[0]
        except (ValueError, TypeError):
            kind = "error"
            output = {"code": "INVALID_INPUT", "message": "Invalid worker input"}
        response = InboxMessage(
            request_id=message.request_id,
            type=kind,
            data=json.dumps(output),
            meta=meta.model_dump_json(),
        )
        # Write response before ACK. A crash between these operations may duplicate inference.
        await self.redis.xadd(
            message.reply_to,
            {key: str(value) for key, value in response.model_dump().items()},
            maxlen=10000,
            approximate=True,
        )
        await self.redis.expire(message.reply_to, 120)
        await self.redis.xack(request_stream(self.model), GROUP, entry_id)
        await self.redis.xdel(request_stream(self.model), entry_id)
        log.info(
            "request_finished",
            component="worker",
            request_id=message.request_id,
            trace_id=message.trace_id,
            worker_id=self.worker_id,
            model=self.model,
        )


async def main() -> None:
    configure_logging()
    settings = Settings()
    redis = create_redis(settings)
    worker = Worker(redis, settings.worker_model, settings.load_models()[settings.worker_model])
    for sig in (signal.SIGTERM, signal.SIGINT):
        asyncio.get_running_loop().add_signal_handler(sig, worker.stop.set)
    try:
        await worker.run()
    finally:
        await redis.aclose()


if __name__ == "__main__":
    asyncio.run(main())
