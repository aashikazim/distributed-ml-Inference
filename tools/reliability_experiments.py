"""E4/E5: real Redis and process failure, with explicitly simulated model compute."""

import argparse
import asyncio
import json
import platform
import statistics
import subprocess
import tempfile
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import structlog
from testcontainers.community.redis import RedisContainer

from infer_platform.common.keys import GROUP, request_stream
from infer_platform.common.redis import create_redis
from infer_platform.common.settings import Settings
from infer_platform.gateway.app import create_app
from tools.reliability import WorkerProcess, busy, client_for, eventually, settings_for

log = structlog.get_logger()


def metadata() -> dict[str, Any]:
    return {
        "date_utc": datetime.now(UTC).isoformat(),
        "platform": platform.platform(),
        "python": platform.python_version(),
        "cpu_inventory": subprocess.check_output(["lscpu"], text=True),
        "commit": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        "tracked_changes": subprocess.check_output(
            ["git", "diff", "--name-only"], text=True
        ).splitlines(),
        "simulated_compute": True,
    }


async def recovery_trial(url: str, root: Path, idle_ms: int) -> dict[str, Any]:
    settings = settings_for(root, url, base_ms=200, idle_ms=idle_ms)
    model = settings.worker_model
    redis = create_redis(settings)
    victim = await asyncio.to_thread(WorkerProcess, settings, root)
    survivor = None
    app = create_app(settings)
    loop = asyncio.get_running_loop()
    try:
        victim_id = await victim.ready(redis, model)
        async with app.router.lifespan_context(app):
            async with client_for(app, settings) as client:
                requests = [
                    asyncio.create_task(
                        client.post(
                            f"/v1/infer/{model}",
                            json={"input": f"synthetic-{i}"},
                        )
                    )
                    for i in range(4)
                ]
                await busy(redis, model, victim_id, 4)
                before = await redis.xpending_range(request_stream(model), GROUP, "-", "+", 4)
                killed_at = loop.time()
                assert await victim.stop(kill=True) == -9
                after_kill = await redis.xpending(request_stream(model), GROUP)
                survivor = await asyncio.to_thread(WorkerProcess, settings, root)
                survivor_id = await survivor.ready(redis, model)
                responses = await asyncio.gather(*requests)
                recovered_ms = (loop.time() - killed_at) * 1000
                assert [r.status_code for r in responses] == [200] * 4
                assert {r.json()["meta"]["worker_id"] for r in responses} == {survivor_id}

                async def empty():
                    return await redis.xlen(request_stream(model)) == 0

                await eventually(empty)
                row = {
                    "reclaim_idle_ms": idle_ms,
                    "reclaim_interval_ms": 50,
                    "fake": {"base_ms": 200, "per_item_ms": 1, "jitter_ms": 0},
                    "batch_size": 4,
                    "prefetch_bound": 4,
                    "request_timeout_ms": 6000,
                    "kill_to_all_responses_ms": recovered_ms,
                    "before_kill_pending": before,
                    "after_kill_pending": after_kill["pending"],
                    "after_recovery_pending": (await redis.xpending(request_stream(model), GROUP))[
                        "pending"
                    ],
                    "after_recovery_xlen": await redis.xlen(request_stream(model)),
                    "statuses": [r.status_code for r in responses],
                    "reclaim_events": [
                        e for e in survivor.events() if e.get("event") == "request_reclaimed"
                    ],
                    "meta": [r.json()["meta"] for r in responses],
                }
                log.info("E4_trial", idle_ms=idle_ms, recovery_ms=recovered_ms)
                return row
    finally:
        await victim.stop(kill=True)
        if survivor:
            await survivor.stop()
        await redis.delete(request_stream(model))
        await redis.aclose()


def percentile(values: list[float], quantile: float) -> float | None:
    if not values:
        return None
    return sorted(values)[max(0, int(len(values) * quantile + 0.999999) - 1)]


async def overload_trial(url: str, root: Path, enabled: bool) -> dict[str, Any]:
    settings = settings_for(
        root,
        url,
        base_ms=60,
        idle_ms=2000,
        deadline_ms=450,
        depth=8,
        batch=4,
        admission_enabled=enabled,
    )
    model = settings.worker_model
    redis = create_redis(settings)
    worker = await asyncio.to_thread(WorkerProcess, settings, root)
    app = create_app(settings)
    loop = asyncio.get_running_loop()
    try:
        await worker.ready(redis, model)
        async with app.router.lifespan_context(app):
            async with client_for(app, settings) as client:
                records = []
                depths = []
                done = asyncio.Event()

                async def sample() -> None:
                    while not done.is_set():
                        depths.append(await redis.xlen(request_stream(model)))
                        await asyncio.sleep(0.005)

                async def submit(index: int) -> None:
                    start = loop.time()
                    response = await client.post(
                        f"/v1/infer/{model}", json={"input": f"overload-{index}"}
                    )
                    records.append(
                        {
                            "index": index,
                            "status": response.status_code,
                            "latency_ms": (loop.time() - start) * 1000,
                            "retry_after": response.headers.get("Retry-After"),
                            "error_code": response.json().get("error", {}).get("code"),
                        }
                    )

                sampler = asyncio.create_task(sample())
                started = loop.time()
                try:
                    # Fixed burst: both policies receive the same callers.
                    await asyncio.gather(*(submit(index) for index in range(120)))
                finally:
                    done.set()
                    await sampler
                elapsed = loop.time() - started
                successes = [r["latency_ms"] for r in records if r["status"] == 200]
                rejects = [r["latency_ms"] for r in records if r["status"] == 503]
                timeouts = [r["latency_ms"] for r in records if r["status"] == 504]

                async def drained():
                    return await redis.xlen(request_stream(model)) == 0

                await eventually(drained)
                row = {
                    "admission_enabled": enabled,
                    "requests": 120,
                    "max_queue_depth": 8,
                    "request_timeout_ms": 450,
                    "fake": {"base_ms": 60, "per_item_ms": 1, "jitter_ms": 0},
                    "max_batch_size": 4,
                    "prefetch_bound": 4,
                    "elapsed_s": elapsed,
                    "statuses": dict(Counter(str(r["status"]) for r in records)),
                    "observed_max_xlen": max(depths, default=0),
                    "success_p50_ms": percentile(successes, 0.5),
                    "success_p95_ms": percentile(successes, 0.95),
                    "reject_p95_ms": percentile(rejects, 0.95),
                    "timeout_p95_ms": percentile(timeouts, 0.95),
                    "after_drain_pending": (await redis.xpending(request_stream(model), GROUP))[
                        "pending"
                    ],
                    "records": sorted(records, key=lambda r: r["index"]),
                }
                log.info(
                    "E5_trial",
                    enabled=enabled,
                    statuses=row["statuses"],
                    max_xlen=row["observed_max_xlen"],
                    success_p95=row["success_p95_ms"],
                )
                return row
    finally:
        await worker.stop()
        await redis.delete(request_stream(model))
        await redis.aclose()


async def run(args: argparse.Namespace, url: str, root: Path) -> None:
    redis = create_redis(Settings(redis_url=url, api_keys=["disposable"]))
    version = (await redis.info("server"))["redis_version"]
    await redis.aclose()
    report = {"metadata": await asyncio.to_thread(metadata), "redis_version": version}
    if args.experiment == "E4":
        report["rows"] = [
            await recovery_trial(url, root, idle) for idle in (500, 1000, 2000) for _ in range(3)
        ]
        report["summary"] = [
            {
                "reclaim_idle_ms": idle,
                "median_recovery_ms": statistics.median(
                    r["kill_to_all_responses_ms"]
                    for r in report["rows"]
                    if r["reclaim_idle_ms"] == idle
                ),
            }
            for idle in (500, 1000, 2000)
        ]
    else:
        report["rows"] = [
            await overload_trial(url, root, enabled) for enabled in (True, False) for _ in range(3)
        ]
    await asyncio.to_thread(args.output.parent.mkdir, parents=True, exist_ok=True)
    await asyncio.to_thread(args.output.write_text, json.dumps(report, indent=2) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("experiment", choices=["E4", "E5"])
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="reliability-") as root:
        with RedisContainer("redis:7.4") as redis:
            url = f"redis://{redis.get_container_host_ip()}:{redis.get_exposed_port(6379)}/0"
            asyncio.run(run(args, url, Path(root)))


if __name__ == "__main__":
    main()
