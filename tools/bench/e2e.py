"""Open-loop local kind benchmark and independently sampled Little's Law check."""

import argparse
import asyncio
import json
import platform
import subprocess
import time
from pathlib import Path

import httpx
import structlog

from tools.kind_experiments import kube, roll

log = structlog.get_logger()


def percentile(values: list[float], q: float) -> float:
    return sorted(values)[max(0, int(len(values) * q + 0.999999) - 1)]


async def trial(model: str, rate: int, seconds: int) -> dict:
    records = []
    samples = []
    active = 0
    done = asyncio.Event()
    started = time.monotonic()

    async def sample() -> None:
        while not done.is_set():
            samples.append((time.monotonic() - started, active))
            await asyncio.sleep(0.005)

    sampler = asyncio.create_task(sample())
    try:
        async with httpx.AsyncClient(
            timeout=35, limits=httpx.Limits(max_connections=500)
        ) as client:

            async def request(index: int) -> None:
                nonlocal active
                began = time.monotonic()
                active += 1
                try:
                    response = await client.post(
                        f"http://localhost:8001/v1/infer/{model}",
                        headers={"X-API-Key": "phase1-verification-key"},
                        json={
                            "input": "I loved this movie."
                            if index % 2 == 0
                            else "This film was awful."
                        },
                    )
                    data = response.json()
                    records.append(
                        {
                            "status": response.status_code,
                            "duration_s": time.monotonic() - began,
                            "meta": data.get("meta"),
                            "output": data.get("output"),
                            "error": data.get("error"),
                        }
                    )
                finally:
                    active -= 1

            tasks = []
            for i in range(rate * seconds):
                await asyncio.sleep(max(0, started + i / rate - time.monotonic()))
                tasks.append(asyncio.create_task(request(i)))
            await asyncio.gather(*tasks)
        elapsed = time.monotonic() - started
    finally:
        done.set()
        sampler.cancel()
        await asyncio.gather(sampler, return_exceptions=True)
    samples.append((elapsed, 0))
    durations = [r["duration_s"] for r in records if r["status"] == 200]
    area = sum(a[1] * (b[0] - a[0]) for a, b in zip(samples, samples[1:], strict=False))
    mean = sum(durations) / len(durations)
    return {
        "model": model,
        "simulated_compute": model == "fake-classifier",
        "offered_rps": rate,
        "arrival_seconds": seconds,
        "elapsed_s": elapsed,
        "statuses": {
            str(s): sum(r["status"] == s for r in records) for s in {r["status"] for r in records}
        },
        "completed_rps": len(durations) / elapsed,
        "mean_s": mean,
        "p50_s": percentile(durations, 0.5),
        "p95_s": percentile(durations, 0.95),
        "p99_s": percentile(durations, 0.99),
        "sampled_mean_inflight": area / elapsed,
        "little_law_lambda_W": len(durations) / elapsed * mean,
        "records": records,
        "inflight_samples": samples,
    }


async def main(final: bool = False) -> None:
    report = {
        "date_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "host": platform.platform(),
        "cpu_inventory": await asyncio.to_thread(subprocess.check_output, ["lscpu"], text=True),
        "commit": await asyncio.to_thread(
            subprocess.check_output, ["git", "rev-parse", "HEAD"], text=True
        ),
        "method": (
            "Real kind, TCP HTTP, 2 fixed fake workers, 1 CPU sentiment worker, "
            "2 gateways; open-loop arrivals, three repeats; no GPU in this run."
        ),
    }
    if final:
        report["method"] = (
            "Final timing-contract spot check: one 20-second trial per model, fixed replicas"
        )
        report["tracked_source_changes"] = (
            await asyncio.to_thread(
                subprocess.check_output, ["git", "diff", "--name-only", "--", "src"], text=True
            )
        ).splitlines()
    pods = json.loads(await kube("get", "pods", "-o", "json"))["items"]
    report["serving_image_ids"] = {
        pod["metadata"]["name"]: [
            c["imageID"] for c in pod.get("status", {}).get("containerStatuses", [])
        ]
        for pod in pods
        if pod["metadata"].get("labels", {}).get("app")
        in {"gateway", "worker-fake", "worker-sentiment"}
    }
    prediction = {
        "date_utc": report["date_utc"],
        "model": "fake-classifier",
        "boundary": ("Client HTTP start through final body, including transport and local waiting"),
        "arrival_rps": 10,
        "assumed_mean_s": 0.067,
        "assumed_components_s": {
            "base": 0.050,
            "per_item": 0.002,
            "batch_wait": 0.010,
            "transport_allowance": 0.005,
        },
        "predicted_mean_inflight": 0.67,
        "simulated_model_cost": True,
        "note": (
            "Prediction recorded before traffic; "
            "measured sampling is independent of duration summation."
        ),
    }
    if not final:
        await asyncio.to_thread(
            Path("docs/results/p8-capacity-prediction.json").write_text,
            json.dumps(prediction, indent=2) + "\n",
        )
    await kube("scale", "deployment/autoscaler", "--replicas=0")
    await kube("wait", "--for=delete", "pod", "-l", "app=autoscaler", "--timeout=60s")
    await kube("scale", "deployment/worker-fake", "--replicas=2")
    await roll()
    try:
        report["trials"] = []
        cases = (
            [("fake-classifier", 40), ("sentiment", 5)]
            if final
            else [
                ("fake-classifier", 5),
                ("fake-classifier", 20),
                ("fake-classifier", 40),
                ("sentiment", 5),
            ]
        )
        for model, rate in cases:
            for repeat in range(1 if final else 3):
                result = await trial(model, rate, 20)
                result["repeat"] = repeat + 1
                report["trials"].append(result)
                log.info(
                    "benchmark_trial",
                    model=model,
                    rate=rate,
                    repeat=repeat + 1,
                    p95_s=result["p95_s"],
                    statuses=result["statuses"],
                )
        if not final:
            report["capacity_prediction"] = prediction
            report["capacity_measurement"] = await trial("fake-classifier", 10, 60)
        report["final_timing_contract_spot_check"] = final
        if final:
            targets = json.loads(
                await asyncio.to_thread(Path("docs/results/p8-slo-proposal.json").read_text)
            )["models"]
            for trial_result in report["trials"]:
                assert trial_result["p95_s"] <= targets[trial_result["model"]]["latency_target_s"]
                assert all(row["meta"]["total_ms"] is not None for row in trial_result["records"])
        assert all(r["status"] == 200 for t in report["trials"] for r in t["records"])
    finally:
        await asyncio.to_thread(
            Path(
                "docs/results/p8-e2e-final.json" if final else "docs/results/p8-e2e.json"
            ).write_text,
            json.dumps(report, indent=2) + "\n",
        )
        await kube("scale", "deployment/worker-fake", "--replicas=1")
        await kube("scale", "deployment/autoscaler", "--replicas=1")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--verify-final", action="store_true")
    asyncio.run(main(parser.parse_args().verify_final))
