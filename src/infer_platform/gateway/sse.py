import json
from collections.abc import AsyncIterable, Awaitable, Callable
from typing import Any

from fastapi.responses import StreamingResponse
from starlette.types import Receive, Scope, Send


def frame(value: dict[str, Any] | str) -> str:
    return "data: " + (value if isinstance(value, str) else json.dumps(value)) + "\n\n"


class ManagedStream(StreamingResponse):
    """Run cleanup even if a client disconnects before the iterator starts."""

    def __init__(
        self,
        content: AsyncIterable[str | bytes],
        cleanup: Callable[[], Awaitable[None]],
        **kwargs: Any,
    ) -> None:
        super().__init__(content, **kwargs)
        self.cleanup = cleanup

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            await self.cleanup()
