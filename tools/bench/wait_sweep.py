"""E3: measured arrivals and completion times through the actual in-memory batcher."""

import argparse
import asyncio
import json
import statistics
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import structlog

from infer_platform.common.schemas import InferInput
from infer_platform.worker.batcher import BatchItem, BatchResult, DynamicBatcher
from infer_platform.worker.runners.base import ModelRunner
from tools.bench.shared import TEXT, metadata, runner_for


def percentile(values: list[float], quantile: float) -> float:
    return sorted(values)[max(0, int(len(values) * quantile + 0.999999) - 1)]


async def trial(runner: ModelRunner, wait_ms: float, period_s: float, count: int) -> dict[str, Any]:
    loop = asyncio.get_running_loop()
    records: list[dict[str, Any]] = []
    executor = ThreadPoolExecutor(max_workers=1)

    async def prepare(item: BatchItem) -> dict[str, Any]:
        return InferInput.model_validate_json(item.payload).model_dump()

    async def finish(item: BatchItem, result: BatchResult) -> None:
        assert result.error_code is None
        records.append(
            {
                "id": item.request_id,
                "latency_ms": (loop.time() - item.enqueued_at) * 1000,
                "batch_wait_ms": result.batch_wait_ms,
                "inference_ms": result.inference_ms,
                "batch_size": result.batch_size,
            }
        )

    batcher = DynamicBatcher(runner, executor, prepare, finish, 16, wait_ms)
    task = asyncio.create_task(batcher.run())
    start = loop.time()
    admission_waits = []
    try:
        for index in range(count):
            await asyncio.sleep(max(0, start + index * period_s - loop.time()))
            arrival = loop.time()
            await batcher.submit(
                BatchItem(str(index), InferInput(input=TEXT).model_dump_json(), arrival)
            )
            admission_waits.append((loop.time() - arrival) * 1000)
        # Leave the last batch under the normal timer rule; close only after all work completes.
        await batcher.queue.join()
        elapsed = loop.time() - start
        batcher.close()
        await task
    finally:
        batcher.close()
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await asyncio.to_thread(executor.shutdown, wait=True)
    latencies = [record["latency_ms"] for record in records]
    waits = [record["batch_wait_ms"] for record in records]
    return {
        "max_wait_ms": wait_ms,
        "arrival_period_s": period_s,
        "count": count,
        "elapsed_s": elapsed,
        "completion_items_s": count / elapsed,
        "latency_p50_ms": percentile(latencies, 0.5),
        "latency_p95_ms": percentile(latencies, 0.95),
        "mean_batch_wait_ms": statistics.mean(waits),
        "mean_batch_size_seen_by_item": statistics.mean(r["batch_size"] for r in records),
        "max_admission_wait_ms": max(admission_waits),
        "records": records,
    }


async def run(args: argparse.Namespace) -> None:
    runner = runner_for(args.backend, args.threads)
    await asyncio.to_thread(runner.load)
    await asyncio.to_thread(runner.warmup)
    rows = []
    for regime, period, count in [("low", 0.1, 24), ("high", 0.001, 128)]:
        for wait in (0, 2, 10, 30):
            row = await trial(runner, wait, period, count)
            row["load"] = regime
            rows.append(row)
            structlog.get_logger().info(
                "E3_trial",
                backend=args.backend,
                **{key: value for key, value in row.items() if key != "records"},
            )
    meta = await asyncio.to_thread(metadata, runner)
    await asyncio.to_thread(args.output.parent.mkdir, parents=True, exist_ok=True)
    await asyncio.to_thread(
        args.output.write_text,
        json.dumps(
            {
                "experiment": "E3",
                "backend": args.backend,
                "simulated": args.backend == "fake",
                "threads": args.threads,
                "max_batch_size": 16,
                "prefetch_bound": 32,
                "metadata": meta,
                "rows": rows,
            },
            indent=2,
        )
        + "\n",
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--backend", choices=["fake", "torch"], required=True)
    parser.add_argument("--threads", type=int, default=1)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
