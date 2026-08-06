"""Hold a behaviour command durably before anyone tries to publish it.

Owner: Jerome & Richard

Storing first is what lets a restart recover a generated result instead of paying for the model
call again. The per-NPC command sequence is allocated here because it is the store that knows
what has already been issued for that NPC in that session.

The command is serialized once, at the moment it is stored, and every later attempt publishes
that same string. Re-serializing per attempt would look identical today and stop being identical
the first time a field's rendering changes, which is exactly the guarantee Minecraft is being
promised: a retry is the same command, not an equivalent one.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from backend.ingestion.durable_store import DurableStore
from backend.orchestration.behaviour_command import BehaviourCommand

PENDING = "pending"
PUBLISHED = "published"
EXPIRED = "expired"


@dataclass(frozen=True, slots=True)
class StoredCommand:
    """A committed command and the exact bytes every publication attempt must send."""

    command: BehaviourCommand
    serialized: str


class CommandStore:
    def __init__(self, store: DurableStore) -> None:
        self._store = store

    async def next_sequence(self, session_id: str, npc_id: str) -> int:
        rows = await self._store.connection.execute_fetchall(
            "SELECT COALESCE(MAX(command_sequence), 0) FROM behaviour_commands"
            " WHERE session_id = ? AND npc_id = ?",
            (session_id, npc_id),
        )
        return int(list(rows)[0][0]) + 1

    async def store(self, command: BehaviourCommand) -> StoredCommand:
        """Commit one command. A clash of identity or sequence raises rather than vanishing."""
        serialized = json.dumps(command.as_payload())
        connection = self._store.connection
        await connection.execute(
            "INSERT INTO behaviour_commands"
            " (command_id, session_id, npc_id, command_sequence, created_at_ms,"
            "  expires_at_ms, payload, publication_status, published_at_ms)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL)",
            (
                command.command_id,
                command.session_id,
                command.npc_id,
                command.command_sequence,
                command.created_at_ms,
                command.expires_at_ms,
                serialized,
                PENDING,
            ),
        )
        await connection.commit()
        return StoredCommand(command=command, serialized=serialized)

    async def mark_published(self, command_id: str, at_ms: int) -> None:
        await self._set_status(command_id, PUBLISHED, at_ms)

    async def mark_expired(self, command_id: str) -> None:
        """A command nobody could deliver inside its lifetime. The row stays as evidence."""
        await self._set_status(command_id, EXPIRED, None)

    async def latest_for(self, session_id: str, npc_id: str) -> BehaviourCommand | None:
        """The newest command issued for one NPC, which is the behaviour that can expire."""
        rows = await self._rows(
            "WHERE session_id = ? AND npc_id = ? ORDER BY command_sequence DESC LIMIT 1",
            (session_id, npc_id),
        )
        return rows[0].command if rows else None

    async def stored(self, command_id: str) -> BehaviourCommand | None:
        rows = await self._rows("WHERE command_id = ?", (command_id,))
        return rows[0].command if rows else None

    async def unpublished(self) -> tuple[StoredCommand, ...]:
        """Commands committed but never delivered, oldest first, for restart recovery."""
        return await self._rows(
            "WHERE publication_status = ? ORDER BY created_at_ms, command_sequence",
            (PENDING,),
        )

    async def _rows(self, clause: str, parameters: tuple[Any, ...]) -> tuple[StoredCommand, ...]:
        rows = await self._store.connection.execute_fetchall(
            f"SELECT payload FROM behaviour_commands {clause}", parameters
        )
        return tuple(
            StoredCommand(command=_from_payload(json.loads(row[0])), serialized=row[0])
            for row in rows
        )

    async def _set_status(self, command_id: str, status: str, at_ms: int | None) -> None:
        connection = self._store.connection
        await connection.execute(
            "UPDATE behaviour_commands SET publication_status = ?, published_at_ms = ?"
            " WHERE command_id = ?",
            (status, at_ms, command_id),
        )
        await connection.commit()


def _from_payload(payload: dict[str, Any]) -> BehaviourCommand:
    return BehaviourCommand(
        session_id=payload["session_id"],
        command_id=payload["command_id"],
        request_id=payload["request_id"],
        npc_id=payload["npc_id"],
        tier=payload["tier"],
        event_id=payload["event_id"],
        conversation_id=payload["conversation_id"],
        turn_id=payload["turn_id"],
        source_sequence=payload["source_sequence"],
        created_at_ms=payload["created_at_ms"],
        expires_at_ms=payload["expires_at_ms"],
        dialogue=payload["dialogue"],
        action=payload["action"],
        fallback_used=payload["fallback_used"],
        command_sequence=payload["command_sequence"],
    )
