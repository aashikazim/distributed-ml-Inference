import asyncio
import json
from collections.abc import AsyncGenerator, Awaitable, Callable
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, Request

from infer_platform.common.clock import Clock
from infer_platform.common.schemas import ChatRequest, ChunkBundle, InboxMessage, RequestMessage
from infer_platform.common.settings import Settings
from infer_platform.gateway.admission import enqueue
from infer_platform.gateway.sse import ManagedStream, frame

Cancel = Callable[[Request, str], Awaitable[None]]
Wait = Callable[[asyncio.Queue[InboxMessage], Request], Awaitable[Any]]


def install_chat_routes(
    app: FastAPI, config: Settings, clock: Clock, cancel: Cancel, wait: Wait
) -> None:
    from infer_platform.gateway.app import error, render

    @app.post("/v1/chat/completions")
    async def chat(body: ChatRequest, request: Request) -> Any:
        started = clock.monotonic()
        state = request.app.state
        key_id = state.auth.authenticate(request.headers.get("X-API-Key"))
        if key_id is None:
            return error(401, "UNAUTHORIZED", "Missing or invalid API key")
        model = state.models.get(body.model)
        if model is None:
            return error(404, "UNKNOWN_MODEL", "Unknown model")
        if model.backend != "llm" or model.llm is None:
            return error(400, "INVALID_INPUT", "Model does not support chat")
        if (
            body.max_tokens > model.llm.max_tokens_cap
            or sum(len(m.content) for m in body.messages) > model.max_input_chars
        ):
            return error(422, "INVALID_INPUT", "Chat input or token limit exceeded")
        router = state.router
        if len(router.waiters) >= config.max_gateway_inflight:
            return error(503, "OVERLOADED", "Gateway waiter capacity reached", retry_after=1)
        rid = uuid4().hex
        queue = router.register(
            rid, config.gateway_stream_buffer if body.stream else 1, body.stream
        )
        handed_off = False
        complete = False
        closed = False
        attempted = False
        deadline = started + model.request_timeout_ms / 1000

        async def cleanup() -> None:
            nonlocal closed
            if not closed:
                closed = True
                router.unregister(rid)
                if attempted and not complete:
                    await cancel(request, rid)

        async def events(first: InboxMessage) -> AsyncGenerator[str]:
            nonlocal complete
            current = first
            first_content = True
            try:
                while True:
                    if current.type == "chunk":
                        bundle = ChunkBundle.model_validate_json(current.data)
                        for chunk in bundle.chunks:
                            if first_content and any(c.delta.content for c in chunk.choices):
                                first_content = False
                                state.chat_ttft.labels(body.model).observe(
                                    clock.monotonic() - started
                                )
                            yield frame(chunk.model_dump(exclude_none=True))
                    elif current.type == "error":
                        complete = True
                        failure = json.loads(current.data)
                        yield frame({"error": {**failure, "request_id": rid}})
                        yield frame("[DONE]")
                        return
                    else:
                        complete = True
                        final = json.loads(current.data)
                        yield frame(
                            {
                                "id": final["id"],
                                "object": "chat.completion.chunk",
                                "created": final["created"],
                                "model": final["model"],
                                "choices": [],
                                "usage": final.get("usage"),
                                "meta": json.loads(current.meta),
                            }
                        )
                        yield frame("[DONE]")
                        return
                    async with asyncio.timeout(max(0, deadline - clock.monotonic())):
                        current = await queue.get()
            except TimeoutError:
                yield frame(
                    {
                        "error": {
                            "code": "EXPIRED",
                            "message": "Request deadline exceeded",
                            "request_id": rid,
                        }
                    }
                )
                yield frame("[DONE]")

        try:
            async with asyncio.timeout(max(0, deadline - clock.monotonic())):
                retry = await state.limiter.check(key_id)
                if retry:
                    return error(
                        429, "RATE_LIMITED", "API key rate limit exceeded", retry_after=retry
                    )
                remaining = max(1, int((deadline - clock.monotonic()) * 1000))
                message = RequestMessage(
                    request_id=rid,
                    reply_to=router.inbox,
                    kind="chat",
                    payload=body.model_dump_json(),
                    timeout_ms=remaining,
                    stream="1" if body.stream else "0",
                    key_id=key_id,
                    trace_id=uuid4().hex,
                )
                attempted = True
                outcome = await enqueue(
                    state.redis,
                    body.model,
                    message,
                    model.max_queue_depth,
                    config.result_ttl_s,
                    config.admission_enabled,
                )
                if outcome != "ACCEPTED":
                    attempted = False
                    return error(503, outcome, "No worker capacity available", rid, retry_after=1)
                first = await wait(queue, request)
                if not body.stream or first.type == "error":
                    complete = True
                    return render(first)
                response = ManagedStream(
                    events(first),
                    cleanup,
                    media_type="text/event-stream",
                    headers={
                        "Cache-Control": "no-cache",
                        "X-Accel-Buffering": "no",
                        "X-Request-ID": rid,
                    },
                )
                handed_off = True
                return response
        except TimeoutError:
            return error(504, "EXPIRED", "Request deadline exceeded", rid)
        except ConnectionAbortedError:
            return error(400, "CANCELLED", "Client disconnected", rid)
        finally:
            if not handed_off:
                await cleanup()
