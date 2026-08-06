"""The time sources orchestration reads, so tests are not at the mercy of the wall clock.

Owner: Jerome & Richard

Wall-clock milliseconds stamp the times Minecraft and telemetry compare, so they must remain
comparable with timestamps produced on another machine. Interaction recency compares stamps only
with each other and must not be disturbed by a clock correction, so it reads monotonic time. The
two are different quantities and neither substitutes for the other.

`Deadlines` is the third: waiting. A provider call bounded by four seconds and a publication
retry cadence measured in milliseconds are both real waits, and a suite that used the event
loop's own clock for them would have to sleep for real. Keeping the wait behind a port means a
test decides when a deadline expires instead of watching one elapse.
"""

from __future__ import annotations

import asyncio
import time
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import AsyncIterator, Protocol


class Clock(Protocol):
    def now_ms(self) -> int: ...

    def monotonic_ms(self) -> int: ...


class SystemClock:
    def now_ms(self) -> int:
        return time.time_ns() // 1_000_000

    def monotonic_ms(self) -> int:
        return time.monotonic_ns() // 1_000_000


class DeadlineExceeded(TimeoutError):
    """Work did not finish inside the budget it was given, so it was abandoned."""


class Deadlines(Protocol):
    def limit(self, milliseconds: int) -> AbstractAsyncContextManager[None]:
        """Bound the work inside, raising `DeadlineExceeded` when it runs over."""
        ...

    async def sleep(self, milliseconds: int) -> None: ...


class AsyncioDeadlines:
    """The production waiting implementation, on the event loop's own clock."""

    @asynccontextmanager
    async def limit(self, milliseconds: int) -> AsyncIterator[None]:
        try:
            async with asyncio.timeout(milliseconds / 1000):
                yield
        except TimeoutError as expired:
            raise DeadlineExceeded(f"exceeded the {milliseconds}ms budget") from expired

    async def sleep(self, milliseconds: int) -> None:
        await asyncio.sleep(milliseconds / 1000)
