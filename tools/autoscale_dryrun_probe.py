import asyncio
import json
from pathlib import Path

import httpx

from tools.monitoring_experiment import load


async def main() -> None:
    report = {
        "simulated_compute": True,
        "actual_scaling": False,
        "http": await load(seconds=20, rate=80),
    }
    await asyncio.sleep(6)
    async with httpx.AsyncClient(timeout=10) as client:
        for name in (
            "autoscaler_desired_replicas",
            "autoscaler_current_replicas",
            "autoscaler_decisions_total",
        ):
            response = await client.get(
                "http://localhost:9090/api/v1/query", params={"query": name}
            )
            response.raise_for_status()
            report[name] = response.json()["data"]["result"]
    await asyncio.to_thread(
        Path("docs/results/p6-dryrun.json").write_text, json.dumps(report, indent=2) + "\n"
    )


if __name__ == "__main__":
    asyncio.run(main())
