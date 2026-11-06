"""HTTP verification of three Compose fake replicas; no prompts or API keys logged."""

import argparse
import asyncio
import json
import os
from collections import Counter
from pathlib import Path

import httpx
import structlog

from tools.reliability import eventually


async def run(output: Path) -> None:
    async with httpx.AsyncClient(
        base_url="http://localhost:8000",
        timeout=10,
        headers={"X-API-Key": os.environ["INFER_SMOKE_API_KEY"]},
    ) as client:

        async def ready():
            listing = (await client.get("/v1/models")).json()["models"]
            return next(
                (m for m in listing if m["id"] == "fake-classifier" and m["live_workers"] == 3),
                None,
            )

        listing = await eventually(ready, seconds=30)
        responses = await asyncio.gather(
            *(
                client.post(
                    "/v1/infer/fake-classifier",
                    json={"input": f"synthetic-{index}"},
                )
                for index in range(120)
            )
        )
        assert all(r.status_code == 200 for r in responses)
        counts = Counter(r.json()["meta"]["worker_id"] for r in responses)
        assert len(counts) == 3, counts
        report = {
            "live_workers": listing["live_workers"],
            "successful_requests": 120,
            "requests_per_worker": dict(counts),
            "simulated_compute": True,
        }
        await asyncio.to_thread(output.parent.mkdir, parents=True, exist_ok=True)
        await asyncio.to_thread(output.write_text, json.dumps(report, indent=2) + "\n")
        structlog.get_logger().info("distribution_verified", **report)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    asyncio.run(run(parser.parse_args().output))


if __name__ == "__main__":
    main()
