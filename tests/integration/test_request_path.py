import asyncio
from contextlib import suppress
from uuid import uuid4

import httpx
import pytest
from pydantic import SecretStr

from infer_platform.common.keys import GROUP, request_stream
from infer_platform.common.redis import create_redis
from infer_platform.common.schemas import InboxMessage, RequestMessage
from infer_platform.common.settings import FakeConfig, ModelConfig, Settings
from infer_platform.gateway.app import create_app
from infer_platform.gateway.response_router import ResponseRouter
from infer_platform.worker.batcher import BatchItem, BatchResult
from infer_platform.worker.main import Delivery, Worker

pytestmark = pytest.mark.integration


async def wait_until(check, timeout=5):
    async with asyncio.timeout(timeout):
        while not await check():
            await asyncio.sleep(0.01)


@pytest.fixture
async def settings(redis_url, tmp_path):
    model = "test-" + uuid4().hex
    path = tmp_path / "models.yaml"
    path.write_text(f"models:\n  {model}:\n    backend: fake\n    request_timeout_ms: 200\n")
    return Settings(
        redis_url=redis_url, api_keys=[SecretStr("test-key")], models_path=path, worker_model=model
    )


async def test_roundtrip_errors_cleanup_and_timeout(settings):
    redis = create_redis(settings)
    model = settings.worker_model
    worker = Worker(redis, model, settings.load_models()[model])
    await worker.setup()
    task = asyncio.create_task(worker.run())
    app = create_app(settings)
    try:
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test", timeout=5
            ) as client:
                assert (await client.get("/healthz")).status_code == 200
                assert (await client.get("/readyz")).status_code == 200
                path = f"/v1/infer/{model}"
                assert (await client.post(path, json={"input": "hi"})).status_code == 401
                headers = {"X-API-Key": "test-key"}
                response = await client.post(path, json={"input": "hello"}, headers=headers)
                assert response.status_code == 200, response.text
                result = response.json()
                assert result["output"] == {"label": "SIMULATED", "input_chars": 5}
                assert result["meta"]["worker_id"] == worker.worker_id
                assert result["meta"]["inference_ms"] > 0
                assert result["meta"]["queue_ms"] >= 0

                async def empty():
                    return await redis.xlen(request_stream(model)) == 0

                await wait_until(empty)
                assert (await redis.xpending(request_stream(model), GROUP))["pending"] == 0
                assert not app.state.router.waiters
                assert (await client.get("/v1/models", headers=headers)).status_code == 200
                for body in ({}, {"input": 3}, {"input": ""}, {"input": "x" * 4001}):
                    invalid = await client.post(path, json=body, headers=headers)
                    assert invalid.status_code == 422
                    assert "error" in invalid.json()
                assert (
                    await client.post("/v1/infer/unknown", json={"input": "x"}, headers=headers)
                ).status_code == 404
                assert (
                    await client.post(path + "?async=true", json={"input": "x"}, headers=headers)
                ).status_code == 400
                worker.stop.set()
                await task
                timeout = await client.post(path, json={"input": "hi"}, headers=headers)
                assert timeout.status_code == 504
                assert not app.state.router.waiters
    finally:
        worker.stop.set()
        await task
        await redis.delete(request_stream(model))
        await redis.aclose()


async def test_router_fanout_duplicates_and_malformed_reply(settings):
    redis = create_redis(settings)
    router = ResponseRouter(redis, "inbox:test-" + uuid4().hex, 120)
    await router.start()
    try:
        first, second = router.register("a"), router.register("b")
        for rid in ("b", "unknown", "a", "a"):
            message = InboxMessage(request_id=rid, type="final", data="{}", meta="{}")
            await redis.xadd(router.inbox, message.model_dump())
        assert (await asyncio.wait_for(first.get(), 5)).request_id == "a"
        assert (await asyncio.wait_for(second.get(), 5)).request_id == "b"
        router.waiters.pop("a")
        await redis.xadd(router.inbox, {"bad": "fields"})
        assert not router.task.done()
    finally:
        await router.close()
        await redis.aclose()


async def test_expired_work_skips_runner(settings):
    redis = create_redis(settings)
    model = settings.worker_model
    worker = Worker(
        redis,
        model,
        ModelConfig(backend="fake", fake=FakeConfig(base_ms=0)),
    )
    await worker.setup()
    rid = uuid4().hex
    reply = "inbox:expired-" + rid
    message = RequestMessage(
        request_id=rid,
        reply_to=reply,
        payload='{"input":"x"}',
        timeout_ms=1,
        key_id="hash",
        trace_id=rid,
    )
    # Old stream ID makes expiry deterministic without wall-clock sleeps.
    await redis.xadd(request_stream(model), message.fields(), id="1-0")
    task = asyncio.create_task(worker.run())
    try:

        async def answered():
            return await redis.xlen(reply) > 0

        await wait_until(answered)
        entries = await redis.xrange(reply)
        assert '"EXPIRED"' in entries[0][1]["data"]
    finally:
        worker.stop.set()
        with suppress(asyncio.CancelledError):
            await task
        await redis.delete(request_stream(model), reply)
        await redis.aclose()


async def test_reply_failure_leaves_request_pending(settings):
    from unittest.mock import AsyncMock, patch

    from redis.exceptions import ConnectionError

    redis = create_redis(settings)
    model = settings.worker_model
    worker = Worker(redis, model, settings.load_models()[model])
    await worker.setup()
    message = RequestMessage(
        request_id=uuid4().hex,
        reply_to="inbox:unavailable",
        payload='{"input":"x"}',
        timeout_ms=5000,
        key_id="hash",
        trace_id="trace",
    )
    entry_id = await redis.xadd(request_stream(model), message.fields())
    delivered = await redis.xreadgroup(
        GROUP,
        worker.worker_id,
        {request_stream(model): ">"},
        count=1,
    )
    assert delivered
    try:
        item = BatchItem(message.request_id, message.payload, 0, Delivery(entry_id, message, 0))
        with patch.object(redis, "xadd", AsyncMock(side_effect=ConnectionError("offline"))):
            with pytest.raises(ConnectionError):
                await worker.finish(item, BatchResult(output={"echo": "x"}, batch_size=1))
        assert (await redis.xpending(request_stream(model), GROUP))["pending"] == 1
        assert await redis.xlen(request_stream(model)) == 1
    finally:
        await redis.delete(request_stream(model))
        await redis.aclose()


async def test_concurrent_requests_and_readiness(settings):
    from unittest.mock import AsyncMock, patch

    from redis.exceptions import ConnectionError

    redis = create_redis(settings)
    model = settings.worker_model
    worker = Worker(redis, model, settings.load_models()[model])
    task = asyncio.create_task(worker.run())
    app = create_app(settings)
    try:
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://test",
                timeout=5,
            ) as client:
                responses = await asyncio.gather(
                    *(
                        client.post(
                            f"/v1/infer/{model}",
                            json={"input": "x" * length},
                            headers={"X-API-Key": "test-key"},
                        )
                        for length in range(1, 5)
                    )
                )
                assert all(response.status_code == 200 for response in responses)
                assert [response.json()["output"]["input_chars"] for response in responses] == [
                    1,
                    2,
                    3,
                    4,
                ]
                assert len({response.json()["request_id"] for response in responses}) == 4
                with patch.object(
                    app.state.redis, "ping", AsyncMock(side_effect=ConnectionError("offline"))
                ):
                    assert (await client.get("/healthz")).status_code == 200
                    assert (await client.get("/readyz")).status_code == 503
                app.state.router.task.cancel()
                with suppress(asyncio.CancelledError):
                    await app.state.router.task
                assert (await client.get("/readyz")).status_code == 503
    finally:
        worker.stop.set()
        await task
        await redis.delete(request_stream(model))
        await redis.aclose()
