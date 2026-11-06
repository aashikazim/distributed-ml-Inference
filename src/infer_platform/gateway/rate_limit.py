import math

from redis.asyncio import Redis

# Server time makes token accounting independent of gateway wall-clock skew.
TOKEN_BUCKET = """
local t = redis.call('TIME')
local now = tonumber(t[1]) + tonumber(t[2]) / 1000000
local rate, burst = tonumber(ARGV[1]), tonumber(ARGV[2])
local old = redis.call('HMGET', KEYS[1], 'tokens', 'updated')
local tokens = tonumber(old[1]) or burst
local updated = tonumber(old[2]) or now
tokens = math.min(burst, tokens + math.max(0, now - updated) * rate)
local allowed, retry_ms = 0, 0
if tokens >= 1 then
    tokens = tokens - 1
    allowed = 1
else
    retry_ms = math.ceil((1 - tokens) / rate * 1000)
end
redis.call('HSET', KEYS[1], 'tokens', tokens, 'updated', now)
redis.call('EXPIRE', KEYS[1], math.max(1, math.ceil(2 * burst / rate)))
return {allowed, retry_ms}
"""


class RateLimiter:
    def __init__(self, redis: Redis, rate: float, burst: int) -> None:
        self.redis, self.rate, self.burst = redis, rate, burst

    async def check(self, key_id: str) -> int:
        result = await self.redis.eval(TOKEN_BUCKET, 1, f"rl:{key_id}", self.rate, self.burst)
        return 0 if result[0] else max(1, math.ceil(result[1] / 1000))
