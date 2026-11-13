"""Local TCP servers and isolated LLM catalogs for tests and measured drills."""

import asyncio
import os
import socket
import subprocess
import sys
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
import yaml
from pydantic import SecretStr

from infer_platform.common.settings import Settings
from tools.reliability import eventually


class LocalServer:
    def __init__(self, root: Path, factory: str, env: dict[str, str] | None = None) -> None:
        self.log_path = root / f"http-{uuid4().hex}.log"
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen()
            self.url = f"http://127.0.0.1:{listener.getsockname()[1]}"
            with self.log_path.open("wb") as log:
                self.process = subprocess.Popen(
                    [
                        sys.executable,
                        "-m",
                        "uvicorn",
                        factory,
                        "--factory",
                        "--fd",
                        str(listener.fileno()),
                        "--log-level",
                        "warning",
                        "--no-access-log",
                    ],
                    pass_fds=(listener.fileno(),),
                    env={**os.environ, **(env or {})},
                    stdout=log,
                    stderr=subprocess.STDOUT,
                )

    async def ready(self, path: str = "/health") -> None:
        async with httpx.AsyncClient(timeout=2) as client:

            async def healthy() -> bool:
                if self.process.poll() is not None:
                    raise RuntimeError(self.log_path.read_text())
                try:
                    return (await client.get(self.url + path)).status_code == 200
                except httpx.HTTPError:
                    return False

            await eventually(healthy, seconds=20)

    async def stats(self) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=3) as client:
            return (await client.get(self.url + "/debug/stats")).json()

    async def close(self) -> None:
        if self.process.poll() is None:
            self.process.terminate()
        try:
            await asyncio.to_thread(self.process.wait, 5)
        except subprocess.TimeoutExpired:
            self.process.kill()
            await asyncio.to_thread(self.process.wait, 5)


def llm_settings(
    root: Path,
    redis_url: str,
    upstream: str,
    concurrent: int = 2,
    coalesce_ms: float = 20,
    deadline_ms: int = 3000,
    cap: int = 64,
    served_model: str = "Qwen/Qwen2.5-0.5B-Instruct",
) -> Settings:
    model = "chat-" + uuid4().hex
    path = root / f"catalog-{uuid4().hex}.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "models": {
                    model: {
                        "backend": "llm",
                        "task": "chat",
                        "max_input_chars": 16000,
                        "request_timeout_ms": deadline_ms,
                        "max_queue_depth": 200,
                        "reclaim_idle_ms": deadline_ms + 1000,
                        "reclaim_interval_ms": 100,
                        "llm": {
                            "base_url": upstream + "/v1",
                            "served_model": served_model,
                            "max_concurrent": concurrent,
                            "max_tokens_cap": cap,
                            "coalesce_ms": coalesce_ms,
                            "cancel_poll_ms": 20,
                            "http_timeout_s": 2,
                        },
                    }
                }
            }
        )
    )
    return Settings(
        redis_url=redis_url,
        api_keys=[SecretStr("llm-" + uuid4().hex)],
        models_path=path,
        worker_model=model,
        heartbeat_interval_s=0.05,
        heartbeat_ttl_s=1,
        rate_limit_burst=100000,
        rate_limit_per_s=100000,
    )
