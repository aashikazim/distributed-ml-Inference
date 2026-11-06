from collections.abc import AsyncGenerator

import httpx


async def sse_data(response: httpx.Response, max_event_chars: int = 262144) -> AsyncGenerator[str]:
    """Parse SSE data lines, including CRLF and multiline data, with bounded event size."""
    parts: list[str] = []
    size = 0
    async for line in response.aiter_lines():
        if len(line) > max_event_chars:
            raise ValueError("Upstream SSE line exceeds limit")
        if not line:
            if parts:
                yield "\n".join(parts)
                parts, size = [], 0
        elif line.startswith("data:"):
            value = line[5:].removeprefix(" ")
            size += len(value)
            if size > max_event_chars:
                raise ValueError("Upstream SSE event exceeds limit")
            parts.append(value)
    if parts:
        yield "\n".join(parts)
