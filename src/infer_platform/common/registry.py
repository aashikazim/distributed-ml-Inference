from pydantic import ValidationError
from redis.asyncio import Redis

from infer_platform.common.schemas import Heartbeat


async def live_workers(redis: Redis, model: str) -> dict[str, Heartbeat]:
    """SCAN discovers candidates; GET discards expired or malformed registrations."""
    workers: dict[str, Heartbeat] = {}
    async for key in redis.scan_iter(match=f"worker:{model}:*", count=100):
        value = await redis.get(key)
        if value:
            try:
                heartbeat = Heartbeat.model_validate_json(value)
            except ValidationError:
                continue
            if heartbeat.accepting:
                workers[str(key)] = heartbeat
    return workers
