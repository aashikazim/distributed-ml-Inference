import asyncio
import json
from uuid import uuid4

import httpx
import pytest
from pydantic import SecretStr

from infer_platform.common.keys import GROUP, request_stream
from infer_platform.common.redis import create_redis
from infer_platform.common.schemas import RequestMessage
from infer_platform.common.settings import ModelConfig, Settings
from infer_platform.gateway.app import create_app
from infer_platform.worker.main import Worker

pytestmark = pytest.mark.integration


@pytest.mark.model
async def test_real_sentiment_http_roundtrip(redis_url, tmp_path, sentiment_runner):
    model = "sentiment-" + uuid4().hex
    path = tmp_path / "models.yaml"
    path.write_text(
        f"models:\n  {model}:\n    backend: torch\n"
        "    hf_id: distilbert-base-uncased-finetuned-sst-2-english\n"
        "    max_batch_size: 4\n    max_wait_ms: 30\n    request_timeout_ms: 5000\n"
    )
    settings = Settings(redis_url=redis_url, api_keys=[SecretStr("test")], models_path=path)
    redis = create_redis(settings)
    worker = Worker(redis, model, settings.load_models()[model], runner=sentiment_runner)
    task = asyncio.create_task(worker.run())
    app = create_app(settings)
    try:
        await asyncio.wait_for(worker.ready.wait(), 10)
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test", timeout=10
            ) as client:
                texts = ["I loved this movie.", "It was awful.", "An excellent film.", "So boring."]
                responses = await asyncio.gather(
                    *(
                        client.post(
                            f"/v1/infer/{model}",
                            json={"input": text},
                            headers={"X-API-Key": "test"},
                        )
                        for text in texts
                    )
                )
                assert all(r.status_code == 200 for r in responses)
                assert [r.json()["output"]["label"] for r in responses] == [
                    "POSITIVE",
                    "NEGATIVE",
                    "POSITIVE",
                    "NEGATIVE",
                ]
                assert {r.json()["meta"]["batch_size"] for r in responses} == {4}
                assert len({r.json()["request_id"] for r in responses}) == 4
        worker.stop.set()
        await asyncio.wait_for(task, 10)
        assert await redis.xlen(request_stream(model)) == 0
        assert (await redis.xpending(request_stream(model), GROUP))["pending"] == 0
    finally:
        worker.stop.set()
        await task
        await redis.delete(request_stream(model))
        await redis.aclose()


async def test_expiry_cancellation_validation_and_identity(redis_url):
    model = "drops-" + uuid4().hex
    settings = Settings(redis_url=redis_url, api_keys=[SecretStr("unused")])
    redis = create_redis(settings)
    config = ModelConfig(backend="fake", max_batch_size=4, max_wait_ms=50)
    worker = Worker(redis, model, config)
    reply = "inbox:" + model
    rids = [uuid4().hex for _ in range(4)]
    await worker.setup()
    for index, rid in enumerate(rids):
        message = RequestMessage(
            request_id=rid,
            reply_to=reply,
            key_id="hash",
            trace_id="trace",
            timeout_ms=5000,
            payload="{}" if index == 2 else '{"input":"valid"}',
        )
        await redis.xadd(request_stream(model), message.fields(), id="1-0" if index == 0 else "*")
    await redis.set(f"cancel:{rids[1]}", "1", ex=120)
    task = asyncio.create_task(worker.run())
    try:
        async with asyncio.timeout(10):
            while await redis.xlen(reply) < 4:
                await asyncio.sleep(0.01)
        entries = await redis.xrange(reply)
        outcomes = {fields["request_id"]: fields for _, fields in entries}
        assert json.loads(outcomes[rids[0]]["data"])["code"] == "EXPIRED"
        assert json.loads(outcomes[rids[1]]["data"])["code"] == "CANCELLED"
        assert json.loads(outcomes[rids[2]]["data"])["code"] == "INVALID_INPUT"
        assert json.loads(outcomes[rids[3]]["data"])["label"] == "SIMULATED"
        assert json.loads(outcomes[rids[3]]["meta"])["batch_size"] == 1
    finally:
        worker.stop.set()
        await asyncio.wait_for(task, 10)
        await redis.delete(request_stream(model), reply, f"cancel:{rids[1]}")
        await redis.aclose()
