from typing import cast

from redis.asyncio import Redis

from infer_platform.common.keys import GROUP, request_stream


class Reclaimer:
    """Transfer idle PEL ownership, then inspect delivery counts (not stream length)."""

    def __init__(self, redis: Redis, model: str, worker_id: str, idle_ms: int) -> None:
        self.redis, self.stream = redis, request_stream(model)
        self.worker_id, self.idle_ms = worker_id, idle_ms
        self.cursor = "0-0"

    async def claim(self, capacity: int) -> list[tuple[str, dict[str, str], int]]:
        if capacity < 1:
            return []
        result = await self.redis.xautoclaim(
            self.stream,
            GROUP,
            self.worker_id,
            self.idle_ms,
            start_id=self.cursor,
            count=capacity,
        )
        self.cursor = str(result[0])
        entries = cast(list[tuple[str, dict[str, str]]], result[1])
        claimed = []
        for entry_id, fields in entries:
            pending = await self.redis.xpending_range(
                self.stream,
                GROUP,
                min=entry_id,
                max=entry_id,
                count=1,
            )
            if pending:
                claimed.append((entry_id, fields, int(pending[0]["times_delivered"])))
        return claimed
