"""Contract fakes for the adapters the owned trace ends at.

Owner: Jerome & Richard

Passing against these proves the owned pipeline only; Minecraft command application and
Elson & Daniel's telemetry aggregation are separate integration gates.
"""

from __future__ import annotations

import asyncio
from collections import Counter
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import AsyncIterator, Callable

from backend.models.model_gateway import GeneratedBehaviour, GenerationRequest, Provider
from backend.orchestration.behaviour_command import BehaviourCommand
from backend.orchestration.clock import DeadlineExceeded
from backend.orchestration.command_store import CommandStore, StoredCommand
from backend.orchestration.router_port import AttentionTier
from backend.orchestration.telemetry_port import ModelCallFact

# Waiting for the pipeline to do what it is going to do. Durable reads cross a real thread, so
# a bare `sleep(0)` is not enough to let them land.
TICK_SECONDS = 0.002
WAIT_TICKS = 500
SETTLE_TICKS = 25


class ManualClock:
    """A clock that only moves when a test moves it.

    Both readings advance together, because a test that moves time forward means the scene
    aged, not that one of the two clocks drifted.
    """

    def __init__(self, now_ms: int = 1_786_208_500_300) -> None:
        self._now_ms = now_ms
        self._monotonic_ms = 0

    def now_ms(self) -> int:
        return self._now_ms

    def monotonic_ms(self) -> int:
        return self._monotonic_ms

    def advance(self, milliseconds: int) -> None:
        self._now_ms += milliseconds
        self._monotonic_ms += milliseconds

    def correct(self, milliseconds: int) -> None:
        """Jump the wall clock alone, the way NTP does. Monotonic time cannot be corrected."""
        self._now_ms += milliseconds


@dataclass(slots=True)
class _Pending:
    """One open deadline: whose work it bounds, and the budget it was opened with."""

    task: asyncio.Task[object]
    milliseconds: int
    expired: bool = False


class ManualDeadlines:
    """Waiting that only happens when a test says so.

    `limit` expires the way `asyncio.timeout` does — by cancelling the task and converting the
    cancellation — so production code cannot tell this apart from a real elapsed budget. `sleep`
    returns at once and moves the manual clock instead, which is what lets a retry cadence be
    asserted as a list of requested delays rather than watched in real time.
    """

    def __init__(self, clock: "ManualClock | None" = None) -> None:
        self.slept_ms: list[int] = []
        self._clock = clock
        self._open: list[_Pending] = []

    @property
    def open_budgets_ms(self) -> tuple[int, ...]:
        return tuple(pending.milliseconds for pending in self._open)

    @asynccontextmanager
    async def limit(self, milliseconds: int) -> AsyncIterator[None]:
        task = asyncio.current_task()
        assert task is not None
        pending = _Pending(task=task, milliseconds=milliseconds)
        self._open.append(pending)
        try:
            yield
        except asyncio.CancelledError:
            if not pending.expired:
                raise
            task.uncancel()
            raise DeadlineExceeded(f"exceeded the {milliseconds}ms budget") from None
        finally:
            self._open.remove(pending)

    async def sleep(self, milliseconds: int) -> None:
        self.slept_ms.append(milliseconds)
        if self._clock is not None:
            self._clock.advance(milliseconds)
        await asyncio.sleep(0)

    async def expire_open(self) -> tuple[int, ...]:
        """Time out every budget currently open, reporting what those budgets were."""
        budgets = self.open_budgets_ms
        for pending in tuple(self._open):
            pending.expired = True
            pending.task.cancel()
        await asyncio.sleep(0)
        return budgets


class PublicationFailure(RuntimeError):
    """The sink refused a command, the way a broker that is down would."""


class RecordingPublisher:
    """Records every published command and what the store held at publication time."""

    def __init__(self, commands: CommandStore | None = None) -> None:
        self.published: list[BehaviourCommand] = []
        self.sent_bytes: list[str] = []
        self.stored_when_published: list[BehaviourCommand | None] = []
        self.on_publish: Callable[[], None] | None = None
        self.fail_next = 0
        self.attempts = 0
        self._commands = commands
        self._gate: asyncio.Event | None = None

    def bind(self, commands: CommandStore) -> None:
        self._commands = commands

    def hold(self) -> None:
        """Accept nothing until released, so a command can be stored but never sent."""
        self._gate = asyncio.Event()

    def release(self) -> None:
        if self._gate is not None:
            self._gate.set()
            self._gate = None

    async def publish(self, command: StoredCommand) -> None:
        self.attempts += 1
        gate = self._gate
        if gate is not None:
            await gate.wait()
        if self.fail_next > 0:
            self.fail_next -= 1
            raise PublicationFailure(f"attempt {self.attempts} refused")
        if self._commands is not None:
            self.stored_when_published.append(
                await self._commands.stored(command.command.command_id)
            )
        self.published.append(command.command)
        self.sent_bytes.append(command.serialized)
        if self.on_publish is not None:
            self.on_publish()


class GatedProvider:
    """A provider whose calls can be held open, so concurrency is observable.

    It wraps the real deterministic mock rather than replacing it, so what a held call finally
    returns is exactly what an ungated run would have produced.
    """

    def __init__(self, inner: Provider, gated: bool = False) -> None:
        self._inner = inner
        self._gate: asyncio.Event | None = None
        self._in_flight: list[GenerationRequest] = []
        self.started: list[GenerationRequest] = []
        self.peak_in_flight = 0
        self._peak_by_tier: Counter[AttentionTier] = Counter()
        self._peak_by_npc: Counter[str] = Counter()
        if gated:
            self.gate()

    def gate(self) -> None:
        """Hold every call from here on until `release_all`."""
        self._gate = asyncio.Event()

    def release_all(self) -> None:
        if self._gate is not None:
            self._gate.set()
            self._gate = None

    def peak_in_flight_for(self, tier: AttentionTier) -> int:
        return self._peak_by_tier[tier]

    def peak_in_flight_for_npc(self, npc_id: str) -> int:
        return self._peak_by_npc[npc_id]

    async def started_after(self, expected: int) -> int:
        """Wait for ``expected`` calls to start, then for the scheduler to stop starting more.

        The sleeps wait for work to happen; they never decide an assertion. Once the scheduler
        has dispatched everything its limits allow, the count is stable, so the number this
        returns is the same on every run.
        """
        for _ in range(WAIT_TICKS):
            if len(self.started) >= expected:
                break
            await asyncio.sleep(TICK_SECONDS)

        settled = len(self.started)
        for _ in range(SETTLE_TICKS):
            await asyncio.sleep(TICK_SECONDS)
        assert len(self.started) == settled, "the scheduler started more work than it may"
        return settled

    async def generate(self, request: GenerationRequest) -> GeneratedBehaviour:
        self.started.append(request)
        self._in_flight.append(request)
        self._record_peaks()
        gate = self._gate
        try:
            if gate is not None:
                await gate.wait()
            return await self._inner.generate(request)
        finally:
            self._in_flight.remove(request)

    def _record_peaks(self) -> None:
        self.peak_in_flight = max(self.peak_in_flight, len(self._in_flight))
        for tier in {request.tier for request in self._in_flight}:
            running = sum(1 for one in self._in_flight if one.tier is tier)
            self._peak_by_tier[tier] = max(self._peak_by_tier[tier], running)
        for npc_id in {request.npc_id for request in self._in_flight}:
            running = sum(1 for one in self._in_flight if one.npc_id == npc_id)
            self._peak_by_npc[npc_id] = max(self._peak_by_npc[npc_id], running)


class RecordingTelemetry:
    def __init__(self) -> None:
        self.model_calls: list[ModelCallFact] = []

    def record_model_call(self, fact: ModelCallFact) -> None:
        self.model_calls.append(fact)
