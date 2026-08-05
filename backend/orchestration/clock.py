"""The time source orchestration reads, so tests are not at the mercy of the wall clock.

Owner: Jerome & Richard

Wall-clock milliseconds stamp the times Minecraft and telemetry compare. Interaction recency
needs monotonic time instead; that arrives with the ticket that derives it (#7).
"""

from __future__ import annotations

import time
from typing import Protocol


class Clock(Protocol):
    def now_ms(self) -> int: ...


class SystemClock:
    def now_ms(self) -> int:
        return time.time_ns() // 1_000_000
