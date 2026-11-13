"""Compare exact latency percentiles with what the Prometheus histogram can tell you.

Used by docs/learning/05-observability.md. Needs the Compose stack running. Sends
300 requests (20 concurrent) to the fake classifier, diffs the gateway's
cumulative histogram buckets before and after, and records exact percentiles
from each response's ``meta.total_ms``.

    INFER_LEARNING_URL=http://localhost:8000 INFER_LEARNING_KEY=<key> \
        uv run python -m tools.learning.histogram_vs_exact
"""

import asyncio
import json
import os
import re
import statistics
import sys
from typing import Any

import httpx

URL = os.environ.get("INFER_LEARNING_URL", "http://localhost:8000")
KEY = os.environ.get("INFER_LEARNING_KEY", "")
BUCKET = re.compile(
    r'^gateway_request_duration_seconds_bucket\{endpoint="/v1/infer/\{model\}",'
    r'le="([^"]+)",model="fake-classifier"\} (\S+)$',
    re.M,
)


def buckets(text: str) -> dict[str, float]:
    return {match.group(1): float(match.group(2)) for match in BUCKET.finditer(text)}


def estimate(cumulative: dict[str, float], quantile: float) -> float:
    """Linear interpolation inside the bucket, the same idea as histogram_quantile()."""
    bounds = sorted((float(le), count) for le, count in cumulative.items() if le != "+Inf")
    rank = quantile * cumulative["+Inf"]
    lower_bound, lower_count = 0.0, 0.0
    for upper_bound, count in bounds:
        if count >= rank:
            share = (rank - lower_count) / (count - lower_count) if count > lower_count else 0
            return lower_bound + share * (upper_bound - lower_bound)
        lower_bound, lower_count = upper_bound, count
    return bounds[-1][0]


async def run() -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=10) as client:
        before = buckets((await client.get(URL + "/metrics")).text)
        limit = asyncio.Semaphore(20)

        async def one(index: int) -> dict[str, Any]:
            async with limit:
                response = await client.post(
                    URL + "/v1/infer/fake-classifier",
                    headers={"X-API-Key": KEY},
                    json={"input": f"req {index}"},
                )
                meta: dict[str, Any] = response.json()["meta"]
                return meta

        metas = await asyncio.gather(*(one(index) for index in range(300)))
        after = buckets((await client.get(URL + "/metrics")).text)
    delta = {le: after[le] - before[le] for le in after}
    totals = sorted(meta["total_ms"] for meta in metas)

    def exact(quantile: float) -> float:
        return totals[min(len(totals) - 1, round(quantile * len(totals)) - 1)]

    return {
        "bucket_delta": delta,
        "exact_ms": {"p50": exact(0.5), "p95": exact(0.95), "p99": exact(0.99)},
        "histogram_estimate_ms": {
            "p50": estimate(delta, 0.5) * 1000,
            "p95": estimate(delta, 0.95) * 1000,
            "p99": estimate(delta, 0.99) * 1000,
        },
        "mean_breakdown_ms": {
            name: statistics.mean(meta[name] for meta in metas)
            for name in ("queue_ms", "batch_wait_ms", "inference_ms", "gateway_overhead_ms")
        },
        "mean_batch_size": statistics.mean(meta["batch_size"] for meta in metas),
    }


def main() -> None:
    if not KEY:
        sys.exit("Set INFER_LEARNING_KEY to one configured API key")
    sys.stdout.write(json.dumps(asyncio.run(run()), indent=1) + "\n")


if __name__ == "__main__":
    main()
