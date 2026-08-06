"""Owner: Jerome & Richard

The rest of the suite runs on `ManualClock` and `ManualDeadlines`, which is what makes it
deterministic — and what leaves the shipped adapters, the ones `backend/main.py` actually wires,
unproven. A gateway whose deadlines never expire and whose retries never pause passes every other
test in this repository. These cases are therefore the only ones that touch real time, and they
stay well under a second by expiring a generous wait rather than waiting out a generous budget.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from backend.orchestration.clock import AsyncioDeadlines, DeadlineExceeded, SystemClock

# Long enough to survive a loaded machine, short enough that the suite never notices.
BUDGET_MS = 50

# Far longer than the budget, so the wait is abandoned rather than completed. It is never slept
# through: the deadline cancels it.
UNREACHABLE_WAIT_MS = 30_000

SUB_SECOND = 1.0

# 2020-01-01T00:00:00Z. Any epoch-millisecond reading is past it and any monotonic reading on a
# machine that has not been up for fifty years is not.
EPOCH_MS_FLOOR = 1_577_836_800_000

ADVANCE_TIMEOUT_SECONDS = 2.0


async def test_work_over_its_budget_is_abandoned_inside_a_second() -> None:
    started = time.monotonic()

    with pytest.raises(DeadlineExceeded):
        async with AsyncioDeadlines().limit(BUDGET_MS):
            await asyncio.sleep(UNREACHABLE_WAIT_MS / 1000)

    assert time.monotonic() - started < SUB_SECOND


async def test_work_inside_its_budget_is_left_alone() -> None:
    """A `limit` that expired unconditionally would pass the case above and fail every call."""
    completed = False

    async with AsyncioDeadlines().limit(BUDGET_MS):
        await asyncio.sleep(0)
        completed = True

    assert completed


async def test_an_expired_budget_names_the_budget_it_exceeded() -> None:
    """Telemetry reads this text to say which bound was hit, so it carries the number."""
    with pytest.raises(DeadlineExceeded, match=str(BUDGET_MS)):
        async with AsyncioDeadlines().limit(BUDGET_MS):
            await asyncio.sleep(UNREACHABLE_WAIT_MS / 1000)


async def test_sleeping_actually_waits_for_the_requested_delay() -> None:
    """The publication retry cadence is a real pause; a no-op sleep would spin the retries."""
    started = time.monotonic()

    await AsyncioDeadlines().sleep(BUDGET_MS)

    elapsed_ms = (time.monotonic() - started) * 1000
    assert elapsed_ms >= BUDGET_MS * 0.9
    assert elapsed_ms < SUB_SECOND * 1000


def test_the_monotonic_reading_never_goes_backwards() -> None:
    clock = SystemClock()

    readings = []
    for _ in range(5):
        readings.append(clock.monotonic_ms())
        time.sleep(0.001)

    assert readings == sorted(readings)


def test_the_monotonic_reading_advances_with_real_time() -> None:
    """A constant satisfies "non-decreasing", so the reading also has to move."""
    clock = SystemClock()
    first = clock.monotonic_ms()

    deadline = time.monotonic() + ADVANCE_TIMEOUT_SECONDS
    while clock.monotonic_ms() == first and time.monotonic() < deadline:
        time.sleep(0.001)

    assert clock.monotonic_ms() > first


def test_the_wall_reading_is_epoch_milliseconds() -> None:
    """Minecraft and telemetry compare these stamps across machines, so the origin is shared."""
    clock = SystemClock()

    assert clock.now_ms() > EPOCH_MS_FLOOR


def test_the_two_readings_are_different_quantities() -> None:
    """Serving one from the other would make recency survive a clock correction, or not."""
    clock = SystemClock()

    assert clock.now_ms() != clock.monotonic_ms()
