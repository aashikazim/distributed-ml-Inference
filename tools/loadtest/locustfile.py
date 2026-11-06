"""Closed-loop user scenarios; fake classifier/chat compute is simulated."""

import json
import os

from locust import HttpUser, LoadTestShape, constant, task

SCENARIO = os.environ.get("INFER_LOAD_SCENARIO", "steady")
if SCENARIO not in {"steady", "ramp", "spike", "mixed"}:
    raise ValueError("Choose steady, ramp, spike or mixed")


class InferenceUser(HttpUser):
    wait_time = constant(0.05)

    def on_start(self) -> None:
        self.iteration = 0
        self.client.headers["X-API-Key"] = os.environ.get(
            "INFER_LOAD_API_KEY", "phase1-verification-key"
        )

    @task
    def inference(self) -> None:
        chat = SCENARIO == "mixed" and self.iteration % 4 == 3
        self.iteration += 1
        path = "/v1/chat/completions" if chat else "/v1/infer/fake-classifier"
        body = (
            {
                "model": "chat",
                "messages": [{"role": "user", "content": "Explain queues briefly."}],
                "max_tokens": 32,
                "stream": True,
            }
            if chat
            else {"input": "Locust synthetic classification"}
        )
        # Read the complete SSE body so Locust records stream lifetime, not
        # headers-only latency. Incremental TTFT is measured by the E6 harness.
        with self.client.post(
            path, json=body, catch_response=True, timeout=35, name=path
        ) as response:
            if response.status_code != 200:
                response.failure(f"HTTP {response.status_code}")
            elif chat:
                events = [
                    line[6:] for line in response.text.splitlines() if line.startswith("data: ")
                ]
                if not events or events[-1] != "[DONE]":
                    response.failure("Incomplete SSE")
                elif any("error" in json.loads(event) for event in events[:-1]):
                    response.failure("SSE error")
            elif "output" not in response.json():
                response.failure("Missing inference output")


class TrafficShape(LoadTestShape):
    def tick(self) -> tuple[int, float] | None:
        elapsed = self.get_run_time()
        stages = {
            "steady": [(30, 4)],
            "ramp": [(10, 2), (20, 8), (30, 16), (40, 4)],
            "spike": [(10, 2), (30, 32), (40, 2)],
            "mixed": [(30, 8)],
        }[SCENARIO]
        for until, users in stages:
            if elapsed < until:
                return users, 16
        return None
