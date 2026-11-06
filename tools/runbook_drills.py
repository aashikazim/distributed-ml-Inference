"""Isolated operational drills: real TCP gateway/Redis, simulated compute."""

import asyncio
import json
import platform
import socket
import subprocess
import tempfile
import time
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
import structlog
from testcontainers.community.redis import RedisContainer

from infer_platform.common.redis import create_redis
from infer_platform.worker.main import Worker
from infer_platform.worker.runners.fake import FakeRunner
from tools.llm_lab import LocalServer
from tools.reliability import settings_for

log = structlog.get_logger()


class PoisonRunner(FakeRunner):
    def predict_batch(self, inputs: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if any(item["input"] == "POISON" for item in inputs):
            raise ValueError("Controlled runbook failure; no prompt data in error")
        return super().predict_batch(inputs)


@asynccontextmanager
async def lab(
    root: Path,
    url: str,
    *,
    base_ms: float = 10,
    depth: int = 100,
    poison: bool = False,
    start_worker: bool = True,
) -> AsyncGenerator[dict[str, Any]]:
    cfg = await asyncio.to_thread(
        settings_for,
        root,
        url,
        base_ms=base_ms,
        batch=1,
        depth=depth,
        idle_ms=120 if poison else 1000,
        deadline_ms=6000,
        redis_timeout_s=1.2,
    )
    config = cfg.load_models()[cfg.worker_model]
    redis = create_redis(cfg)
    worker = Worker(
        redis,
        cfg.worker_model,
        config,
        settings=cfg,
        runner=PoisonRunner(config.fake) if poison else FakeRunner(config.fake),
    )
    task = asyncio.create_task(worker.run()) if start_worker else None
    gateway = None
    try:
        if task:
            await asyncio.wait_for(worker.ready.wait(), 10)
        gateway = await asyncio.to_thread(
            LocalServer,
            root,
            "infer_platform.gateway.app:create_app",
            {
                "INFER_REDIS_URL": url,
                "INFER_MODELS_PATH": str(cfg.models_path),
                "INFER_API_KEYS": json.dumps([cfg.api_keys[0].get_secret_value()]),
                "INFER_REDIS_TIMEOUT_S": "1.2",
                "INFER_RATE_LIMIT_PER_S": "100000",
                "INFER_RATE_LIMIT_BURST": "100000",
            },
        )
        await gateway.ready("/readyz")
        async with httpx.AsyncClient(
            base_url=gateway.url,
            headers={"X-API-Key": cfg.api_keys[0].get_secret_value()},
            timeout=10,
        ) as client:
            yield {
                "cfg": cfg,
                "redis": redis,
                "worker": worker,
                "task": task,
                "client": client,
                "gateway": gateway,
                "config": config,
            }
    finally:
        worker.stop.set()
        if task:
            await asyncio.gather(task, return_exceptions=True)
        if gateway:
            await gateway.close()
        await redis.aclose()


def result(response: httpx.Response) -> dict[str, Any]:
    return {
        "status": response.status_code,
        "retry_after": response.headers.get("retry-after"),
        "data": response.json(),
    }


async def post(state: dict[str, Any], text: str = "synthetic") -> httpx.Response:
    return await state["client"].post(
        "/v1/infer/" + state["cfg"].worker_model, json={"input": text}
    )


async def run(root: Path, container: RedisContainer, url: str, report: dict[str, Any]) -> dict:
    async with lab(root, url, start_worker=False) as state:
        response = await post(state)
        assert response.status_code == 503 and response.headers.get("retry-after")
        report["no_workers"] = result(response)
        recovery_task = asyncio.create_task(state["worker"].run())
        try:
            await asyncio.wait_for(state["worker"].ready.wait(), 10)
            recovered = await post(state)
            assert recovered.status_code == 200
            report["no_workers"]["recovered_status"] = recovered.status_code
        finally:
            state["worker"].stop.set()
            await recovery_task
    async with lab(root, url, base_ms=200) as state:
        responses = await asyncio.gather(*(post(state) for _ in range(6)))
        assert all(r.status_code == 200 for r in responses)
        report["slow_model"] = {
            "responses": [result(r) for r in responses],
            "last_inference_ms": responses[-1].json()["meta"]["inference_ms"],
            "max_queue_ms": max(r.json()["meta"]["queue_ms"] for r in responses),
        }
        state["config"].fake.base_ms = 10
        restored = await post(state)
        assert restored.status_code == 200
        assert restored.json()["meta"]["inference_ms"] < report["slow_model"]["last_inference_ms"]
        report["slow_model"]["restored_inference_ms"] = restored.json()["meta"]["inference_ms"]
    async with lab(root, url, base_ms=300, depth=2) as state:
        peak = 0
        tasks = [asyncio.create_task(post(state)) for _ in range(20)]
        while not all(t.done() for t in tasks):
            peak = max(peak, await state["redis"].xlen("req:" + state["cfg"].worker_model))
            await asyncio.sleep(0.005)
        responses = await asyncio.gather(*tasks)
        assert peak <= 2
        assert any(r.status_code == 503 and r.headers.get("retry-after") for r in responses)
        report["overload"] = {
            "peak_retained": peak,
            "statuses": {
                str(s): sum(r.status_code == s for r in responses)
                for s in {r.status_code for r in responses}
            },
        }
        recovered = await post(state)
        assert recovered.status_code == 200
        report["overload"]["recovered_status"] = recovered.status_code
    for label, count in [("poison_input", 1), ("dlq_growth", 3)]:
        async with lab(root, url, poison=True) as state:
            responses = await asyncio.gather(*(post(state, "POISON") for _ in range(count)))
            assert all(
                r.status_code == 500 and r.json()["error"]["code"] == "DEAD_LETTERED"
                for r in responses
            )
            model = state["cfg"].worker_model
            dlq = await state["redis"].xlen("dlq:" + model)
            reclaimed = state["worker"].metrics.registry.get_sample_value(
                "worker_reclaimed_total", {"model": model}
            )
            assert dlq == count
            assert reclaimed is not None and reclaimed >= count * 3
            report[label] = {
                "quarantined": dlq,
                "reclaimed_attempts": reclaimed,
                "responses": [result(r) for r in responses],
            }
            healthy = await post(state)
            assert healthy.status_code == 200
            assert await state["redis"].xlen("dlq:" + model) == count
            report[label]["healthy_input_status"] = healthy.status_code
    async with lab(root, url) as state:
        before = await post(state)
        assert before.status_code == 200
        rid = before.json()["request_id"]
        raw = container.get_wrapped_container()
        await asyncio.to_thread(raw.stop, timeout=1)
        try:
            alive = await state["client"].get("/healthz")
            ready = await state["client"].get("/readyz")
            failed = await post(state)
            assert (
                alive.status_code == 200 and ready.status_code == 503 and failed.status_code == 503
            )
            report["redis_down"] = {
                "liveness": alive.status_code,
                "readiness": ready.status_code,
                "inference": result(failed),
            }
        finally:
            await asyncio.to_thread(raw.start)
        await state["gateway"].ready("/readyz")
        # The worker fails closed on Redis loss. Demonstrate the documented restart.
        await asyncio.gather(state["task"], return_exceptions=True)
        replacement_redis = create_redis(state["cfg"])
        replacement = Worker(
            replacement_redis, state["cfg"].worker_model, state["config"], settings=state["cfg"]
        )
        replacement_task = asyncio.create_task(replacement.run())
        try:
            await asyncio.wait_for(replacement.ready.wait(), 10)
            recovered = await post(state)
            cached = await state["client"].get("/v1/results/" + rid)
            assert recovered.status_code == 200 and cached.status_code == 200
            report["redis_down"].update(
                recovered_status=recovered.status_code,
                preserved_final_cache_status=cached.status_code,
                worker_restart_required=True,
            )
        finally:
            replacement.stop.set()
            try:
                await replacement_task
            finally:
                await replacement_redis.aclose()
    return report


def main() -> None:
    report: dict[str, Any] = {
        "date_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "host": platform.platform(),
        "commit": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        "real_redis_and_tcp": True,
        "simulated_compute": True,
        "drills": {},
    }
    # Random Docker host ports can change across stop/start. Reserve a fixed
    # loopback port for this owned container so outage recovery uses one endpoint.
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    try:
        with tempfile.TemporaryDirectory(prefix="infer-runbook-") as directory:
            container = RedisContainer("redis:7.4").with_command("redis-server --appendonly yes")
            # Docker supports loopback tuples in the public forwarded port map;
            # with_bind_ports itself only accepts an integer host port.
            container.ports = {"6379/tcp": ("127.0.0.1", port)}
            with container:
                url = f"redis://{container.get_container_host_ip()}:{container.get_exposed_port(6379)}/0"
                asyncio.run(run(Path(directory), container, url, report["drills"]))
    except Exception as exc:
        report["failure_type"] = type(exc).__name__
        raise
    finally:
        Path("docs/results/p8-runbook.json").write_text(json.dumps(report, indent=2) + "\n")
    log.info("runbook_drills_passed", drills=list(report["drills"]))


if __name__ == "__main__":
    main()
