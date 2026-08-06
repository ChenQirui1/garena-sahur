"""The time source orchestration reads, so tests are not at the mercy of the wall clock.

Owner: Jerome & Richard

Wall-clock milliseconds stamp the times Minecraft and telemetry compare, so they must remain
comparable with timestamps produced on another machine. Interaction recency compares stamps only
with each other and must not be disturbed by a clock correction, so it reads monotonic time. The
two are different quantities and neither substitutes for the other.
"""

from __future__ import annotations

import time
from typing import Protocol


class Clock(Protocol):
    def now_ms(self) -> int: ...

    def monotonic_ms(self) -> int: ...


class SystemClock:
    def now_ms(self) -> int:
        return time.time_ns() // 1_000_000

    def monotonic_ms(self) -> int:
        return time.monotonic_ns() // 1_000_000
