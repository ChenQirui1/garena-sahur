"""Publish dialogue/action commands back to Minecraft, and keep trying while they are current.

Owner: Jerome & Richard

Publication is stored-then-sent, always in that order, so a restart can find a generated result
instead of paying for the model call again. Until the live command transport exists (issues #4
and #11), the port below is satisfied by a development sink.

A failed send is retried on the specified cadence, but only while the command is still inside
its 15-second acceptance lifetime. Past that, Minecraft would reject it anyway, so the attempt
stops and the conversation is released rather than regenerated: the model call was already spent
and repeating it is exactly what this ticket exists to prevent.
"""

from __future__ import annotations

import logging
from typing import Protocol

from backend.orchestration.behaviour_command import BehaviourCommand, PayloadTooLarge
from backend.orchestration.clock import Clock, Deadlines
from backend.orchestration.command_store import CommandStore, StoredCommand
from backend.orchestration.observations import (
    COMMAND_PAYLOAD_TOO_LARGE,
    COMMAND_PUBLICATION_EXPIRED,
    COMMAND_PUBLICATION_RETRIED,
    Observations,
)

logger = logging.getLogger(__name__)


class PublisherPort(Protocol):
    async def publish(self, command: StoredCommand) -> None: ...


class LoggingPublisher:
    """The development stand-in for Ivan's command consumer."""

    async def publish(self, command: StoredCommand) -> None:
        logger.info("behaviour_command %s", command.serialized)


class BehaviourPublisher:
    def __init__(
        self,
        commands: CommandStore,
        publisher: PublisherPort,
        deadlines: Deadlines,
        clock: Clock,
        observations: Observations,
        retry_delays_ms: tuple[int, ...],
    ) -> None:
        self._commands = commands
        self._publisher = publisher
        self._deadlines = deadlines
        self._clock = clock
        self._observations = observations
        self._retry_delays_ms = retry_delays_ms

    async def publish(self, command: BehaviourCommand) -> bool:
        """Commit the command, then deliver it — never the other way round.

        A command too large for the consumer to read is not sent and not stored. That is the
        backend declining to emit something unusable, not the backend rejecting a command:
        deciding whether to *apply* one stays Minecraft's.
        """
        try:
            stored = await self._commands.store(command)
        except PayloadTooLarge as oversized:
            self._observations.note(
                COMMAND_PAYLOAD_TOO_LARGE,
                session_id=command.session_id,
                npc_id=command.npc_id,
                command_id=command.command_id,
                reason=str(oversized),
            )
            return False
        return await self.deliver(stored)

    async def deliver(self, stored: StoredCommand) -> bool:
        """Send one already-committed command, retrying while it stays current.

        Recovery calls this directly: a command found unpublished after a restart is already
        stored, and storing it again would raise on its own identity.
        """
        attempt = 0
        # The first attempt waits for nothing; after that the cadence repeats unchanged, so a
        # long-suffering command is retried at the same rate rather than backing off for ever.
        delays_ms = (0, *self._retry_delays_ms)
        while True:
            for delay_ms in delays_ms:
                if delay_ms:
                    await self._deadlines.sleep(delay_ms)
                if self._clock.now_ms() >= stored.command.expires_at_ms:
                    await self._expire(stored)
                    return False
                if await self._send(stored, attempt):
                    return True
                attempt += 1
            delays_ms = self._retry_delays_ms

    async def _send(self, stored: StoredCommand, attempt: int) -> bool:
        try:
            await self._publisher.publish(stored)
        except Exception as undeliverable:
            self._observations.note(
                COMMAND_PUBLICATION_RETRIED,
                session_id=stored.command.session_id,
                command_id=stored.command.command_id,
                attempt=attempt + 1,
                reason=repr(undeliverable),
            )
            return False

        await self._commands.mark_published(
            stored.command.command_id, self._clock.now_ms()
        )
        return True

    async def _expire(self, stored: StoredCommand) -> None:
        await self._commands.mark_expired(stored.command.command_id)
        self._observations.note(
            COMMAND_PUBLICATION_EXPIRED,
            session_id=stored.command.session_id,
            npc_id=stored.command.npc_id,
            command_id=stored.command.command_id,
            expires_at_ms=stored.command.expires_at_ms,
        )
