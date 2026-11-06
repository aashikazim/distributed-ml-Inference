import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from uuid import uuid4

import structlog

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from redis.exceptions import RedisError

from infer_platform.common.keys import inbox, request_stream
from infer_platform.common.logging import configure_logging
from infer_platform.common.redis import create_redis
from infer_platform.common.schemas import InferInput, InferResponse, Meta, RequestMessage
from infer_platform.common.settings import Settings
from infer_platform.gateway.auth import Authenticator
from infer_platform.gateway.response_router import ResponseRouter


def error(status: int, code: str, message: str, request_id: str | None = None) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={"error": {"code": code, "message": message, "request_id": request_id}},
    )


def create_app(settings: Settings | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        config = settings or Settings()
        configure_logging()
        redis = create_redis(config)
        router = ResponseRouter(redis, inbox(uuid4().hex), config.inbox_ttl_s)
        app.state.config = config
        app.state.models = config.load_models()
        app.state.auth = Authenticator(config.api_keys)
        app.state.redis = redis
        app.state.router = router
        try:
            await router.start()
            yield
        finally:
            try:
                await router.close()
            finally:
                await redis.aclose()

    app = FastAPI(lifespan=lifespan)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        # Validation details can contain user input: keep the external error generic.
        return error(422, "INVALID_INPUT", "Invalid request body")

    @app.exception_handler(RedisError)
    async def redis_error(request: Request, exc: RedisError) -> JSONResponse:
        return error(503, "UNAVAILABLE", "Redis unavailable")

    @app.get("/healthz")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/readyz")
    async def ready(request: Request) -> Any:
        router = request.app.state.router
        try:
            await request.app.state.redis.ping()
        except RedisError:
            return error(503, "NOT_READY", "Redis unavailable")
        if not router.connected or router.task is None or router.task.done():
            return error(503, "NOT_READY", "Inbox reader unavailable")
        return {"status": "ready"}

    @app.get("/v1/models")
    async def models(request: Request) -> Any:
        if not request.app.state.auth.authenticate(request.headers.get("X-API-Key")):
            return error(401, "UNAUTHORIZED", "Missing or invalid API key")
        return {
            "models": [
                {
                    "id": name,
                    "backend": config.backend,
                    "queue_depth": await request.app.state.redis.xlen(request_stream(name)),
                }
                for name, config in request.app.state.models.items()
            ]
        }

    @app.post("/v1/infer/{model}")
    async def infer(model: str, body: InferInput, request: Request) -> Any:
        state = request.app.state
        key_id = state.auth.authenticate(request.headers.get("X-API-Key"))
        if key_id is None:
            return error(401, "UNAUTHORIZED", "Missing or invalid API key")
        config = state.models.get(model)
        if config is None:
            return error(404, "UNKNOWN_MODEL", "Unknown model")
        if len(body.input) > config.max_input_chars:
            return error(422, "INVALID_INPUT", "Input exceeds model character limit")
        if request.query_params:
            return error(
                400, "UNSUPPORTED_MODE", "Only synchronous inference is available in Phase 1"
            )
        router = state.router
        if len(router.waiters) >= state.config.max_gateway_inflight:
            return error(503, "OVERLOADED", "Gateway waiter capacity reached")
        request_id = uuid4().hex
        queue = router.register(request_id)
        message = RequestMessage(
            request_id=request_id,
            reply_to=router.inbox,
            payload=body.model_dump_json(),
            timeout_ms=config.request_timeout_ms,
            key_id=key_id,
            trace_id=uuid4().hex,
        )
        try:
            async with asyncio.timeout(config.request_timeout_ms / 1000):
                await state.redis.xadd(request_stream(model), message.fields())
                response = await queue.get()
            if response.type == "error":
                failure = json.loads(response.data)
                return error(
                    504 if failure["code"] == "EXPIRED" else 500,
                    failure["code"],
                    failure["message"],
                    request_id,
                )
            return InferResponse(
                request_id=request_id,
                output=json.loads(response.data),
                meta=Meta.model_validate_json(response.meta),
            )
        except TimeoutError:
            return error(504, "TIMEOUT", "Request deadline exceeded", request_id)
        finally:
            router.waiters.pop(request_id, None)
            structlog.get_logger().info(
                "request_wait_finished",
                component="gateway",
                request_id=request_id,
                trace_id=message.trace_id,
                model=model,
                input_chars=len(body.input),
            )

    return app
