import asyncio
import json

import pytest

from infer_platform.common.keys import GROUP, request_stream, worker_key
from infer_platform.common.redis import create_redis
from infer_platform.gateway.app import create_app
from tools.reliability import WorkerProcess, busy, client_for, eventually, settings_for

pytestmark = [pytest.mark.chaos, pytest.mark.integration]


async def test_sigkill_mid_batch_reclaims_all_requests(redis_url, tmp_path):
    settings = settings_for(tmp_path, redis_url, base_ms=500, idle_ms=800)
    model = settings.worker_model
    redis = create_redis(settings)
    victim = await asyncio.to_thread(WorkerProcess, settings, tmp_path)
    survivor = None
    app = create_app(settings)
    try:
        victim_id = await victim.ready(redis, model)
        async with app.router.lifespan_context(app):
            async with client_for(app, settings) as client:
                requests = [
                    asyncio.create_task(
                        client.post(
                            f"/v1/infer/{model}",
                            json={"input": str(index)},
                        )
                    )
                    for index in range(4)
                ]
                await busy(redis, model, victim_id, 4)
                assert (await redis.xpending(request_stream(model), GROUP))["pending"] == 4
                assert await victim.stop(kill=True) == -9
                before = await redis.xpending_range(request_stream(model), GROUP, "-", "+", 4)
                assert {p["consumer"] for p in before} == {victim_id}
                survivor = await asyncio.to_thread(WorkerProcess, settings, tmp_path)
                survivor_id = await survivor.ready(redis, model)
                responses = await asyncio.gather(*requests)
                assert [r.status_code for r in responses] == [200] * 4
                assert {r.json()["meta"]["worker_id"] for r in responses} == {survivor_id}
                assert len({r.json()["request_id"] for r in responses}) == 4
                assert sum(e.get("event") == "request_reclaimed" for e in survivor.events()) == 4

                async def empty():
                    return await redis.xlen(request_stream(model)) == 0

                await eventually(empty)
                assert (await redis.xpending(request_stream(model), GROUP))["pending"] == 0

                async def expired():
                    return not await redis.exists(worker_key(model, victim_id))

                await eventually(expired)
    finally:
        await victim.stop(kill=True)
        if survivor:
            await survivor.stop()
        await redis.delete(request_stream(model))
        await redis.aclose()


async def test_sigterm_finishes_batch_and_removes_heartbeat(redis_url, tmp_path):
    settings = settings_for(tmp_path, redis_url, base_ms=300, idle_ms=800)
    model = settings.worker_model
    redis = create_redis(settings)
    worker = await asyncio.to_thread(WorkerProcess, settings, tmp_path)
    app = create_app(settings)
    try:
        worker_id = await worker.ready(redis, model)
        async with app.router.lifespan_context(app):
            async with client_for(app, settings) as client:
                requests = [
                    asyncio.create_task(
                        client.post(
                            f"/v1/infer/{model}",
                            json={"input": str(index)},
                        )
                    )
                    for index in range(4)
                ]
                await busy(redis, model, worker_id, 4)
                assert await worker.stop() == 0
                responses = await asyncio.gather(*requests)
                assert [r.status_code for r in responses] == [200] * 4
                assert not await redis.exists(worker_key(model, worker_id))
                assert await redis.xlen(request_stream(model)) == 0
                assert (await redis.xpending(request_stream(model), GROUP))["pending"] == 0
                cached = await redis.get("result:" + responses[0].json()["request_id"])
                assert json.loads(cached)["status"] == "SUCCEEDED"
    finally:
        await worker.stop(kill=True)
        await redis.delete(request_stream(model))
        await redis.aclose()
