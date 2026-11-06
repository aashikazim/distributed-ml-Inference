from starlette.types import ASGIApp, Message, Receive, Scope, Send


class BodyLimit:
    """Count streamed bytes too; Content-Length alone is not a trustworthy cap."""

    def __init__(self, app: ASGIApp, limit: int) -> None:
        self.app, self.limit = app, limit

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        chunks: list[Message] = []
        size = 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            size += len(message.get("body", b""))
            if size > self.limit:
                from infer_platform.gateway.app import error

                await error(422, "INVALID_INPUT", "Request body exceeds byte limit")(
                    scope, receive, send
                )
                return
            chunks.append(message)
            if not message.get("more_body", False):
                break
        index = 0

        async def bounded_receive() -> Message:
            nonlocal index
            if index < len(chunks):
                message = chunks[index]
                index += 1
                return message
            return await receive()

        await self.app(scope, bounded_receive, send)
