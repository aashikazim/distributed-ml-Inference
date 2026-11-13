from redis.asyncio import Redis

from infer_platform.common.clock import Clock, SystemClock
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


class WorkerDirectory:
    """Per-gateway cache of live worker heartbeat keys, refreshed at most every ``ttl_s``.

    The cache only chooses *which keys* the admission script checks; liveness is still
    decided atomically inside Redis with EXISTS. A stale cache can therefore cause a
    false NO_WORKERS (handled by one forced refresh in ``enqueue``), never a false
    acceptance. This removes a keyspace SCAN from the hot path of every request.
    """

    def __init__(self, redis: Redis, ttl_s: float, clock: Clock | None = None) -> None:
        self.redis, self.ttl_s = redis, ttl_s
        self.clock = clock or SystemClock()
        self.cache: dict[str, tuple[float, list[str]]] = {}

    async def keys(self, model: str, refresh: bool = False) -> tuple[list[str], bool]:
        """Return (heartbeat keys, whether they were freshly scanned)."""
        now = self.clock.monotonic()
        cached = self.cache.get(model)
        if cached is not None and not refresh and now - cached[0] < self.ttl_s:
            return cached[1], False
        workers = list(await live_workers(self.redis, model))
        self.cache[model] = (now, workers)
        return workers, True


async def enqueue(
    redis: Redis,
    model: str,
    message: RequestMessage,
    max_depth: int,
    ttl: int,
    enabled: bool = True,
    directory: WorkerDirectory | None = None,
) -> str:
    async def attempt(workers: list[str]) -> str:
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

    if not enabled:
        return await attempt([])
    if directory is None:
        return await attempt(list(await live_workers(redis, model)))
    workers, fresh = await directory.keys(model)
    outcome = await attempt(workers)
    if outcome == "NO_WORKERS" and not fresh:
        # Cached keys may all have expired while a new worker registered: rescan once.
        workers, _ = await directory.keys(model, refresh=True)
        outcome = await attempt(workers)
    return outcome
