"""Bounded open-loop soak of simulated and real CPU models on the local kind stack."""

import argparse
import asyncio
import json
import platform
import subprocess
import time
from pathlib import Path

from tools.bench.e2e import trial


async def main(seconds: int, output: Path) -> None:
    started = time.time()
    results = await asyncio.gather(
        trial("fake-classifier", 20, seconds), trial("sentiment", 2, seconds)
    )
    for result in results:
        result.pop("records")
        result.pop("inflight_samples")
    report = {
        "started_unix": started,
        "hardware": platform.platform(),
        "commit": (
            await asyncio.to_thread(
                subprocess.check_output, ["git", "rev-parse", "HEAD"], text=True, timeout=10
            )
        ).strip(),
        "duration_target_s": seconds,
        "trials": results,
        "scope": (
            "Bounded local HTTP soak; fake classifier compute is simulated, sentiment is real CPU."
        ),
    }
    await asyncio.to_thread(output.write_text, json.dumps(report, indent=2) + "\n")
    if any(set(result["statuses"]) != {"200"} for result in results):
        raise RuntimeError("Soak had failed requests; inspect the retained report")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--seconds", type=int, default=900)
    parser.add_argument("--output", type=Path, default=Path("docs/results/final-soak.json"))
    args = parser.parse_args()
    if args.seconds < 1:
        parser.error("--seconds must be positive")
    asyncio.run(main(args.seconds, args.output))
