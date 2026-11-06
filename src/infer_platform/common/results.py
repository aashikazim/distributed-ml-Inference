from redis.asyncio import Redis

from infer_platform.common.schemas import ResultRecord

MARK_RUNNING = """
local old = redis.call('GET', KEYS[1])
if old then
    local record = cjson.decode(old)
    if record.response and record.response ~= cjson.null then return old end
end
redis.call('SET', KEYS[1], ARGV[1], 'EX', ARGV[2])
return ''
"""


async def mark_running(
    redis: Redis, key: str, record: ResultRecord, ttl: int
) -> ResultRecord | None:
    existing = await redis.eval(MARK_RUNNING, 1, key, record.model_dump_json(), ttl)
    return ResultRecord.model_validate_json(existing) if existing else None
