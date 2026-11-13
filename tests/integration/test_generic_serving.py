"""Phase D end to end with real Redis: typed inputs, claim-check, worker directory."""

import asyncio
import base64
from uuid import uuid4

import httpx
import pytest
from pydantic import SecretStr

from infer_platform.common.keys import GROUP, request_stream, worker_key
from infer_platform.common.redis import create_redis
from infer_platform.common.schemas import RequestMessage
from infer_platform.common.settings import Settings
from infer_platform.gateway.admission import WorkerDirectory, enqueue
from infer_platform.gateway.app import create_app
from infer_platform.worker.main import Worker

pytestmark = pytest.mark.integration

CATALOG = """models:
  {model}:
    backend: fake
    request_timeout_ms: 3000
    fake: {{base_ms: 1, per_item_ms: 0, jitter_ms: 0}}
    inputs:
      question: {{type: text, max_chars: 100}}
      photo: {{type: image, max_bytes: 200000}}
      features: {{type: tensor, dtype: float32, shape: [-1], max_elements: 4, required: false}}
"""


@pytest.fixture
async def settings(redis_url, tmp_path):
    model = "generic-" + uuid4().hex
    path = tmp_path / "models.yaml"
    path.write_text(CATALOG.format(model=model))
    return Settings(
        redis_url=redis_url,
        api_keys=[SecretStr("test-key")],
        models_path=path,
        worker_model=model,
        claim_check_bytes=1024,
        max_body_bytes=400000,
    )


def photo(size: int) -> str:
    return base64.b64encode(b"\x89PNG" + b"\0" * size).decode()


async def test_typed_inputs_and_claim_check_round_trip(settings):
    redis = create_redis(settings)
    model = settings.worker_model
    worker = Worker(redis, model, settings.load_models()[model], settings=settings)
    task = asyncio.create_task(worker.run())
    await asyncio.wait_for(worker.ready.wait(), 5)
    app = create_app(settings)
    try:
        async with app.router.lifespan_context(app):
            blobs: list[tuple[str, int]] = []
            original_set = app.state.redis.set

            async def record_set(name, value, *args, **kwargs):
                if str(name).startswith("blob:"):
                    blobs.append((name, len(value)))
                return await original_set(name, value, *args, **kwargs)

            app.state.redis.set = record_set
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test", timeout=5
            ) as client:
                headers = {"X-API-Key": "test-key"}
                path = f"/v1/infer/{model}"
                listing = (await client.get("/v1/models", headers=headers)).json()["models"]
                assert listing[0]["inputs"]["photo"] == {
                    "type": "image",
                    "required": True,
                    "max_bytes": 200000,
                }

                small = {
                    "inputs": {
                        "question": {"type": "text", "value": "what is this?"},
                        "photo": {"type": "image", "data_b64": photo(10)},
                        "features": {"type": "tensor", "dtype": "float32", "shape": [2],
                                     "data": [0.5, 1.5]},
                    }
                }  # fmt: skip
                response = await client.post(path, json=small, headers=headers)
                assert response.status_code == 200, response.text
                assert response.json()["output"] == {
                    "label": "SIMULATED",
                    "inputs": ["features", "photo", "question"],
                }

                # Larger than claim_check_bytes: the stream entry must carry only a reference.
                big = {
                    "inputs": {
                        "question": {"type": "text", "value": "big"},
                        "photo": {"type": "image", "data_b64": photo(50000)},
                    }
                }
                response = await client.post(path, json=big, headers=headers)
                assert response.status_code == 200, response.text
                request_id = response.json()["request_id"]
                assert len(blobs) == 1 and blobs[0][0] == f"blob:{request_id}"
                assert blobs[0][1] > settings.claim_check_bytes  # small request used no blob
                assert not await redis.exists(f"blob:{request_id}")  # deleted with the ACK
                assert await redis.xlen(request_stream(model)) == 0
                assert (await redis.xpending(request_stream(model), GROUP))["pending"] == 0

                legacy = await client.post(path, json={"input": "hi"}, headers=headers)
                assert legacy.status_code == 422
                assert "named 'inputs'" in legacy.json()["error"]["message"]
                wrong = await client.post(
                    path,
                    json={"inputs": {"question": {"type": "text", "value": "x"}}},
                    headers=headers,
                )
                assert wrong.status_code == 422
                assert "missing inputs" in wrong.json()["error"]["message"]
    finally:
        worker.stop.set()
        await asyncio.wait_for(task, 5)
        await redis.aclose()


async def test_claim_check_entry_is_small_and_missing_blob_fails_cleanly(settings):
    redis = create_redis(settings)
    model = settings.worker_model
    worker = Worker(redis, model, settings.load_models()[model], settings=settings)
    task = asyncio.create_task(worker.run())
    await asyncio.wait_for(worker.ready.wait(), 5)
    app = create_app(settings)
    try:
        async with app.router.lifespan_context(app):
            router = app.state.router
            request_id = uuid4().hex
            queue = router.register(request_id)
            message = RequestMessage(
                request_id=request_id,
                reply_to=router.inbox,
                payload="",
                payload_ref=f"blob:{request_id}",  # never written: simulates an expired blob
                timeout_ms=3000,
                key_id="k",
                trace_id="t",
            )
            outcome = await enqueue(redis, model, message, 100, 60, True, app.state.directory)
            assert outcome == "ACCEPTED"
            entries = await redis.xrange(request_stream(model))
            assert entries[0][1]["payload"] == "" and entries[0][1]["payload_ref"]
            reply = await asyncio.wait_for(queue.get(), 5)
            assert reply.type == "error" and "PAYLOAD_MISSING" in reply.data
    finally:
        worker.stop.set()
        await asyncio.wait_for(task, 5)
        await redis.aclose()


class StepClock:
    def __init__(self) -> None:
        self.now = 0.0

    def monotonic(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.now += seconds


async def test_worker_directory_caches_and_recovers_from_stale_keys(settings):
    redis = create_redis(settings)
    model = settings.worker_model
    clock = StepClock()
    directory = WorkerDirectory(redis, ttl_s=60, clock=clock)
    old, new = worker_key(model, "old"), worker_key(model, "new")
    heartbeat = '{"worker_id":"w","device":"cpu","backend":"fake","in_flight":0,'
    heartbeat += '"busy_ratio":0,"started_at":0}'

    def message() -> RequestMessage:
        rid = uuid4().hex
        return RequestMessage(
            request_id=rid, reply_to="inbox:x", payload="{}", timeout_ms=1000, key_id="k",
            trace_id="t",
        )  # fmt: skip

    try:
        await redis.set(old, heartbeat, ex=60)
        assert await enqueue(redis, model, message(), 100, 60, True, directory) == "ACCEPTED"
        assert directory.cache[model][1] == [old]
        # The cached worker dies and a new one registers; the cache is still "fresh".
        await redis.delete(old)
        await redis.set(new, heartbeat, ex=60)
        assert await enqueue(redis, model, message(), 100, 60, True, directory) == "ACCEPTED"
        assert directory.cache[model][1] == [new]  # one forced rescan fixed it
        await redis.delete(new)
        assert await enqueue(redis, model, message(), 100, 60, True, directory) == "NO_WORKERS"
    finally:
        await redis.delete(request_stream(model))
        await redis.aclose()
