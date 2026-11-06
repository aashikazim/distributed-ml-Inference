"""Disposable real-Redis E1: successful round trip, then SIGKILL after delivery."""

import asyncio
import json
import os
import platform
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
import structlog
from pydantic import SecretStr
from redis.asyncio import Redis
from testcontainers.community.redis import RedisContainer

from infer_platform.common.redis import create_redis
from infer_platform.common.settings import Settings
from infer_platform.gateway.app import create_app

log = structlog.get_logger()


async def pending(redis: Redis, stream: str) -> dict[str, Any]:
    async with asyncio.timeout(10):
        while True:
            summary = await redis.xpending(stream, "workers")
            if summary["pending"]:
                return summary
            await asyncio.sleep(0.01)


async def snapshot(redis: Redis, stream: str) -> dict[str, Any]:
    return {
        "xinfo_stream": await redis.xinfo_stream(stream),
        "xinfo_groups": await redis.xinfo_groups(stream),
        "xpending": await redis.xpending(stream, "workers"),
        "xlen": await redis.xlen(stream),
    }


async def experiment(url: str, root: Path) -> None:
    model = "e1-" + uuid4().hex
    stream = f"req:{model}"
    path = root / "models.yaml"
    path.write_text(
        f"models:\n  {model}:\n    backend: fake\n    request_timeout_ms: 1000\n"
        "    fake: {base_ms: 20, per_item_ms: 0, jitter_ms: 0}\n"
    )
    settings = Settings(
        redis_url=url,
        api_keys=[SecretStr("disposable-e1-key")],
        models_path=path,
        worker_model=model,
    )
    redis = create_redis(settings)
    env = {
        **os.environ,
        "INFER_REDIS_URL": url,
        "INFER_MODELS_PATH": str(path),
        "INFER_API_KEYS": '["disposable-e1-key"]',
        "INFER_WORKER_MODEL": model,
    }
    process = None
    report = {}

    def launch() -> subprocess.Popen[bytes]:
        return subprocess.Popen(
            [sys.executable, "-m", "infer_platform.worker.main"],
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    try:
        app = create_app(settings)
        async with app.router.lifespan_context(app):
            # Use the same initialized app for ASGI HTTP requests.
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://e1", timeout=10
            ) as client:
                process = launch()
                response = await client.post(
                    f"/v1/infer/{model}",
                    json={"input": "hello"},
                    headers={"X-API-Key": "disposable-e1-key"},
                )
                assert response.status_code == 200, response.text
                report["success"] = response.json()
                async with asyncio.timeout(5):
                    while await redis.xlen(stream):
                        await asyncio.sleep(0.01)
                report["after_success"] = await snapshot(redis, stream)
                report["bad_key_status"] = (
                    await client.post(
                        f"/v1/infer/{model}", json={"input": "x"}, headers={"X-API-Key": "wrong"}
                    )
                ).status_code
                report["unknown_model_status"] = (
                    await client.post(
                        "/v1/infer/unknown",
                        json={"input": "x"},
                        headers={"X-API-Key": "disposable-e1-key"},
                    )
                ).status_code
                process.terminate()
                await asyncio.to_thread(process.wait, 5)
                # Slow simulated compute creates a deterministic window to kill after delivery.
                path.write_text(path.read_text().replace("base_ms: 20", "base_ms: 2000"))
                process = launch()
                request = asyncio.create_task(
                    client.post(
                        f"/v1/infer/{model}",
                        json={"input": "kill during simulated compute"},
                        headers={"X-API-Key": "disposable-e1-key"},
                    )
                )
                await pending(redis, stream)
                report["before_kill"] = await snapshot(redis, stream)
                process.kill()
                await asyncio.to_thread(process.wait, 5)
                response = await request
                report["killed_request_status"] = response.status_code
                report["after_kill"] = await snapshot(redis, stream)
                assert response.status_code == 504
                assert report["after_kill"]["xpending"]["pending"] == 1
                assert report["after_kill"]["xlen"] == 1
        report["redis_version"] = (await redis.info("server"))["redis_version"]
        report["platform"] = platform.platform()
        report["cpu"] = platform.processor()
        report["python"] = platform.python_version()
        report["commit"] = (
            await asyncio.to_thread(
                subprocess.check_output, ["git", "rev-parse", "HEAD"], text=True
            )
        ).strip()
        report["date"] = "2026-11-06"
        await asyncio.to_thread(
            Path("docs/e1-results.json").write_text, json.dumps(report, indent=2) + "\n"
        )
        log.info(
            "E1_complete",
            success=200,
            killed_request=report["killed_request_status"],
            pending=report["after_kill"]["xpending"]["pending"],
        )
    finally:
        if process and process.poll() is None:
            process.kill()
            await asyncio.to_thread(process.wait, 5)
        await redis.delete(stream)
        await redis.aclose()


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="infer-e1-") as directory:
        with RedisContainer("redis:7.4") as redis:
            asyncio.run(
                experiment(
                    f"redis://{redis.get_container_host_ip()}:{redis.get_exposed_port(6379)}/0",
                    Path(directory),
                )
            )


if __name__ == "__main__":
    main()
