import asyncio
import json
from contextlib import asynccontextmanager
from typing import Any
from uuid import uuid4

import pytest

from infer_platform.common.keys import GROUP, request_stream, result_key
from infer_platform.common.redis import create_redis
from infer_platform.common.schemas import RequestMessage, ResultRecord
from infer_platform.gateway.app import create_app
from infer_platform.gateway.rate_limit import RateLimiter
from infer_platform.worker.main import Worker
from infer_platform.worker.runners.fake import FakeRunner
from tools.reliability import client_for, eventually, settings_for

pytestmark = pytest.mark.integration


@asynccontextmanager
async def system(settings, runner=None):
    redis = create_redis(settings)
    model = settings.worker_model
    worker = Worker(redis, model, settings.load_models()[model], runner=runner, settings=settings)
    task = asyncio.create_task(worker.run())
    app = create_app(settings)
    try:
        await asyncio.wait_for(worker.ready.wait(), 5)
        async with app.router.lifespan_context(app):
            async with client_for(app, settings) as client:
                yield redis, worker, client, app
    finally:
        worker.stop.set()
        await asyncio.wait_for(task, 10)
        await redis.delete(request_stream(model), f"dlq:{model}")
        await redis.aclose()


async def test_async_poll_owner_and_expired_lookup(redis_url, tmp_path):
    settings = settings_for(tmp_path, redis_url)
    model = settings.worker_model
    async with system(settings) as (redis, _, client, _):
        accepted = await client.post(f"/v1/infer/{model}?async=true", json={"input": "hello"})
        assert accepted.status_code == 202
        rid = accepted.json()["request_id"]
        poll = await client.get(f"/v1/results/{rid}")
        assert poll.status_code == 202
        assert (await client.get("/v1/results/missing")).status_code == 404
        assert (
            await client.get(f"/v1/results/{rid}", headers={"X-API-Key": "bad"})
        ).status_code == 401

        async def complete():
            result = await client.get(f"/v1/results/{rid}")
            return result if result.status_code != 202 else None

        final = await eventually(complete)
        assert final.status_code == 200
        assert final.json()["output"]["input_chars"] == 5
        record = ResultRecord.model_validate_json(await redis.get(result_key(rid)))
        foreign = record.model_copy(update={"key_id": "another-owner"})
        await redis.set(result_key(rid), foreign.model_dump_json(), ex=300)
        assert (await client.get(f"/v1/results/{rid}")).status_code == 404
        await redis.delete(result_key(rid))
        assert (await client.get(f"/v1/results/{rid}")).status_code == 404


async def test_atomic_overload_and_no_workers(redis_url, tmp_path):
    settings = settings_for(tmp_path, redis_url, base_ms=400, idle_ms=1500, depth=2, batch=1)
    model = settings.worker_model
    async with system(settings) as (redis, worker, client, _):
        responses = await asyncio.gather(
            *(client.post(f"/v1/infer/{model}?async=true", json={"input": "x"}) for _ in range(20))
        )
        assert sum(r.status_code == 202 for r in responses) == 2
        rejected = [r for r in responses if r.status_code == 503]
        assert len(rejected) == 18
        assert all(r.headers["Retry-After"] == "1" for r in rejected)
        assert await redis.xlen(request_stream(model)) <= 2
        worker.stop.set()

        async def no_worker():
            return not await redis.exists(f"worker:{model}:{worker.worker_id}")

        await eventually(no_worker)
        rejected = await client.post(f"/v1/infer/{model}", json={"input": "x"})
        assert rejected.status_code == 503
        assert rejected.json()["error"]["code"] == "NO_WORKERS"


async def test_rate_limit_across_gateways_and_redis_refill(redis_url, tmp_path):
    settings = settings_for(tmp_path, redis_url, rate_limit_per_s=0.01, rate_limit_burst=1)
    model = settings.worker_model
    async with system(settings) as (redis, _, client, _):
        accepted = await client.post(f"/v1/infer/{model}?async=true", json={"input": "x"})
        assert accepted.status_code == 202
        other = create_app(settings)
        async with other.router.lifespan_context(other):
            async with client_for(other, settings) as second:
                limited = await second.post(f"/v1/infer/{model}", json={"input": "x"})
                assert limited.status_code == 429
                assert int(limited.headers["Retry-After"]) >= 1
        key = uuid4().hex
        limiter = RateLimiter(redis, 10, 1)
        assert await limiter.check(key) == 0
        assert await limiter.check(key) > 0

        async def refilled():
            return await limiter.check(key) == 0

        await eventually(refilled)


class CountingRunner(FakeRunner):
    def __init__(self, config):
        super().__init__(config)
        self.calls = 0

    def predict_batch(self, inputs: list[dict[str, Any]]) -> list[dict[str, Any]]:
        self.calls += 1
        return super().predict_batch(inputs)


async def test_cached_duplicate_is_replayed_without_compute(redis_url, tmp_path):
    settings = settings_for(tmp_path, redis_url, base_ms=1, batch=1)
    model = settings.worker_model
    runner = CountingRunner(settings.load_models()[model].fake)
    async with system(settings, runner) as (redis, _, client, app):
        response = await client.post(f"/v1/infer/{model}", json={"input": "one"})
        assert response.status_code == 200
        rid = response.json()["request_id"]

        async def cached():
            value = await redis.get(result_key(rid))
            return value if value and json.loads(value).get("response") else None

        record = ResultRecord.model_validate_json(await eventually(cached))
        duplicate = RequestMessage(
            request_id=rid,
            reply_to=app.state.router.inbox,
            timeout_ms=5000,
            payload='{"input":"one"}',
            key_id=record.key_id,
            trace_id="duplicate",
        )
        await redis.xadd(request_stream(model), duplicate.fields())

        async def empty():
            return await redis.xlen(request_stream(model)) == 0

        await eventually(empty)
        assert runner.calls == 1
        assert not app.state.router.waiters
        assert (await client.get(f"/v1/results/{rid}")).json() == response.json()
        assert (await client.post(f"/v1/infer/{model}", json={"input": "next"})).status_code == 200


class PoisonRunner(FakeRunner):
    def predict_batch(self, inputs: list[dict[str, Any]]) -> list[dict[str, Any]]:
        raise ValueError("intentional poison")


async def test_poison_redelivery_limit_dead_letters_with_reply(redis_url, tmp_path):
    settings = settings_for(tmp_path, redis_url, base_ms=1, idle_ms=80, batch=1)
    model = settings.worker_model
    config = settings.load_models()[model]
    runner = PoisonRunner(config.fake)
    async with system(settings, runner) as (redis, _, client, _):
        accepted = await client.post(f"/v1/infer/{model}?async=true", json={"input": "poison"})
        rid = accepted.json()["request_id"]

        async def failed():
            result = await client.get(f"/v1/results/{rid}")
            return result if result.status_code != 202 else None

        result = await eventually(failed)
        assert result.status_code == 500
        assert result.json()["error"]["code"] == "DEAD_LETTERED"
        entries = await redis.xrange(f"dlq:{model}")
        assert len(entries) == 1
        assert entries[0][1]["delivery_count"] == "4"
        assert entries[0][1]["last_error"] == "INFERENCE_FAILED"

        async def acked():
            return (await redis.xpending(request_stream(model), GROUP))["pending"] == 0

        await eventually(acked)


async def test_timeout_and_cancelled_handler_discard_late_output(redis_url, tmp_path):
    settings = settings_for(tmp_path, redis_url, base_ms=250, idle_ms=800, deadline_ms=80, batch=1)
    model = settings.worker_model
    async with system(settings) as (redis, _, client, app):
        result = await client.post(f"/v1/infer/{model}", json={"input": "slow"})
        assert result.status_code == 504
        rid = result.json()["error"]["request_id"]
        assert await redis.exists(f"cancel:{rid}")
        assert not app.state.router.waiters

        async def expired():
            value = await redis.get(result_key(rid))
            return value if value and json.loads(value).get("response") else None

        final = ResultRecord.model_validate_json(await eventually(expired))
        assert final.status == "EXPIRED"

    settings = settings_for(tmp_path, redis_url, base_ms=200, idle_ms=800, batch=1)
    model = settings.worker_model
    async with system(settings) as (redis, _, client, app):
        task = asyncio.create_task(client.post(f"/v1/infer/{model}", json={"input": "cancel"}))

        async def enqueued():
            return await redis.xlen(request_stream(model))

        await eventually(enqueued)
        rid = next(iter(app.state.router.waiters))
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert await redis.exists(f"cancel:{rid}")

        async def cancelled():
            value = await redis.get(result_key(rid))
            return value if value and json.loads(value).get("response") else None

        assert ResultRecord.model_validate_json(await eventually(cancelled)).status == "CANCELLED"
        assert not app.state.router.waiters


async def test_streamed_body_size_is_bounded(redis_url, tmp_path):
    settings = settings_for(tmp_path, redis_url, max_body_bytes=100)
    async with system(settings) as (_, _, client, _):

        async def chunks():
            yield b'{"input":"'
            yield b"x" * 200
            yield b'"}'

        response = await client.post(
            f"/v1/infer/{settings.worker_model}",
            content=chunks(),
            headers={"Content-Type": "application/json"},
        )
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "INVALID_INPUT"


async def test_actual_http_disconnect_sets_cancel_flag(redis_url, tmp_path):
    settings = settings_for(tmp_path, redis_url, base_ms=200, idle_ms=800, batch=1)
    model = settings.worker_model
    async with system(settings) as (redis, _, _, app):
        disconnected = asyncio.Event()
        sent = []
        first = True

        async def receive():
            nonlocal first
            if first:
                first = False
                return {
                    "type": "http.request",
                    "body": b'{"input":"disconnect"}',
                    "more_body": False,
                }
            await disconnected.wait()
            return {"type": "http.disconnect"}

        async def send(message):
            sent.append(message)

        scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": f"/v1/infer/{model}",
            "raw_path": f"/v1/infer/{model}".encode(),
            "query_string": b"",
            "headers": [
                (b"content-type", b"application/json"),
                (b"x-api-key", settings.api_keys[0].get_secret_value().encode()),
            ],
            "client": ("127.0.0.1", 1234),
            "server": ("127.0.0.1", 8000),
            "root_path": "",
        }
        task = asyncio.create_task(app(scope, receive, send))

        async def accepted():
            return await redis.xlen(request_stream(model))

        await eventually(accepted)
        rid = next(iter(app.state.router.waiters))
        disconnected.set()
        await asyncio.wait_for(task, 5)
        assert await redis.exists(f"cancel:{rid}")
        assert any(m["type"] == "http.response.start" and m["status"] == 400 for m in sent)
        assert not app.state.router.waiters


async def test_malformed_envelope_eventually_quarantined(redis_url, tmp_path):
    settings = settings_for(tmp_path, redis_url, base_ms=1, idle_ms=50, batch=1)
    model = settings.worker_model
    async with system(settings) as (redis, _, _, app):
        rid = uuid4().hex
        await redis.xadd(
            request_stream(model),
            {
                "request_id": rid,
                "reply_to": app.state.router.inbox,
                "payload": "{}",
                "key_id": "test",
                "timeout_ms": "bad",
                "kind": "invalid",
            },
        )

        async def quarantined():
            return await redis.xlen(f"dlq:{model}")

        await eventually(quarantined)

        async def acked():
            return (await redis.xpending(request_stream(model), GROUP))["pending"] == 0

        await eventually(acked)
        entry = (await redis.xrange(f"dlq:{model}"))[0][1]
        assert entry["last_error"] == "INVALID_MESSAGE"
        assert entry["delivery_count"] == "4"
        assert (
            ResultRecord.model_validate_json(await redis.get(result_key(rid))).status
            == "DEAD_LETTERED"
        )
