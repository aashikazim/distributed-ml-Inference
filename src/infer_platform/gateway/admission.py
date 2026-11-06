from redis.asyncio import Redis

from infer_platform.common.keys import request_stream, result_key
from infer_platform.common.registry import live_workers
from infer_platform.common.schemas import RequestMessage

# Check and append in one operation: concurrent gateways cannot race past the bound.
ENQUEUE = """
if ARGV[1] == '1' then
    local live = false
    for i = 3, #KEYS do
        if redis.call('EXISTS', KEYS[i]) == 1 then live = true; break end
    end
    if not live then return 'NO_WORKERS' end
    if redis.call('XLEN', KEYS[1]) >= tonumber(ARGV[2]) then return 'OVERLOADED' end
end
local t = redis.call('TIME')
local deadline = tonumber(t[1]) * 1000 + tonumber(t[2]) / 1000 + tonumber(ARGV[4])
local state = cjson.encode({status='QUEUED', key_id=ARGV[5], deadline_ms=deadline})
redis.call('SET', KEYS[2], state, 'EX', ARGV[3])
redis.call('XADD', KEYS[1], '*', unpack(ARGV, 6))
return 'ACCEPTED'
"""


async def enqueue(
    redis: Redis,
    model: str,
    message: RequestMessage,
    max_depth: int,
    ttl: int,
    enabled: bool = True,
) -> str:
    workers = await live_workers(redis, model) if enabled else {}
    keys = [request_stream(model), result_key(message.request_id), *workers]
    fields = [part for pair in message.fields().items() for part in pair]
    result = await redis.eval(
        ENQUEUE,
        len(keys),
        *keys,
        "1" if enabled else "0",
        max_depth,
        ttl,
        message.timeout_ms,
        message.key_id,
        *fields,
    )
    return str(result)
