"""Verify actual gateway/Redis/adapter/vLLM Pods and GPU disconnect cancellation."""

import asyncio
import json
import time
from pathlib import Path

import httpx
from prometheus_client.parser import text_string_to_metric_families

from infer_platform.common.sse import sse_data
from tools.kind_experiments import kube
from tools.reliability import eventually


def active(text: str) -> float:
    return sum(
        sample.value
        for family in text_string_to_metric_families(text)
        for sample in family.samples
        if sample.name == "vllm:num_requests_running"
    )


async def main() -> None:
    async with httpx.AsyncClient(timeout=90) as client:
        health = await client.get("http://localhost:18100/health")
        assert health.status_code == 200, "GPU engine must be healthy before this check"
        payload = {
            "model": "chat",
            "messages": [{"role": "user", "content": "Say hello briefly."}],
            "max_tokens": 32,
            "temperature": 0,
            "stream": False,
        }
        url = "http://localhost:8001/v1/chat/completions"
        headers = {"X-API-Key": "phase1-verification-key"}
        completion = await client.post(url, headers=headers, json=payload)
        assert completion.status_code == 200, completion.text
        data = completion.json()
        assert data["usage"]["completion_tokens"] > 0
        long_payload = dict(payload)
        long_payload["messages"] = [
            {
                "role": "user",
                "content": "Explain why inference workers pull requests from a queue. " * 100,
            }
        ]
        long_payload["max_tokens"] = 128
        long_response = await client.post(url, headers=headers, json=long_payload)
        assert long_response.status_code == 200, long_response.text
        long_data = long_response.json()
        assert long_data["usage"]["prompt_tokens"] > 900
        payload.update(
            stream=True,
            max_tokens=512,
            messages=[
                {
                    "role": "user",
                    "content": "Count from 1 to 1000, separated by commas. Do not stop early.",
                }
            ],
        )
        async with client.stream("POST", url, headers=headers, json=payload) as stream:
            assert stream.status_code == 200
            request_id = stream.headers["x-request-id"]
            async for event in sse_data(stream):
                chunk = json.loads(event)
                if any(c["delta"].get("content") for c in chunk["choices"]):
                    before = active((await client.get("http://localhost:18100/metrics")).text)
                    break
        assert before > 0, "Generation must still be running at disconnect"
        started = time.monotonic()

        async def aborted() -> bool:
            nonlocal outcome
            outcome = json.loads(
                await kube("exec", "redis-0", "--", "redis-cli", "GET", f"result:{request_id}")
                or "null"
            )
            return active((await client.get("http://localhost:18100/metrics")).text) == 0 and bool(
                outcome and outcome["status"] == "CANCELLED"
            )

        outcome = None
        await eventually(aborted, seconds=10)
        abort_ms = (time.monotonic() - started) * 1000
        pods = json.loads(await kube("get", "pods", "-l", "app=vllm", "-o", "json"))
        pod = pods["items"][0]
        report = {
            "date_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "scope": (
                "Actual kind gateway -> Redis -> LLM adapter -> GPU vLLM Pod; no fake compute."
            ),
            "gpu_resource_limit": pod["spec"]["containers"][0]["resources"]["limits"][
                "nvidia.com/gpu"
            ],
            "node": pod["spec"]["nodeName"],
            "engine_image_id": pod["status"]["containerStatuses"][0]["imageID"],
            "engine_requested_image": pod["spec"]["containers"][0]["image"],
            "model_revision": (
                await kube(
                    "exec",
                    pod["metadata"]["name"],
                    "--",
                    "cat",
                    "/models/hub/models--Qwen--Qwen2.5-0.5B-Instruct/refs/main",
                )
            ).strip(),
            "process_non_root_uid": pod["spec"]["securityContext"]["runAsUser"],
            "completion_usage": data["usage"],
            "long_context_usage": long_data["usage"],
            "completion_meta": data["meta"],
            "active_before_disconnect": before,
            "active_after_disconnect": 0,
            "disconnect_to_zero_and_cancelled_ms": abort_ms,
            "worker_outcome": outcome["status"],
        }
        await asyncio.to_thread(
            Path("docs/results/final-kind-gpu.json").write_text, json.dumps(report, indent=2) + "\n"
        )


if __name__ == "__main__":
    asyncio.run(main())
