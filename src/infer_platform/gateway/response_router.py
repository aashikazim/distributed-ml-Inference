import asyncio
import contextlib
from typing import cast

import structlog
from pydantic import ValidationError
from redis.asyncio import Redis
from redis.exceptions import RedisError

from infer_platform.common.schemas import InboxMessage

log = structlog.get_logger()


class ResponseRouter:
    """One reader per gateway; register before enqueue to avoid fast-response races."""

    def __init__(self, redis: Redis, inbox: str, ttl: int) -> None:
        self.redis = redis
        self.inbox = inbox
        self.ttl = ttl
        self.waiters: dict[str, asyncio.Queue[InboxMessage]] = {}
        self.task: asyncio.Task[None] | None = None
        self.connected = False

    async def start(self) -> None:
        # Establish a concrete cursor before accepting traffic, including on an empty inbox.
        await self.redis.xadd(self.inbox, {"type": "init"}, maxlen=10000, approximate=True)
        entries = cast(
            list[tuple[str, dict[str, str]]], await self.redis.xrevrange(self.inbox, count=1)
        )
        self.task = asyncio.create_task(self.read(entries[0][0]))
        self.connected = True

    def register(self, request_id: str) -> asyncio.Queue[InboxMessage]:
        queue: asyncio.Queue[InboxMessage] = asyncio.Queue(maxsize=1)
        self.waiters[request_id] = queue
        return queue

    async def read(self, cursor: str) -> None:
        while True:
            try:
                await self.redis.expire(self.inbox, self.ttl)
                streams = cast(
                    list[tuple[str, list[tuple[str, dict[str, str]]]]],
                    await self.redis.xread({self.inbox: cursor}, block=1000, count=100),
                )
                self.connected = True
                for _, entries in streams:
                    for entry_id, fields in entries:
                        cursor = entry_id
                        try:
                            message = InboxMessage.model_validate(fields)
                        except ValidationError:
                            log.warning("invalid_inbox_message", component="gateway")
                            continue
                        queue = self.waiters.get(message.request_id)
                        if queue is not None and queue.empty():
                            queue.put_nowait(message)
            except RedisError:
                self.connected = False
                log.warning("inbox_disconnected", component="gateway")
                await asyncio.sleep(0.2)

    async def close(self) -> None:
        if self.task:
            self.task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.task
        await self.redis.delete(self.inbox)
