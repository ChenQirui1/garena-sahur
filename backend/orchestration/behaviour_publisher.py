"""Publish dialogue/action commands back to Minecraft.

Owner: Jerome & Richard

Publication is stored-then-sent, always in that order. Until the live command transport exists
(issues #4 and #11), the port below is satisfied by a development sink; the ordering guarantee
is the part that is real today.
"""

from __future__ import annotations

import logging
from typing import Protocol

from backend.orchestration.behaviour_command import BehaviourCommand
from backend.orchestration.command_store import CommandStore

logger = logging.getLogger(__name__)


class PublisherPort(Protocol):
    async def publish(self, command: BehaviourCommand) -> None: ...


class LoggingPublisher:
    """The development stand-in for Ivan's command consumer."""

    async def publish(self, command: BehaviourCommand) -> None:
        logger.info("behaviour_command %s", command.as_payload())


class BehaviourPublisher:
    def __init__(self, commands: CommandStore, publisher: PublisherPort) -> None:
        self._commands = commands
        self._publisher = publisher

    async def publish(self, command: BehaviourCommand) -> None:
        """Commit the command, then hand it downstream — never the other way round."""
        await self._commands.store(command)
        await self._publisher.publish(command)
