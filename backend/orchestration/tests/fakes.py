"""Contract fakes for the adapters the owned trace ends at.

Owner: Jerome & Richard

Passing against these proves the owned pipeline only; Minecraft command application and
Elson & Daniel's telemetry aggregation are separate integration gates.
"""

from __future__ import annotations

from typing import Callable

from backend.orchestration.behaviour_command import BehaviourCommand
from backend.orchestration.command_store import CommandStore
from backend.orchestration.telemetry_port import ModelCallFact


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


class RecordingPublisher:
    """Records every published command and what the store held at publication time."""

    def __init__(self, commands: CommandStore | None = None) -> None:
        self.published: list[BehaviourCommand] = []
        self.stored_when_published: list[BehaviourCommand | None] = []
        self.on_publish: Callable[[], None] | None = None
        self._commands = commands

    def bind(self, commands: CommandStore) -> None:
        self._commands = commands

    async def publish(self, command: BehaviourCommand) -> None:
        if self._commands is not None:
            self.stored_when_published.append(await self._commands.stored(command.command_id))
        self.published.append(command)
        if self.on_publish is not None:
            self.on_publish()


class RecordingTelemetry:
    def __init__(self) -> None:
        self.model_calls: list[ModelCallFact] = []

    def record_model_call(self, fact: ModelCallFact) -> None:
        self.model_calls.append(fact)
