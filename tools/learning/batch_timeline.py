"""Drive the real DynamicBatcher with a scripted arrival pattern and record what it did.

Used by docs/learning/02-dynamic-batching.md. No Redis: arrivals are submitted
straight into the batcher, and the FakeRunner (jitter disabled) simulates cost as
``base_ms + per_item_ms * batch_size``. Times are wall-clock on the machine that
runs it, so expect a few milliseconds of scheduling noise.

    uv run python -m tools.learning.batch_timeline
"""

import asyncio
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from infer_platform.common.settings import FakeConfig
from infer_platform.worker.batcher import BatchItem, BatchResult, DynamicBatcher
from infer_platform.worker.runners.fake import FakeRunner

ARRIVALS_MS = [0, 3, 7, 12, 15, 18, 20, 25, 30, 100]
MAX_BATCH_SIZE = 4
MAX_WAIT_MS = 10
COST = FakeConfig(base_ms=20, per_item_ms=2, jitter_ms=0)


async def simulate() -> dict[str, Any]:
    start = time.monotonic()
    rows: list[dict[str, Any]] = []
    batches: list[dict[str, float | int]] = []

    async def prepare(item: BatchItem) -> dict[str, Any]:
        return {"input": item.payload}

    async def finish(item: BatchItem, result: BatchResult) -> None:
        rows.append(
            {
                "request": item.request_id,
                "arrived_ms": round(item.context, 1),
                "batch_size": result.batch_size,
                "batch_wait_ms": round(result.batch_wait_ms, 1),
                "inference_ms": round(result.inference_ms, 1),
                "done_ms": round((time.monotonic() - start) * 1000, 1),
            }
        )

    def observe(size: int, seconds: float) -> None:
        ended = (time.monotonic() - start) * 1000
        batches.append(
            {"size": size, "start_ms": round(ended - seconds * 1000, 1), "end_ms": round(ended, 1)}
        )

    executor = ThreadPoolExecutor(max_workers=1)
    batcher = DynamicBatcher(
        FakeRunner(COST), executor, prepare, finish, MAX_BATCH_SIZE, MAX_WAIT_MS, observe=observe
    )
    runner = asyncio.create_task(batcher.run())
    for index, at_ms in enumerate(ARRIVALS_MS):
        await asyncio.sleep(max(0, start + at_ms / 1000 - time.monotonic()))
        now = time.monotonic()
        await batcher.submit(BatchItem(f"r{index + 1}", "x", now, context=(now - start) * 1000))
    await asyncio.sleep(0.2)
    batcher.close()
    await runner
    executor.shutdown()
    return {
        "config": {
            "max_batch_size": MAX_BATCH_SIZE,
            "max_wait_ms": MAX_WAIT_MS,
            "fake_cost": COST.model_dump(),
            "arrivals_ms": ARRIVALS_MS,
        },
        "batches": batches,
        "items": sorted(rows, key=lambda row: int(row["request"][1:])),
    }


def main() -> None:
    result = asyncio.run(simulate())
    out = Path("docs/results/learning/batch-timeline.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2) + "\n")
    for batch in result["batches"]:
        size, start, end = batch["size"], batch["start_ms"], batch["end_ms"]
        sys.stdout.write(f"batch size={size} ran {start}-{end} ms\n")
    for row in result["items"]:
        sys.stdout.write(json.dumps(row) + "\n")


if __name__ == "__main__":
    main()
