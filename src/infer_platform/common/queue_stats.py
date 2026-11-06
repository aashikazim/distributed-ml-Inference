from dataclasses import dataclass

from redis.asyncio import Redis
from redis.exceptions import ResponseError

from infer_platform.common.keys import GROUP, request_stream
from infer_platform.common.redis import server_ms
from infer_platform.common.registry import live_workers


@dataclass(frozen=True)
class QueueStats:
    backlog: int
    pending: int
    head_age: float
    workers: int
    busy_ratio: float


async def snapshot(redis: Redis, model: str) -> QueueStats:
    """Best-effort live snapshot: head age excludes entries already delivered."""
    key = request_stream(model)
    length = await redis.xlen(key)
    pending, delivered = 0, "0-0"
    try:
        groups = await redis.xinfo_groups(key)
        group = next((g for g in groups if g["name"] == GROUP), None)
        if group:
            pending = int(group["pending"])
            delivered = str(group["last-delivered-id"])
    except ResponseError as exc:
        if "no such key" not in str(exc):
            raise
    head = await redis.xrange(key, min="(" + delivered, max="+", count=1)
    age = (
        max(0, (await server_ms(redis) - int(str(head[0][0]).split("-")[0])) / 1000)
        if head
        else 0.0
    )
    workers = await live_workers(redis, model)
    return QueueStats(
        max(0, length - pending),
        pending,
        age,
        len(workers),
        sum(w.busy_ratio for w in workers.values()) / len(workers) if workers else 0,
    )
