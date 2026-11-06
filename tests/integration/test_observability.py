import asyncio
from uuid import uuid4

import httpx
import pytest
from prometheus_client import generate_latest

from infer_platform.common.clock import SystemClock
from infer_platform.common.queue_stats import snapshot
from infer_platform.common.redis import create_redis
from infer_platform.exporter.main import Exporter
from infer_platform.gateway.app import create_app
from infer_platform.worker.main import Worker
from tools.reliability import settings_for

pytestmark = pytest.mark.integration


async def test_metrics_separate_pending_backlog_and_preserve_label_bounds(redis_url, tmp_path):
    cfg = settings_for(tmp_path, redis_url, base_ms=5)
    redis = create_redis(cfg)
    model = cfg.worker_model
    worker = Worker(redis, model, cfg.load_models()[model], settings=cfg)
    await worker.setup()
    key = "req:" + model
    await redis.xadd(key, {"invalid": "entry"}, id="1-0")
    await redis.xadd(key, {"invalid": "entry"}, id="2-0")
    await redis.xreadgroup("workers", "test", {key: ">"}, count=1)
    stats = await snapshot(redis, model)
    assert (stats.pending, stats.backlog) == (1, 1)
    assert stats.head_age > 1
    await redis.delete(key)
    app = create_app(cfg)
    task = asyncio.create_task(worker.run())
    try:
        await asyncio.wait_for(worker.ready.wait(), 10)
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.post(
                    "/v1/infer/" + model,
                    headers={"X-API-Key": cfg.api_keys[0].get_secret_value()},
                    json={"input": "private-input-" + uuid4().hex},
                )
                assert response.status_code == 200
                timing = response.json()["meta"]
                assert timing["total_ms"] > 0
                assert sum(
                    timing[name]
                    for name in ("queue_ms", "batch_wait_ms", "inference_ms", "gateway_overhead_ms")
                ) == pytest.approx(timing["total_ms"])

                await client.post("/v1/infer/untrusted-user-label", json={"input": "x"})
                text = (await client.get("/metrics")).text
                assert "gateway_requests_total" in text
                assert "gateway_queue_wait_seconds_count" in text
                assert "UNAUTHORIZED" in text
                assert "untrusted-user-label" not in text
                assert response.json()["request_id"] not in text
        exporter = Exporter(redis, [model], SystemClock())
        await exporter.poll()
        text = generate_latest(exporter.registry).decode()
        assert f'workers_alive{{model="{model}"}} 1.0' in text
        assert f'queue_backlog{{model="{model}"}} 0.0' in text
        text = generate_latest(worker.metrics.registry).decode()
        assert "worker_inference_seconds_count" in text
        assert "worker_batch_size_count" in text
        assert 'outcome="SUCCEEDED"' in text
    finally:
        worker.stop.set()
        await task
        await redis.aclose()
