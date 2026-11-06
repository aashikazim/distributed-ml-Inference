import re
from collections.abc import AsyncGenerator

import httpx


async def sse_data(response: httpx.Response, max_event_chars: int = 262144) -> AsyncGenerator[str]:
    """Parse SSE with bounded line/event buffers, including CRLF and multiline data."""

    async def lines() -> AsyncGenerator[str]:
        buffer = ""
        async for text in response.aiter_text():
            buffer += text
            while match := re.search(r"\r\n|\r|\n", buffer):
                if match.group() == "\r" and match.end() == len(buffer):
                    break  # The next text fragment may complete CRLF.
                if match.start() > max_event_chars:
                    raise ValueError("SSE line exceeds limit")
                yield buffer[: match.start()]
                buffer = buffer[match.end() :]
            if len(buffer) > max_event_chars:
                raise ValueError("SSE line exceeds limit")
        if buffer:
            yield buffer.rstrip("\r")

    parts: list[str] = []
    size = 0
    async for line in lines():
        if not line:
            if parts:
                yield "\n".join(parts)
                parts, size = [], 0
        elif line.startswith("data:"):
            value = line[5:].removeprefix(" ")
            size += len(value) + 1  # Empty data lines still consume bounded bookkeeping.
            if size > max_event_chars:
                raise ValueError("SSE event exceeds limit")
            parts.append(value)
    if parts:
        yield "\n".join(parts)
