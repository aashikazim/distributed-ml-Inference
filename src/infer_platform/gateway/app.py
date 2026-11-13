import asyncio
import json
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager, suppress
from typing import Any
from uuid import uuid4

import structlog
from fastapi import FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from prometheus_client import generate_latest
from redis.exceptions import RedisError

from infer_platform.common.clock import Clock, SystemClock
from infer_platform.common.inputs import InputError, describe, normalize
from infer_platform.common.keys import blob_key, cancel_key, inbox, request_stream, result_key
from infer_platform.common.logging import configure_logging
from infer_platform.common.redis import create_redis, server_ms
from infer_platform.common.registry import live_workers
from infer_platform.common.schemas import (
    InboxMessage,
    InferInput,
    InferResponse,
    Meta,
    RequestMessage,
    ResultRecord,
)
from infer_platform.common.settings import Settings
from infer_platform.gateway.admission import WorkerDirectory, enqueue
from infer_platform.gateway.auth import Authenticator
from infer_platform.gateway.body_limit import BodyLimit
from infer_platform.gateway.rate_limit import RateLimiter
from infer_platform.gateway.response_router import ResponseRouter


def error(
    status: int,
    code: str,
    message: str,
    request_id: str | None = None,
    retry_after: int | None = None,
) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        headers={"Retry-After": str(retry_after)} if retry_after is not None else None,
        content={"error": {"code": code, "message": message, "request_id": request_id}},
    )


def create_app(settings: Settings | None = None, clock: Clock | None = None) -> FastAPI:
    config = settings or Settings()
    clock = clock or SystemClock()

    from infer_platform.gateway.metrics import GatewayMetrics, ObserveHTTP, timed_reply

    observed = GatewayMetrics()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
        configure_logging()
        redis = create_redis(config)
        router = ResponseRouter(redis, inbox(uuid4().hex), config.inbox_ttl_s, config.cancel_ttl_s)
        app.state.config = config
        app.state.models = config.load_models()
        app.state.auth = Authenticator(config.api_keys)
        app.state.limiter = RateLimiter(redis, config.rate_limit_per_s, config.rate_limit_burst)
        app.state.metrics_registry = observed.registry
        app.state.chat_ttft = observed.ttft
        app.state.redis = redis
        app.state.router = router
        app.state.directory = WorkerDirectory(redis, config.worker_cache_s, clock)
        try:
            await router.start()
            yield
        finally:
            try:
                await router.close()
            finally:
                await redis.aclose()

    app = FastAPI(lifespan=lifespan)
    app.add_middleware(BodyLimit, limit=config.max_body_bytes)
    app.add_middleware(ObserveHTTP, metrics=observed, models=set(config.load_models()), clock=clock)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        # Validation details can contain user input: keep the external error generic.
        return error(422, "INVALID_INPUT", "Invalid request body")

    @app.exception_handler(RedisError)
    async def redis_error(request: Request, exc: RedisError) -> JSONResponse:
        return error(503, "UNAVAILABLE", "Redis unavailable", retry_after=1)

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

    @app.get("/metrics")
    async def metrics(request: Request) -> Response:
        return Response(
            generate_latest(request.app.state.metrics_registry),
            media_type="text/plain; version=0.0.4",
        )

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
                    "live_workers": len(await live_workers(request.app.state.redis, name)),
                    **(
                        {}
                        if config.backend == "llm"
                        else {"inputs": describe(config.input_specs())}
                    ),
                }
                for name, config in request.app.state.models.items()
            ]
        }

    @app.get("/v1/results/{request_id}")
    async def results(request_id: str, request: Request) -> Any:
        key_id = request.app.state.auth.authenticate(request.headers.get("X-API-Key"))
        if key_id is None:
            return error(401, "UNAUTHORIZED", "Missing or invalid API key")
        value = await request.app.state.redis.get(result_key(request_id))
        if not value:
            return error(404, "UNKNOWN_REQUEST", "Unknown or expired request", request_id)
        record = ResultRecord.model_validate_json(value)
        if record.key_id != key_id:
            return error(404, "UNKNOWN_REQUEST", "Unknown or expired request", request_id)
        if record.response is not None:
            return render(record.response)
        if await server_ms(request.app.state.redis) >= record.deadline_ms:
            return error(504, "EXPIRED", "Request deadline exceeded", request_id)
        return JSONResponse(
            status_code=202, content={"request_id": request_id, "status": record.status}
        )

    async def mark_cancelled(request: Request, request_id: str) -> None:
        # Cancellation is best effort during Redis failure and must not delay a timeout by seconds.
        with suppress(RedisError, TimeoutError):
            async with asyncio.timeout(0.25):
                await request.app.state.redis.set(
                    cancel_key(request_id), "1", ex=config.cancel_ttl_s
                )

    async def disconnected(request: Request) -> None:
        while not await request.is_disconnected():
            await clock.sleep(0.05)

    async def wait_reply(queue: asyncio.Queue[InboxMessage], request: Request) -> Any:
        reply = asyncio.create_task(queue.get())
        disconnect = asyncio.create_task(disconnected(request))
        try:
            done, _ = await asyncio.wait([reply, disconnect], return_when=asyncio.FIRST_COMPLETED)
            if reply in done:
                return await reply
            raise ConnectionAbortedError
        finally:
            for task in (reply, disconnect):
                task.cancel()
            await asyncio.gather(reply, disconnect, return_exceptions=True)

    @app.post("/v1/infer/{model}")
    async def infer(
        model: str,
        body: InferInput,
        request: Request,
        async_mode: bool = Query(False, alias="async"),
    ) -> Any:
        state = request.app.state
        key_id = state.auth.authenticate(request.headers.get("X-API-Key"))
        if key_id is None:
            return error(401, "UNAUTHORIZED", "Missing or invalid API key")
        model_config = state.models.get(model)
        if model_config is None:
            return error(404, "UNKNOWN_MODEL", "Unknown model")
        if model_config.backend == "llm":
            return error(400, "INVALID_INPUT", "Use /v1/chat/completions for LLM models")
        try:
            normalize(body, model_config.input_specs())
        except InputError as exc:
            return error(422, "INVALID_INPUT", str(exc))
        if any(key != "async" for key in request.query_params):
            return error(400, "INVALID_INPUT", "Unknown query parameter")
        started = clock.monotonic()
        try:
            async with asyncio.timeout(model_config.request_timeout_ms / 1000):
                retry = await state.limiter.check(key_id)
        except TimeoutError:
            return error(504, "TIMEOUT", "Request deadline exceeded")
        if retry:
            return error(429, "RATE_LIMITED", "API key rate limit exceeded", retry_after=retry)
        remaining_s = max(
            0.001, model_config.request_timeout_ms / 1000 - (clock.monotonic() - started)
        )
        router = state.router
        if not async_mode and len(router.waiters) >= config.max_gateway_inflight:
            return error(503, "OVERLOADED", "Gateway waiter capacity reached", retry_after=1)
        request_id = uuid4().hex
        context = structlog.contextvars.get_contextvars()
        structlog.contextvars.bind_contextvars(request_id=request_id)
        queue = None if async_mode else router.register(request_id)
        # exclude_none keeps legacy bodies byte-identical to the original wire payload.
        payload = body.model_dump_json(exclude_none=True)
        payload_bytes = len(payload.encode())
        claim_check = payload_bytes > config.claim_check_bytes
        message = RequestMessage(
            request_id=request_id,
            reply_to=router.inbox,
            payload="" if claim_check else payload,
            payload_ref=blob_key(request_id) if claim_check else "",
            timeout_ms=max(1, int(remaining_s * 1000)),
            key_id=key_id,
            trace_id=context.get("trace_id", uuid4().hex),
        )
        accepted = False
        try:
            async with asyncio.timeout(remaining_s):
                if claim_check:
                    # The blob must exist before the entry is visible to any worker.
                    await state.redis.set(blob_key(request_id), payload, ex=config.result_ttl_s)
                outcome = await enqueue(
                    state.redis,
                    model,
                    message,
                    model_config.max_queue_depth,
                    config.result_ttl_s,
                    config.admission_enabled,
                    state.directory,
                )
                if outcome != "ACCEPTED":
                    if claim_check:
                        await state.redis.delete(blob_key(request_id))
                    return error(
                        503, outcome, "No worker capacity available", request_id, retry_after=1
                    )
                accepted = True
                if async_mode:
                    return JSONResponse(status_code=202, content={"request_id": request_id})
                assert queue is not None
                reply = await wait_reply(queue, request)
                elapsed = (clock.monotonic() - request.scope.get("metric_started", started)) * 1000
                return render(timed_reply(reply, elapsed))
        except TimeoutError:
            await mark_cancelled(request, request_id)
            return error(504, "TIMEOUT", "Request deadline exceeded", request_id)
        except ConnectionAbortedError:
            await mark_cancelled(request, request_id)
            return error(400, "CANCELLED", "Client disconnected", request_id)
        except asyncio.CancelledError:
            # Enqueue may have reached Redis even if its connection/future was cancelled.
            await mark_cancelled(request, request_id)
            raise
        finally:
            router.unregister(request_id)
            structlog.get_logger().info(
                "request_wait_finished",
                component="gateway",
                request_id=request_id,
                trace_id=message.trace_id,
                model=model,
                payload_bytes=payload_bytes,
                claim_check=claim_check,
                accepted=accepted,
                async_mode=async_mode,
            )

    from infer_platform.gateway.chat import install_chat_routes

    install_chat_routes(app, config, clock, mark_cancelled, wait_reply)
    return app


def render(response: Any) -> Any:
    if response.type == "error":
        failure = json.loads(response.data)
        status = {
            "EXPIRED": 504,
            "CANCELLED": 400,
            "INVALID_INPUT": 422,
            "DEAD_LETTERED": 500,
            "UPSTREAM_BAD_REQUEST": 422,
            "UPSTREAM_ERROR": 502,
            "UPSTREAM_PROTOCOL": 502,
            "UPSTREAM_UNAVAILABLE": 503,
            "UPSTREAM_TIMEOUT": 504,
            "STREAM_INTERRUPTED": 502,
            "SLOW_CONSUMER": 503,
        }.get(failure["code"], 500)
        return error(status, failure["code"], failure["message"], response.request_id)
    output = json.loads(response.data)
    if output.get("object") == "chat.completion":
        return {**output, "meta": json.loads(response.meta)}
    return InferResponse(
        request_id=response.request_id,
        output=output,
        meta=Meta.model_validate_json(response.meta),
    )
