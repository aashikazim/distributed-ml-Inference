import asyncio
import json
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import httpx
import pytest

from infer_platform.common.keys import GROUP, request_stream
from infer_platform.common.redis import create_redis
from infer_platform.common.sse import sse_data
from infer_platform.gateway.app import create_app
from infer_platform.worker.llm_worker import LLMWorker
from tools.llm_lab import LocalServer, llm_settings
from tools.reliability import client_for, eventually

pytestmark = pytest.mark.integration


@asynccontextmanager
async def lab(
    redis_url, root, env=None, concurrent=2, coalesce=20, deadline=3000
) -> AsyncGenerator[tuple]:
    server = await asyncio.to_thread(
        LocalServer, root, "infer_platform.fake.openai_server:create_app", env
    )
    worker = None
    task = None
    redis = None
    try:
        await server.ready()
        settings = llm_settings(root, redis_url, server.url, concurrent, coalesce, deadline)
        redis = create_redis(settings)
        model = settings.worker_model
        worker = LLMWorker(redis, model, settings.load_models()[model], settings)
        task = asyncio.create_task(worker.run())
        await asyncio.wait_for(worker.ready.wait(), 5)
        app = create_app(settings)
        async with app.router.lifespan_context(app):
            async with client_for(app, settings) as client:
                yield server, settings, redis, worker, app, client
    finally:
        if worker:
            worker.stop.set()
        if task:
            await asyncio.wait_for(task, 10)
        if redis:
            await redis.delete(request_stream(settings.worker_model))
            await redis.aclose()
        await server.close()


def body(model, stream=True, tokens=12):
    return {
        "model": model,
        "messages": [{"role": "user", "content": "synthetic test"}],
        "max_tokens": tokens,
        "temperature": 0.0,
        "stream": stream,
    }


async def test_stream_order_completeness_coalescing_and_metrics(redis_url, tmp_path):
    async with lab(redis_url, tmp_path) as (server, cfg, redis, worker, app, client):
        response = await client.post("/v1/chat/completions", json=body(cfg.worker_model))
        assert response.status_code == 200, response.text
        assert response.headers["content-type"].startswith("text/event-stream")
        frames = [data async for data in sse_data(response)]
        assert frames[-1] == "[DONE]"
        chunks = [json.loads(data) for data in frames[:-1]]
        content = "".join(
            c["delta"].get("content", "") for chunk in chunks for c in chunk["choices"]
        )
        assert content == "".join(f"token{i} " for i in range(12))
        finishes = [
            c["finish_reason"]
            for chunk in chunks
            for c in chunk["choices"]
            if c.get("finish_reason")
        ]
        assert finishes == ["length"]
        assert chunks[-1]["usage"]["completion_tokens"] == 12
        meta = chunks[-1]["meta"]
        assert 0 < meta["chunk_writes"] < 14
        assert meta["ttft_ms"] > 0
        assert meta["tpot_ms"] > 0

        async def completed():
            return (await redis.xpending(request_stream(cfg.worker_model), GROUP))["pending"] == 0

        await eventually(completed)
        assert not app.state.router.waiters
        metrics = {
            sample.name for metric in worker.metrics.registry.collect() for sample in metric.samples
        }
        assert "worker_ttft_seconds_count" in metrics
        assert "worker_tokens_generated_total" in metrics
        assert "gateway_ttft_seconds_count" in (await client.get("/metrics")).text
        assert (await server.stats())["completed"] == 1


async def test_nonstream_chat_and_token_cap(redis_url, tmp_path):
    async with lab(redis_url, tmp_path, coalesce=0) as (server, cfg, _, _, _, client):
        invalid = await client.post("/v1/chat/completions", json=body(cfg.worker_model, tokens=65))
        assert invalid.status_code == 422
        assert (await server.stats())["calls"] == 0
        response = await client.post("/v1/chat/completions", json=body(cfg.worker_model, False, 5))
        assert response.status_code == 200, response.text
        assert response.json()["object"] == "chat.completion"
        assert response.json()["choices"][0]["message"]["content"] == "".join(
            f"token{i} " for i in range(5)
        )
        assert response.json()["meta"]["chunk_writes"] == 0
        assert (await server.stats())["last_max_tokens"] == 5
        assert (
            await client.post(f"/v1/infer/{cfg.worker_model}", json={"input": "invalid endpoint"})
        ).status_code == 400


async def test_concurrency_leaves_excess_requests_in_redis(redis_url, tmp_path):
    async with lab(redis_url, tmp_path, concurrent=2) as (server, cfg, redis, worker, _, client):
        requests = [
            asyncio.create_task(
                client.post("/v1/chat/completions", json=body(cfg.worker_model, False, 24))
            )
            for _ in range(8)
        ]

        async def capacity_reached():
            stats = await server.stats()
            return stats["active"] == 2

        await eventually(capacity_reached)
        assert worker.outstanding == 2
        assert (await redis.xpending(request_stream(cfg.worker_model), GROUP))["pending"] <= 2
        assert await redis.xlen(request_stream(cfg.worker_model)) > 2
        responses = await asyncio.gather(*requests)
        assert [r.status_code for r in responses] == [200] * 8
        assert (await server.stats())["max_active"] == 2


@pytest.mark.parametrize(
    "env,expected",
    [
        ({"INFER_FAKE_ERROR_STATUS": "503"}, 503),
        ({"INFER_FAKE_ERROR_STATUS": "400"}, 422),
        ({"INFER_FAKE_STALL_AFTER": "0"}, 504),
    ],
)
async def test_upstream_failure_maps_before_headers(redis_url, tmp_path, env, expected):
    # A long coalescing window retains the initial role so the stall fails preflight.
    async with lab(redis_url, tmp_path, env, coalesce=1000, deadline=500) as (
        _,
        cfg,
        _,
        _,
        _,
        client,
    ):
        response = await client.post("/v1/chat/completions", json=body(cfg.worker_model))
        assert response.status_code == expected, response.text
        assert "error" in response.json()


async def test_midstream_error_is_sse_error_then_done(redis_url, tmp_path):
    async with lab(redis_url, tmp_path, {"INFER_FAKE_FAIL_AFTER": "3"}, coalesce=0) as (
        _,
        cfg,
        _,
        _,
        _,
        client,
    ):
        response = await client.post("/v1/chat/completions", json=body(cfg.worker_model))
        frames = [data async for data in sse_data(response)]
        assert response.status_code == 200
        assert frames[-1] == "[DONE]"
        assert json.loads(frames[-2])["error"]["code"] == "UPSTREAM_PROTOCOL"


@pytest.mark.parametrize("stall", [False, True])
async def test_real_tcp_client_disconnect_closes_upstream(redis_url, tmp_path, stall):
    async with lab(
        redis_url,
        tmp_path,
        {"INFER_FAKE_TOKEN_MS": "20", **({"INFER_FAKE_STALL_AFTER": "1"} if stall else {})},
    ) as (
        server,
        cfg,
        redis,
        _,
        app,
        _,
    ):
        env = {
            "INFER_REDIS_URL": cfg.redis_url.get_secret_value(),
            "INFER_MODELS_PATH": str(cfg.models_path),
            "INFER_API_KEYS": json.dumps([cfg.api_keys[0].get_secret_value()]),
        }
        gateway = await asyncio.to_thread(
            LocalServer, tmp_path, "infer_platform.gateway.app:create_app", env
        )
        try:
            await gateway.ready("/readyz")
            async with httpx.AsyncClient(timeout=5) as client:
                async with client.stream(
                    "POST",
                    gateway.url + "/v1/chat/completions",
                    json=body(cfg.worker_model, tokens=64),
                    headers={"X-API-Key": cfg.api_keys[0].get_secret_value()},
                ) as r:
                    assert r.status_code == 200
                    rid = r.headers["x-request-id"]
                    async for data in sse_data(r):
                        if any(c["delta"].get("content") for c in json.loads(data)["choices"]):
                            break

            async def aborted():
                stats = await server.stats()
                return stats["active"] == 0 and stats["cancelled"] == 1

            await eventually(aborted)
            assert await redis.exists(f"cancel:{rid}")
            assert not app.state.router.waiters
        finally:
            await gateway.close()


async def test_stream_queue_overflow_is_local_and_cancels(redis_url, tmp_path):
    from infer_platform.common.schemas import InboxMessage
    from infer_platform.gateway.response_router import ResponseRouter

    settings = llm_settings(tmp_path, redis_url, "http://unused")
    redis = create_redis(settings)
    router = ResponseRouter(redis, "inbox:overflow-" + settings.worker_model, 120)
    await router.start()
    try:
        slow = router.register("slow", capacity=2, streaming=True)
        fast = router.register("fast")
        for _ in range(3):
            chunk = InboxMessage(request_id="slow", type="chunk", data='{"chunks":[]}', meta="{}")
            await redis.xadd(router.inbox, chunk.fields())
        final = InboxMessage(request_id="fast", type="final", data="{}", meta="{}")
        await redis.xadd(router.inbox, final.fields())

        async def cancelled():
            return await redis.exists("cancel:slow")

        await eventually(cancelled)
        assert json.loads((await slow.get()).data)["code"] == "SLOW_CONSUMER"
        assert (await asyncio.wait_for(fast.get(), 5)).request_id == "fast"
    finally:
        await router.close()
        await redis.delete("cancel:slow")
        await redis.aclose()


async def test_missing_chunk_fails_instead_of_silent_partial_success(redis_url, tmp_path):
    from unittest.mock import patch

    async with lab(redis_url, tmp_path, coalesce=0) as (_, cfg, redis, _, _, client):
        original = redis.xadd

        async def drop_one(name, fields, **kwargs):
            if fields.get("type") == "chunk":
                chunks = json.loads(fields["data"])["chunks"]
                if any(
                    c["delta"].get("content") == "token3 " for c in chunks for c in c["choices"]
                ):
                    return "0-0"  # Simulate a missing inbox entry after transport/storage loss.
            return await original(name, fields, **kwargs)

        with patch.object(redis, "xadd", side_effect=drop_one):
            response = await client.post("/v1/chat/completions", json=body(cfg.worker_model))
        frames = [data async for data in sse_data(response)]
        assert frames[-1] == "[DONE]"
        assert json.loads(frames[-2])["error"]["code"] == "STREAM_INTERRUPTED"
        rid = response.headers["x-request-id"]

        async def persisted():
            response = await client.get(f"/v1/results/{rid}")
            return response if response.status_code == 200 else None

        cached = await eventually(persisted)
        assert "token3 " in cached.json()["choices"][0]["message"]["content"]


async def test_reclaimed_partial_stream_is_not_regenerated(redis_url, tmp_path):
    from infer_platform.common.schemas import RequestMessage, ResultRecord
    from infer_platform.worker.llm_adapter import LLMAdapter

    server = await asyncio.to_thread(
        LocalServer, tmp_path, "infer_platform.fake.openai_server:create_app"
    )
    await server.ready()
    cfg = llm_settings(tmp_path, redis_url, server.url)
    redis = create_redis(cfg)
    worker = LLMWorker(redis, cfg.worker_model, cfg.load_models()[cfg.worker_model], cfg)
    await worker.setup()
    rid = "partial-" + cfg.worker_model
    reply = "inbox:" + rid
    try:
        message = RequestMessage(
            request_id=rid,
            reply_to=reply,
            kind="chat",
            payload=json.dumps(body(cfg.worker_model)),
            timeout_ms=3000,
            stream="1",
            key_id="hash",
            trace_id="trace",
        )
        entry = await redis.xadd(request_stream(cfg.worker_model), message.fields())
        await redis.xreadgroup(
            GROUP, "dead-consumer", {request_stream(cfg.worker_model): ">"}, count=1
        )
        await redis.set(
            "result:" + rid,
            ResultRecord(
                status="RUNNING",
                key_id="hash",
                deadline_ms=9999999999999,
                stream_started=True,
            ).model_dump_json(),
            ex=300,
        )
        await redis.xclaim(request_stream(cfg.worker_model), GROUP, worker.worker_id, 0, [entry])
        async with httpx.AsyncClient(timeout=3) as client:
            await worker.schedule(LLMAdapter(client, worker.llm), entry, message.fields(), 2)
        cached = json.loads(await redis.get("result:" + rid))
        assert json.loads(cached["response"]["data"])["code"] == "STREAM_INTERRUPTED"
        assert (await server.stats())["calls"] == 0
        assert (await redis.xpending(request_stream(cfg.worker_model), GROUP))["pending"] == 0
    finally:
        await redis.delete(request_stream(cfg.worker_model), reply, "result:" + rid)
        await redis.aclose()
        await server.close()
