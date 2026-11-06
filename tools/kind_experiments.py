"""E11-E13 on a dedicated real kind cluster; model compute is simulated."""

import asyncio
import json
import platform
import subprocess
import time
from pathlib import Path

import httpx
import structlog
import yaml

from tools.reliability import eventually

log = structlog.get_logger()
KUBE = ["kubectl", "--context", "kind-infer-platform", "-n", "infer-platform"]


def command(*args: str, data: str | None = None) -> str:
    return subprocess.check_output([*KUBE, *args], input=data, text=True, timeout=180)


async def kube(*args: str, data: str | None = None) -> str:
    return await asyncio.to_thread(command, *args, data=data)


async def replicas() -> dict:
    deployment = json.loads(await kube("get", "deployment", "worker-fake", "-o", "json"))
    return {
        "spec": deployment["spec"]["replicas"],
        "ready": deployment.get("status", {}).get("readyReplicas", 0),
        "available": deployment.get("status", {}).get("availableReplicas", 0),
    }


async def roll() -> None:
    await kube("rollout", "status", "deployment/worker-fake", "--timeout=120s")


async def set_models(text: str) -> None:
    cm = {
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "metadata": {"name": "platform-models", "namespace": "infer-platform"},
        "data": {"models.yaml": text},
    }
    await kube("apply", "-f", "-", data=json.dumps(cm))
    await kube("rollout", "restart", "deployment/worker-fake")
    await roll()


async def load(seconds: int, rate: int, action=None) -> dict:
    records = []
    samples = []
    started = time.monotonic()
    finished = asyncio.Event()

    async def sample() -> None:
        while not finished.is_set():
            samples.append({"elapsed_s": time.monotonic() - started, **await replicas()})
            await asyncio.sleep(1)

    async def act() -> dict:
        await asyncio.sleep(4)
        return await action() if action else {}

    monitor = asyncio.create_task(sample())
    actor = asyncio.create_task(act())
    try:
        async with httpx.AsyncClient(
            timeout=40, limits=httpx.Limits(max_connections=500)
        ) as client:

            async def request(index: int) -> None:
                began = time.monotonic()
                try:
                    response = await client.post(
                        "http://localhost:8001/v1/infer/fake-classifier",
                        headers={"X-API-Key": "phase1-verification-key"},
                        json={"input": f"kind synthetic request {index}"},
                    )
                    data = response.json()
                    records.append(
                        {
                            "index": index,
                            "status": response.status_code,
                            "duration_ms": (time.monotonic() - began) * 1000,
                            "request_id": data.get("request_id"),
                            "meta": data.get("meta"),
                            "error": data.get("error"),
                        }
                    )
                except httpx.HTTPError as exc:
                    records.append(
                        {"index": index, "status": "transport_error", "type": type(exc).__name__}
                    )

            tasks = []
            for i in range(seconds * rate):
                await asyncio.sleep(max(0, started + i / rate - time.monotonic()))
                tasks.append(asyncio.create_task(request(i)))
            await asyncio.gather(*tasks)
        outcome = await actor
        statuses = {
            str(s): sum(r["status"] == s for r in records) for s in {r["status"] for r in records}
        }
        return {"statuses": statuses, "records": records, "samples": samples, "action": outcome}
    finally:
        finished.set()
        monitor.cancel()
        if not actor.done():
            actor.cancel()
        await asyncio.gather(monitor, actor, return_exceptions=True)


async def rolling() -> dict:
    await kube("rollout", "restart", "deployment/worker-fake")
    await roll()
    return {"operation": "rolling restart", "finished_at_monotonic": time.monotonic()}


async def kill_pending() -> dict:
    async def candidate():
        entries = json.loads(
            await kube(
                "exec",
                "statefulset/redis",
                "--",
                "redis-cli",
                "--json",
                "XPENDING",
                "req:fake-classifier",
                "workers",
                "-",
                "+",
                "20",
            )
        )
        return next((entry for entry in entries if entry[2] < 200), None)

    entry = await eventually(candidate, seconds=15)
    entry_id, consumer, _, _ = entry
    pod = consumer.rsplit("-", 1)[0]
    raw = json.loads(
        await kube(
            "exec",
            "statefulset/redis",
            "--",
            "redis-cli",
            "--json",
            "XRANGE",
            "req:fake-classifier",
            entry_id,
            entry_id,
        )
    )
    assert raw, "Pending entry finished before kill; rerun with the documented slow configuration"
    fields = dict(zip(raw[0][1][::2], raw[0][1][1::2], strict=True))
    # A namespace-init process can ignore signals sent from a sibling inside
    # that same PID namespace. Send SIGKILL from the kind node's ancestor
    # namespace, targeting only this dedicated cluster's selected container.
    victim = json.loads(await kube("get", "pod", pod, "-o", "json"))
    node = victim["spec"]["nodeName"]
    assert node.startswith("infer-platform-"), "Refusing to target another cluster"
    container_id = next(
        c["containerID"].split("://", 1)[1]
        for c in victim["status"]["containerStatuses"]
        if c["name"] == "worker-fake"
    )
    details = json.loads(
        await asyncio.to_thread(
            subprocess.check_output,
            ["docker", "exec", node, "crictl", "inspect", container_id],
            text=True,
            timeout=10,
        )
    )
    node_pid = int(details["info"]["pid"])
    assert node_pid > 1
    await asyncio.to_thread(
        subprocess.run,
        ["docker", "exec", node, "kill", "-9", str(node_pid)],
        check=True,
        timeout=10,
    )
    await kube("delete", "pod", pod, "--grace-period=0", "--force", "--wait=false")
    return {
        "operation": "node-namespace SIGKILL then force-delete pending worker pod",
        "node": node,
        "container_id": container_id,
        "node_pid": node_pid,
        "pod": pod,
        "entry_id": entry_id,
        "request_id": fields["request_id"],
        "consumer": consumer,
        "pending_idle_at_selection_ms": entry[2],
    }


async def main() -> None:
    report = {
        "simulated_compute": True,
        "real_kubernetes": True,
        "date_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "host": platform.platform(),
        "commit": await asyncio.to_thread(
            subprocess.check_output, ["git", "rev-parse", "HEAD"], text=True
        ),
        "cluster": json.loads(await kube("version", "-o", "json")),
        "prediction": (
            "Rolling drain should preserve requests; forced pending-pod death should reclaim "
            "within the 30 s deadline; queue pressure should grow replicas and "
            "stabilization should delay shrink."
        ),
    }
    original = json.loads(await kube("get", "configmap", "platform-models", "-o", "json"))["data"][
        "models.yaml"
    ]
    path = Path("docs/results/kind-experiments.json")
    try:
        await kube("scale", "deployment/autoscaler", "--replicas=0")
        await kube("wait", "--for=delete", "pod", "-l", "app=autoscaler", "--timeout=60s")
        await kube("scale", "deployment/worker-fake", "--replicas=2")
        await roll()
        report["E11"] = await load(20, 60, rolling)
        log.info("E11_finished", statuses=report["E11"]["statuses"])
        slow = yaml.safe_load(original)
        slow["models"]["fake-classifier"]["fake"]["base_ms"] = 800
        await set_models(yaml.safe_dump(slow))
        report["E12"] = await load(20, 6, kill_pending)
        killed = report["E12"]["action"]
        recovered = next(
            (r for r in report["E12"]["records"] if r.get("request_id") == killed["request_id"]),
            None,
        )
        report["E12"]["killed_pending_request_outcome"] = recovered
        report["E12"]["reclaimed_by_different_worker"] = bool(
            recovered
            and recovered["status"] == 200
            and not recovered["meta"]["worker_id"].startswith(killed["pod"])
        )
        assert report["E12"]["reclaimed_by_different_worker"], (
            "Need direct evidence the killed pending request was recovered elsewhere"
        )
        log.info("E12_finished", statuses=report["E12"]["statuses"])
        await set_models(original)
        await kube("scale", "deployment/worker-fake", "--replicas=1")
        await roll()
        await kube("scale", "deployment/autoscaler", "--replicas=1")
        await kube("rollout", "status", "deployment/autoscaler", "--timeout=120s")
        report["E13"] = await load(35, 120)
        peak = max(s["spec"] for s in report["E13"]["samples"])
        assert peak > 1, "Autoscaler never scaled up"
        cooldown_started = time.monotonic()
        down = []
        async with asyncio.timeout(100):
            while True:
                state = await replicas()
                down.append({"elapsed_after_load_s": time.monotonic() - cooldown_started, **state})
                if state["spec"] == 1 and state["ready"] == 1:
                    break
                await asyncio.sleep(2)
        report["E13"]["cooldown_samples"] = down
        report["E13"]["peak_desired"] = peak
        report["E13"]["down_to_one_after_load_s"] = time.monotonic() - cooldown_started
        log.info("E13_finished", statuses=report["E13"]["statuses"], peak=peak)
    finally:
        await asyncio.to_thread(path.write_text, json.dumps(report, indent=2) + "\n")
        await set_models(original)
        await kube("scale", "deployment/autoscaler", "--replicas=1")


if __name__ == "__main__":
    asyncio.run(main())
