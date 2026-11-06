import asyncio
import json
import os
import time
from pathlib import Path

import httpx
import pytest
from prometheus_client.parser import text_string_to_metric_families

from infer_platform.common.redis import create_redis
from infer_platform.common.sse import sse_data
from infer_platform.worker.llm_worker import LLMWorker
from tools.llm_lab import LocalServer, llm_settings
from tools.reliability import eventually

pytestmark = [pytest.mark.gpu, pytest.mark.integration]


def running(text: str) -> float:
    return sum(
        sample.value
        for family in text_string_to_metric_families(text)
        for sample in family.samples
        if sample.name == "vllm:num_requests_running"
    )


async def test_real_vllm_completion_and_disconnect_abort(redis_url, tmp_path):
    upstream = os.environ.get("INFER_GPU_BASE_URL")
    if not upstream:
        pytest.skip("Set INFER_GPU_BASE_URL to an explicitly verified GPU vLLM server")
    cfg = llm_settings(tmp_path, redis_url, upstream.rstrip("/"), deadline_ms=60000, cap=512)
    model = cfg.worker_model
    config = cfg.load_models()[model]
    config.llm.http_timeout_s = 60
    redis = create_redis(cfg)
    worker = LLMWorker(redis, model, config, cfg)
    task = asyncio.create_task(worker.run())
    gateway = None
    try:
        await asyncio.wait_for(worker.ready.wait(), 20)
        gateway = await asyncio.to_thread(
            LocalServer,
            tmp_path,
            "infer_platform.gateway.app:create_app",
            {
                "INFER_REDIS_URL": cfg.redis_url.get_secret_value(),
                "INFER_MODELS_PATH": str(cfg.models_path),
                "INFER_API_KEYS": json.dumps([cfg.api_keys[0].get_secret_value()]),
            },
        )
        await gateway.ready("/readyz")
        async with httpx.AsyncClient(timeout=60) as client:
            headers = {"X-API-Key": cfg.api_keys[0].get_secret_value()}
            response = await client.post(
                gateway.url + "/v1/chat/completions",
                headers=headers,
                json={
                    "model": model,
                    "messages": [{"role": "user", "content": "Say hello briefly."}],
                    "max_tokens": 32,
                    "temperature": 0.0,
                    "stream": False,
                },
            )
            assert response.status_code == 200, response.text
            data = response.json()
            assert data["choices"][0]["message"]["content"]
            assert data["usage"]["completion_tokens"] > 0
            assert data["meta"]["ttft_ms"] > 0
            observed_running = 0.0
            async with client.stream(
                "POST",
                gateway.url + "/v1/chat/completions",
                headers=headers,
                json={
                    "model": model,
                    "messages": [
                        {
                            "role": "user",
                            "content": (
                                "Count integers starting at 1, separated by commas, "
                                "until you reach 1000. Do not stop early."
                            ),
                        }
                    ],
                    "max_tokens": 512,
                    "temperature": 0.0,
                    "stream": True,
                },
            ) as streamed:
                assert streamed.status_code == 200
                rid = streamed.headers["x-request-id"]
                async for event in sse_data(streamed):
                    chunk = json.loads(event)
                    if any(c["delta"].get("content") for c in chunk["choices"]):
                        observed_running = running((await client.get(upstream + "/metrics")).text)
                        break
            disconnected_at = time.monotonic()
            assert observed_running > 0, "Need an active sequence at disconnect to verify abort"

            async def aborted():
                metrics = (await client.get(upstream + "/metrics")).text
                value = await redis.get("result:" + rid)
                return (
                    running(metrics) == 0 and value and json.loads(value)["status"] == "CANCELLED"
                )

            await eventually(aborted, seconds=10)
            report = {
                "gpu_inference_verified": True,
                "completion_usage": data["usage"],
                "completion_meta": data["meta"],
                "active_sequences_before_disconnect": observed_running,
                "active_sequences_after_disconnect": 0,
                "disconnect_to_zero_and_cancelled_ms": (time.monotonic() - disconnected_at) * 1000,
                "worker_outcome": "CANCELLED",
            }
            await asyncio.to_thread(
                Path(
                    os.environ.get("INFER_GPU_REPORT_PATH", "docs/results/p4-gpu-verification.json")
                ).write_text,
                json.dumps(report, indent=2) + "\n",
            )
    finally:
        if gateway:
            await gateway.close()
        worker.stop.set()
        await asyncio.wait_for(task, 10)
        await redis.delete(f"req:{model}")
        await redis.aclose()
