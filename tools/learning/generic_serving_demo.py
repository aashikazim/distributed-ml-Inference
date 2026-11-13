"""Serve four different model families through the same gateway, Redis and worker code.

Used by docs/learning/09-generic-serving.md. Starts a disposable Redis container,
one in-process worker per model in config/models.examples.yaml, and the real
gateway app, then sends real requests over its HTTP interface (ASGI transport).
Downloads the example models on first use (about 450 MB). CPU only.

    uv run --extra ml python -m tools.learning.generic_serving_demo
"""

import asyncio
import base64
import io
import json
import math
import sys
import tempfile
from pathlib import Path
from typing import Any

import httpx
from pydantic import SecretStr
from testcontainers.community.redis import RedisContainer

from infer_platform.common.redis import create_redis
from infer_platform.common.settings import Settings
from infer_platform.gateway.app import create_app
from infer_platform.worker.main import Worker
from infer_platform.worker.runners.registry import create_runner
from tools.learning.onnx_double import write_double_model

CATALOG = Path("config/models.examples.yaml")
CACHE = Path("/tmp/infer-model-cache")
KEY = "demo-key"


def png(color: tuple[int, int, int]) -> str:
    from PIL import Image, ImageDraw

    image = Image.new("RGB", (224, 224), "white")
    ImageDraw.Draw(image).ellipse((40, 40, 184, 184), fill=color)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode()


def cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    return dot / (math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b)))


async def run(redis_url: str) -> dict[str, Any]:
    write_double_model(CACHE / "double.onnx")
    settings = Settings(
        redis_url=SecretStr(redis_url),
        api_keys=[SecretStr(KEY)],
        models_path=CATALOG,
        model_cache=CACHE,
        max_body_bytes=4_000_000,
        rate_limit_per_s=10_000,
        rate_limit_burst=10_000,
    )
    models = settings.load_models()
    redis = create_redis(settings)
    workers = [
        Worker(redis, name, config, runner=create_runner(config, settings), settings=settings)
        for name, config in models.items()
    ]
    tasks: list[asyncio.Task[None]] = []
    report: dict[str, Any] = {}
    try:
        # One at a time: production runs one worker per process, but here four share a
        # process, and concurrent first imports of transformers in threads can deadlock.
        for worker in workers:
            tasks.append(asyncio.create_task(worker.run()))
            await asyncio.wait_for(worker.ready.wait(), 900)  # first run downloads weights
        app = create_app(settings)
        async with app.router.lifespan_context(app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(
                transport=transport, base_url="http://demo", timeout=60
            ) as client:
                headers = {"X-API-Key": KEY}

                async def infer(model: str, body: dict[str, Any]) -> dict[str, Any]:
                    response = await client.post(f"/v1/infer/{model}", json=body, headers=headers)
                    response.raise_for_status()
                    result: dict[str, Any] = response.json()
                    return result

                listing = (await client.get("/v1/models", headers=headers)).json()["models"]
                report["models"] = {m["id"]: m.get("inputs") for m in listing}

                image = await infer(
                    "image-classifier",
                    {"inputs": {"image": {"type": "image", "data_b64": png((220, 30, 30))}}},
                )
                report["image"] = {"output": image["output"], "meta": image["meta"]}

                sentences = {
                    "a": "The cat sat on the warm windowsill.",
                    "b": "A kitten was resting by the sunny window.",
                    "c": "Quarterly revenue grew by twelve percent.",
                }
                vectors = {
                    key: (
                        await infer("embedder", {"inputs": {"text": {"type": "text", "value": v}}})
                    )["output"]["embedding"]
                    for key, v in sentences.items()
                }
                report["embedding"] = {
                    "dimensions": len(vectors["a"]),
                    "sentences": sentences,
                    "cosine_a_b_related": cosine(vectors["a"], vectors["b"]),
                    "cosine_a_c_unrelated": cosine(vectors["a"], vectors["c"]),
                }

                sentiment = await infer("sentiment-pipeline", {"input": "I loved this movie."})
                report["sentiment_legacy_body"] = sentiment["output"]

                onnx = await infer(
                    "tabular-onnx",
                    {"inputs": {"x": {"type": "tensor", "dtype": "float32", "shape": [3],
                                      "data": [1.5, -2, 4]}}},
                )  # fmt: skip
                report["onnx"] = onnx["output"]

                # Cost-aware batching: 8 short + 8 long texts at once, budget 4096 padded tokens.
                texts = ["Short note."] * 8 + ["word " * 360] * 8  # 1,800 chars: within max_chars
                burst = await asyncio.gather(
                    *(
                        infer("embedder", {"inputs": {"text": {"type": "text", "value": t}}})
                        for t in texts
                    )
                )
                report["cost_batching"] = {
                    "max_batch_size": models["embedder"].max_batch_size,
                    "max_batch_cost": models["embedder"].max_batch_cost,
                    "batch_size_seen_by_short": [r["meta"]["batch_size"] for r in burst[:8]],
                    "batch_size_seen_by_long": [r["meta"]["batch_size"] for r in burst[8:]],
                }
    finally:
        for worker in workers:
            worker.stop.set()
        await asyncio.gather(*tasks, return_exceptions=True)
        await redis.aclose()
    return report


def main() -> None:
    CACHE.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(), RedisContainer("redis:7.4") as container:
        url = f"redis://{container.get_container_host_ip()}:{container.get_exposed_port(6379)}/0"
        report = asyncio.run(run(url))
    out = Path("docs/results/learning/generic-serving-demo.json")
    out.write_text(json.dumps(report, indent=2) + "\n")
    sys.stdout.write(json.dumps(report, indent=2)[:4000] + "\n")


if __name__ == "__main__":
    main()
