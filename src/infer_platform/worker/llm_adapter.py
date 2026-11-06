import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import httpx

from infer_platform.common.clock import Clock, SystemClock
from infer_platform.common.schemas import (
    ChatChunk,
    ChatCompletion,
    ChatMessage,
    ChatRequest,
    CompletionChoice,
    Usage,
)
from infer_platform.common.settings import LLMConfig
from infer_platform.common.sse import sse_data


class LLMError(Exception):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass
class StreamStats:
    ttft_ms: float | None = None
    tpot_ms: float | None = None
    completion_tokens: int | None = None
    chunk_writes: int = 0
    content_chunks: int = 0


Emit = Callable[[list[ChatChunk]], Awaitable[None]]


class LLMAdapter:
    """HTTP transport boundary only: vLLM owns batching, KV cache and GPU scheduling."""

    def __init__(
        self, client: httpx.AsyncClient, config: LLMConfig, clock: Clock | None = None
    ) -> None:
        self.client, self.config = client, config
        self.clock = clock or SystemClock()

    async def ready(self) -> None:
        response = await self.client.get(self.config.base_url.rstrip("/") + "/models")
        response.raise_for_status()
        if not any(model["id"] == self.config.served_model for model in response.json()["data"]):
            raise ValueError("Configured served model not available upstream")

    async def generate(
        self,
        request: ChatRequest,
        cancelled: Callable[[], Awaitable[bool]],
        emit: Emit | None = None,
        dequeued_at: float | None = None,
    ) -> tuple[ChatCompletion, StreamStats]:
        async def monitor() -> None:
            while not await cancelled():
                await self.clock.sleep(self.config.cancel_poll_ms / 1000)

        generation = asyncio.create_task(self._generate(request, emit, dequeued_at))
        cancel = asyncio.create_task(monitor())
        try:
            done, _ = await asyncio.wait([generation, cancel], return_when=asyncio.FIRST_COMPLETED)
            if cancel in done:
                await (
                    cancel
                )  # Preserve Redis/monitor failures, rather than mistaking them for cancel.
                raise LLMError("CANCELLED")
            return await generation
        finally:
            for task in (generation, cancel):
                task.cancel()
            await asyncio.gather(generation, cancel, return_exceptions=True)

    async def _generate(
        self,
        request: ChatRequest,
        emit: Emit | None,
        dequeued_at: float | None,
    ) -> tuple[ChatCompletion, StreamStats]:
        payload = request.model_dump()
        payload.update(
            model=self.config.served_model, stream=True, stream_options={"include_usage": True}
        )
        started = self.clock.monotonic() if dequeued_at is None else dequeued_at
        stats = StreamStats()
        text: list[str] = []
        chars = 0
        content_times: list[float] = []
        usage: Usage | None = None
        finish_reason: str | None = None
        identity: ChatChunk | None = None
        queue: asyncio.Queue[ChatChunk | None] = asyncio.Queue(self.config.max_buffered_chunks)

        async def produce(response: httpx.Response) -> None:
            count = 0
            async for data in sse_data(response):
                if data == "[DONE]":
                    await queue.put(None)
                    return
                chunk = ChatChunk.model_validate_json(data)
                count += 1
                if count > self.config.max_tokens_cap * 4 + 16:
                    raise LLMError("UPSTREAM_PROTOCOL")
                await queue.put(chunk)
            raise LLMError("UPSTREAM_PROTOCOL")  # EOF is not a successful completion.

        async def consume(producer: asyncio.Task[None]) -> None:
            nonlocal chars, usage, finish_reason, identity
            pending: list[ChatChunk] = []
            deadline = 0.0
            receive = asyncio.create_task(queue.get())

            async def flush() -> None:
                nonlocal pending
                if pending and emit is not None:
                    await emit(pending)
                    stats.chunk_writes += 1
                pending = []

            try:
                while True:
                    timer: asyncio.Task[None] | None = None
                    if pending and emit is not None and self.config.coalesce_ms:
                        timer = asyncio.create_task(
                            self.clock.sleep(max(0, deadline - self.clock.monotonic()))
                        )
                    try:
                        done, _ = await asyncio.wait(
                            [receive, producer, *([timer] if timer else [])],
                            return_when=asyncio.FIRST_COMPLETED,
                        )
                        if producer in done:
                            await producer
                        if receive in done:
                            chunk = receive.result()
                            if chunk is None:
                                await flush()
                                return
                            now = self.clock.monotonic()
                            identity = identity or chunk
                            if chunk.id != identity.id:
                                raise LLMError("UPSTREAM_PROTOCOL")
                            if chunk.usage is not None:
                                usage = chunk.usage
                            if chunk.choices:
                                choice = chunk.choices[0]
                                content = choice.delta.content
                                if content:
                                    chars += len(content)
                                    if chars > self.config.max_output_chars:
                                        raise LLMError("UPSTREAM_PROTOCOL")
                                    text.append(content)
                                    content_times.append(now)
                                    stats.content_chunks += 1
                                    if stats.ttft_ms is None:
                                        stats.ttft_ms = (now - started) * 1000
                                if choice.finish_reason is not None:
                                    finish_reason = choice.finish_reason
                                if not pending:
                                    deadline = now + self.config.coalesce_ms / 1000
                                pending.append(chunk)
                            receive = asyncio.create_task(queue.get())
                            if (
                                not self.config.coalesce_ms
                                or emit is None
                                or len(pending) >= self.config.max_buffered_chunks
                            ):
                                await flush()
                        if timer and timer in done:
                            await flush()
                    finally:
                        if timer:
                            timer.cancel()
                            await asyncio.gather(timer, return_exceptions=True)
            finally:
                receive.cancel()
                await asyncio.gather(receive, return_exceptions=True)

        try:
            async with self.client.stream(
                "POST",
                self.config.base_url.rstrip("/") + "/chat/completions",
                json=payload,
            ) as response:
                response.raise_for_status()
                producer = asyncio.create_task(produce(response))
                try:
                    await consume(producer)
                finally:
                    producer.cancel()
                    await asyncio.gather(producer, return_exceptions=True)
        except httpx.HTTPStatusError as exc:
            raise LLMError(
                "UPSTREAM_BAD_REQUEST"
                if exc.response.status_code in {400, 422}
                else "UPSTREAM_UNAVAILABLE"
                if exc.response.status_code >= 500 or exc.response.status_code == 429
                else "UPSTREAM_ERROR"
            ) from exc
        except httpx.TimeoutException as exc:
            raise LLMError("UPSTREAM_TIMEOUT") from exc
        except (httpx.HTTPError, ValueError) as exc:
            raise LLMError("UPSTREAM_PROTOCOL") from exc
        if identity is None or finish_reason is None:
            raise LLMError("UPSTREAM_PROTOCOL")
        if usage:
            stats.completion_tokens = usage.completion_tokens
            if usage.completion_tokens > 1 and len(content_times) > 1:
                stats.tpot_ms = (
                    (content_times[-1] - content_times[0]) * 1000 / (usage.completion_tokens - 1)
                )
        completion = ChatCompletion(
            id=identity.id,
            created=identity.created,
            model=identity.model,
            choices=[
                CompletionChoice(
                    message=ChatMessage(role="assistant", content="".join(text)),
                    finish_reason=finish_reason,
                )
            ],
            usage=usage,
        )
        return completion, stats
