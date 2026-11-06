import asyncio
import time
from typing import Protocol


class Clock(Protocol):
    def monotonic(self) -> float: ...
    async def sleep(self, seconds: float) -> None: ...


class SystemClock:
    def monotonic(self) -> float:
        return time.monotonic()

    async def sleep(self, seconds: float) -> None:
        await asyncio.sleep(seconds)
