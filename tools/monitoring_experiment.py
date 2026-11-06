"""E7: real HTTP and dashboard-query evidence; fake model compute is simulated."""

import asyncio
import json
import platform
import subprocess
import time
from pathlib import Path

import httpx
import structlog
import yaml

log = structlog.get_logger()
COMPOSE = ["docker", "compose", "-f", "deploy/docker/docker-compose.yml"]


async def load(seconds: int = 75, rate: int = 80) -> dict:
    statuses: dict[str, int] = {}
    latencies = []
    async with httpx.AsyncClient(timeout=10, limits=httpx.Limits(max_connections=500)) as client:

        async def request() -> None:
            began = time.monotonic()
            r = await client.post(
                "http://localhost:8000/v1/infer/fake-classifier",
                headers={"X-API-Key": "phase1-verification-key"},
                json={"input": "E7 synthetic traffic"},
            )
            statuses[str(r.status_code)] = statuses.get(str(r.status_code), 0) + 1
            if r.status_code == 200:
                latencies.append((time.monotonic() - began) * 1000)

        started = time.monotonic()
        tasks = []
        for i in range(seconds * rate):
            await asyncio.sleep(max(0, started + i / rate - time.monotonic()))
            tasks.append(asyncio.create_task(request()))
        await asyncio.gather(*tasks)
    return {
        "statuses": statuses,
        "success_p95_ms": sorted(latencies)[int(len(latencies) * 0.95)] if latencies else None,
    }


async def queries() -> dict:
    files = ["overview", "batching"]
    result = {}
    async with httpx.AsyncClient(timeout=10) as client:
        for name in files:
            dashboard = json.loads(
                await asyncio.to_thread(
                    Path(f"deploy/monitoring/grafana/dashboards/{name}.json").read_text
                )
            )
            for panel in dashboard["panels"]:
                expr = panel["targets"][0]["expr"]
                response = await client.get(
                    "http://localhost:9090/api/v1/query", params={"query": expr}
                )
                response.raise_for_status()
                result[name + "/" + panel["title"]] = {
                    "expr": expr,
                    "data": response.json()["data"]["result"],
                }
    return result


def main() -> None:
    report = {
        "simulated_compute": True,
        "date_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "hardware": platform.platform(),
        "commit": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        "prediction": (
            "Batch-size-one limits amortization: busy rises, batch size falls; "
            "compute duration is similar, queue wait and head age grow."
        ),
    }
    base = yaml.safe_load(Path("config/models.yaml").read_text())
    override = {
        "services": {
            "worker-fake": {"volumes": ["/tmp/infer-e7-models.yaml:/app/config/models.yaml:ro"]}
        }
    }
    Path("/tmp/infer-e7-compose.yml").write_text(yaml.safe_dump(override))
    try:
        for label, size in [("baseline", 32), ("bottleneck", 1)]:
            base["models"]["fake-classifier"]["max_batch_size"] = size
            Path("/tmp/infer-e7-models.yaml").write_text(yaml.safe_dump(base))
            subprocess.run(
                [
                    *COMPOSE,
                    "-f",
                    "/tmp/infer-e7-compose.yml",
                    "up",
                    "-d",
                    "--force-recreate",
                    "worker-fake",
                ],
                check=True,
                stdout=subprocess.DEVNULL,
            )

            async def measure() -> dict:
                await asyncio.sleep(
                    5
                )  # Fixed settling period is part of experiment, not a test assertion.
                evidence = await load()
                evidence["dashboard_queries"] = await queries()
                return evidence

            report[label] = asyncio.run(measure())
            log.info("e7_measured", scenario=label, statuses=report[label]["statuses"])
    finally:
        subprocess.run(
            [*COMPOSE, "up", "-d", "--force-recreate", "worker-fake"],
            check=True,
            stdout=subprocess.DEVNULL,
        )
    Path("docs/results/e7-monitoring.json").write_text(json.dumps(report, indent=2) + "\n")


if __name__ == "__main__":
    main()
