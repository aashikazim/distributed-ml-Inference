from redis.asyncio import BlockingConnectionPool, Redis

from infer_platform.common.settings import Settings


def create_redis(settings: Settings) -> Redis:
    pool = BlockingConnectionPool.from_url(
        settings.redis_url.get_secret_value(),
        decode_responses=True,
        max_connections=settings.redis_max_connections,
        timeout=settings.redis_timeout_s,
        socket_timeout=settings.redis_timeout_s,
        socket_connect_timeout=settings.redis_timeout_s,
    )
    return Redis.from_pool(pool)


async def server_ms(redis: Redis) -> float:
    seconds, micros = await redis.time()
    return seconds * 1000 + micros / 1000
