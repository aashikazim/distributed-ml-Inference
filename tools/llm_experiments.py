"""E6a/E6b through real TCP gateway, Redis and upstream; fake data explicitly simulated."""

import argparse
import asyncio
import json
import platform
import statistics
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

import httpx
import structlog
from testcontainers.community.redis import RedisContainer

from infer_platform.common.redis import create_redis
from infer_platform.common.sse import sse_data
from infer_platform.worker.llm_worker import LLMWorker
from tools.llm_lab import LocalServer, llm_settings

PROMPT = (
    "Count integers starting at 1, separated by commas, until you reach 1000. Do not stop early."
)
log = structlog.get_logger()


def metadata(backend: str) -> dict[str, Any]:
    from datetime import UTC, datetime

    return {
        "date_utc": datetime.now(UTC).isoformat(),
        "backend": backend,
        "simulated": backend == "fake",
        "python": platform.python_version(),
        "platform": platform.platform(),
        "cpu_inventory": subprocess.check_output(["lscpu"], text=True),
        "commit": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        "tracked_changes": subprocess.check_output(
            ["git", "diff", "--name-only"], text=True
        ).splitlines(),
        "prompt": PROMPT,
        "gpu_query_on_client_host": (
            subprocess.check_output(
                ["nvidia-smi", "--query-gpu=name,driver_version", "--format=csv,noheader"],
                text=True,
            ).strip()
            if backend == "vllm"
            else None
        ),
    }


def percentile(values: list[float], quantile: float) -> float:
    return sorted(values)[max(0, int(len(values) * quantile + 0.999999) - 1)]


async def trial(
    url: str,
    root: Path,
    upstream: str,
    concurrency: int,
    window_ms: float,
    requests: int,
    tokens: int,
) -> dict[str, Any]:
    cfg = llm_settings(root, url, upstream, concurrency, window_ms, deadline_ms=60000, cap=512)
    config = cfg.load_models()[cfg.worker_model]
    config.llm.http_timeout_s = 60
    redis = create_redis(cfg)
    worker = LLMWorker(redis, cfg.worker_model, config, cfg)
    worker_task = asyncio.create_task(worker.run())
    gateway = None
    try:
        await asyncio.wait_for(worker.ready.wait(), 20)
        gateway = await asyncio.to_thread(
            LocalServer,
            root,
            "infer_platform.gateway.app:create_app",
            {
                "INFER_REDIS_URL": url,
                "INFER_MODELS_PATH": str(cfg.models_path),
                "INFER_API_KEYS": json.dumps([cfg.api_keys[0].get_secret_value()]),
            },
        )
        await gateway.ready("/readyz")
        async with httpx.AsyncClient(
            timeout=65,
            limits=httpx.Limits(
                max_connections=requests,
                max_keepalive_connections=requests,
            ),
        ) as client:

            async def request(index: int) -> dict[str, Any]:
                started = time.monotonic()
                ttft = None
                content = []
                meta = usage = None
                finished = False
                finish_reason = None
                events = 0
                async with client.stream(
                    "POST",
                    gateway.url + "/v1/chat/completions",
                    headers={"X-API-Key": cfg.api_keys[0].get_secret_value()},
                    json={
                        "model": cfg.worker_model,
                        "messages": [{"role": "user", "content": PROMPT}],
                        "max_tokens": tokens,
                        "temperature": 0.0,
                        "stream": True,
                    },
                ) as response:
                    assert response.status_code == 200, await response.aread()
                    async for raw in sse_data(response):
                        if raw == "[DONE]":
                            finished = True
                            break
                        event = json.loads(raw)
                        assert "error" not in event, event
                        events += 1
                        if event.get("usage"):
                            usage, meta = event["usage"], event["meta"]
                        for choice in event["choices"]:
                            delta = choice["delta"].get("content")
                            if delta:
                                ttft = (
                                    ttft
                                    if ttft is not None
                                    else (time.monotonic() - started) * 1000
                                )
                                content.append(delta)
                            if choice.get("finish_reason"):
                                finish_reason = choice["finish_reason"]
                assert finished and usage and meta and content
                return {
                    "index": index,
                    "client_ttft_ms": ttft,
                    "duration_ms": (time.monotonic() - started) * 1000,
                    "usage": usage,
                    "meta": meta,
                    "finish_reason": finish_reason,
                    "events": events,
                    "text": "".join(content),
                }

            started = time.monotonic()
            records = await asyncio.gather(*(request(i) for i in range(requests)))
            elapsed = time.monotonic() - started
        tpot = [r["meta"]["tpot_ms"] for r in records if r["meta"]["tpot_ms"] is not None]
        return {
            "max_concurrent": concurrency,
            "coalesce_ms": window_ms,
            "requests": requests,
            "max_tokens": tokens,
            "elapsed_s": elapsed,
            "completion_tokens": sum(r["usage"]["completion_tokens"] for r in records),
            "tokens_s": sum(r["usage"]["completion_tokens"] for r in records) / elapsed,
            "client_ttft_p50_ms": statistics.median(r["client_ttft_ms"] for r in records),
            "client_ttft_p95_ms": percentile([r["client_ttft_ms"] for r in records], 0.95),
            "worker_ttft_p50_ms": statistics.median(r["meta"]["ttft_ms"] for r in records),
            "worker_tpot_p50_ms": statistics.median(tpot) if tpot else None,
            "chunk_xadds": sum(r["meta"]["chunk_writes"] for r in records),
            "records": records,
        }
    finally:
        if gateway:
            await gateway.close()
        worker.stop.set()
        await asyncio.wait_for(worker_task, 10)
        await redis.delete("req:" + cfg.worker_model)
        await redis.aclose()


async def run(args: argparse.Namespace, url: str, root: Path) -> None:
    fake = None
    try:
        if args.backend == "fake":
            fake = await asyncio.to_thread(
                LocalServer,
                root,
                "infer_platform.fake.openai_server:create_app",
                {
                    "INFER_FAKE_TTFT_MS": "40",
                    "INFER_FAKE_TOKEN_MS": "2",
                    "INFER_FAKE_TOKENS": "64",
                },
            )
            await fake.ready()
            upstream = fake.url
        else:
            upstream = args.upstream.rstrip("/")
        report = {"metadata": await asyncio.to_thread(metadata, args.backend), "rows": []}
        configs = (
            [(n, 20, 32) for n in (1, 2, 4, 8, 16)]
            if args.experiment == "E6a"
            else [(4, window, 64) for window in (0, 20)]
        )
        for concurrent, window, tokens in configs:
            for repeat in range(3):
                row = await trial(url, root, upstream, concurrent, window, 16, tokens)
                row["repeat"] = repeat + 1
                if args.backend == "fake":
                    expected = "".join(f"token{i} " for i in range(tokens))
                    assert all(r["text"] == expected for r in row["records"])
                report["rows"].append(row)
                log.info(
                    args.experiment,
                    backend=args.backend,
                    concurrent=concurrent,
                    coalesce_ms=window,
                    repeat=repeat + 1,
                    tokens_s=row["tokens_s"],
                    client_ttft_ms=row["client_ttft_p50_ms"],
                    chunk_xadds=row["chunk_xadds"],
                )
        if fake:
            report["fake_server_settings"] = {"ttft_ms": 40, "token_ms": 2, "tokens": 64}
        await asyncio.to_thread(args.output.parent.mkdir, parents=True, exist_ok=True)
        await asyncio.to_thread(args.output.write_text, json.dumps(report, indent=2) + "\n")
    finally:
        if fake:
            await fake.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("experiment", choices=["E6a", "E6b"])
    parser.add_argument("--backend", choices=["fake", "vllm"], required=True)
    parser.add_argument("--upstream", default="http://127.0.0.1:8100")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="llm-experiment-") as root:
        with RedisContainer("redis:7.4") as redis:
            url = f"redis://{redis.get_container_host_ip()}:{redis.get_exposed_port(6379)}/0"
            asyncio.run(run(args, url, Path(root)))


if __name__ == "__main__":
    main()
