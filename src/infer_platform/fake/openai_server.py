import time
from collections.abc import AsyncGenerator
from typing import Any
from uuid import uuid4

from fastapi import FastAPI
from fastapi.responses import JSONResponse, Response, StreamingResponse
from prometheus_client import CollectorRegistry, Gauge, generate_latest
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from infer_platform.common.clock import Clock, SystemClock
from infer_platform.common.schemas import (
    ChatChunk,
    ChatRequest,
    ChunkChoice,
    Delta,
    Usage,
)


class FakeSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="INFER_FAKE_")
    ttft_ms: float = Field(default=40, ge=0)
    token_ms: float = Field(default=5, ge=0)
    tokens: int = Field(default=32, ge=1, le=512)
    served_model: str = "Qwen/Qwen2.5-0.5B-Instruct"
    error_status: int = Field(default=0, ge=0, le=599)
    fail_after: int | None = Field(default=None, ge=0)
    stall_after: int | None = Field(default=None, ge=0)


class FakeStreamRequest(ChatRequest):
    stream_options: dict[str, bool] = Field(default_factory=dict)


def create_app(settings: FakeSettings | None = None, clock: Clock | None = None) -> FastAPI:
    settings, clock = settings or FakeSettings(), clock or SystemClock()
    app = FastAPI()
    registry = CollectorRegistry()
    active_gauge = Gauge(
        "vllm:num_requests_running", "SIMULATED active requests", registry=registry
    )
    stats = {
        "active": 0,
        "max_active": 0,
        "completed": 0,
        "cancelled": 0,
        "calls": 0,
        "last_max_tokens": 0,
    }
    app.state.stats = stats

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "backend": "SIMULATED"}

    @app.get("/v1/models")
    async def models() -> dict[str, Any]:
        return {"object": "list", "data": [{"id": settings.served_model, "object": "model"}]}

    @app.get("/debug/stats")
    async def debug_stats() -> dict[str, int]:
        return dict(stats)

    @app.get("/metrics")
    async def metrics() -> Response:
        return Response(generate_latest(registry), media_type="text/plain; version=0.0.4")

    @app.post("/v1/chat/completions")
    async def chat(body: FakeStreamRequest) -> Response:
        if settings.error_status:
            return JSONResponse(
                status_code=settings.error_status,
                content={
                    "error": {
                        "code": "SIMULATED_ERROR",
                        "message": "Configured fake upstream error",
                    },
                },
            )
        if body.model != settings.served_model:
            return JSONResponse(status_code=404, content={"error": {"message": "Unknown model"}})
        stats["last_max_tokens"] = body.max_tokens
        count = min(body.max_tokens, settings.tokens)

        async def stream() -> AsyncGenerator[str]:
            stats["calls"] += 1
            stats["active"] += 1
            stats["max_active"] = max(stats["max_active"], stats["active"])
            active_gauge.inc()
            completed = False
            stream_id, created = "chatcmpl-" + uuid4().hex, int(time.time())

            def event(delta: Delta, finish: str | None = None) -> str:
                chunk = ChatChunk(
                    id=stream_id,
                    created=created,
                    model=body.model,
                    choices=[ChunkChoice(delta=delta, finish_reason=finish)],
                )
                return "data: " + chunk.model_dump_json(exclude_none=True) + "\n\n"

            try:
                yield event(Delta(role="assistant"))
                await clock.sleep(settings.ttft_ms / 1000)
                for index in range(count):
                    if settings.stall_after == index:
                        await clock.sleep(3600)
                    if settings.fail_after == index:
                        yield 'data: {"error":{"message":"SIMULATED stream failure"}}\n\n'
                        return
                    if index:
                        await clock.sleep(settings.token_ms / 1000)
                    yield event(Delta(content=f"token{index} "))
                finish_reason = "length" if count == body.max_tokens else "stop"
                yield event(Delta(), finish_reason)
                if body.stream_options.get("include_usage"):
                    usage = Usage(
                        prompt_tokens=len(body.messages),
                        completion_tokens=count,
                        total_tokens=len(body.messages) + count,
                    )
                    chunk = ChatChunk(
                        id=stream_id, created=created, model=body.model, choices=[], usage=usage
                    )
                    yield "data: " + chunk.model_dump_json(exclude_none=True) + "\n\n"
                yield "data: [DONE]\n\n"
                completed = True
                stats["completed"] += 1
            finally:
                if not completed:
                    stats["cancelled"] += 1
                stats["active"] -= 1
                active_gauge.dec()

        if not body.stream:
            return JSONResponse(
                content={"error": {"message": "Fake server expects streaming"}}, status_code=400
            )
        return StreamingResponse(stream(), media_type="text/event-stream")

    return app
