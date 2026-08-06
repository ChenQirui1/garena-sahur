"""Hold a behaviour command durably before anyone tries to publish it.

Owner: Jerome & Richard

Storing first is what lets a restart recover a generated result instead of paying for the model
call again. The per-NPC command sequence is allocated here because it is the store that knows
what has already been issued for that NPC in that session.
"""

from __future__ import annotations

import json
from typing import Any

from backend.ingestion.durable_store import DurableStore
from backend.orchestration.behaviour_command import BehaviourCommand


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

    async def store(self, command: BehaviourCommand) -> None:
        """Commit one command. A clash of identity or sequence raises rather than vanishing."""
        connection = self._store.connection
        await connection.execute(
            "INSERT INTO behaviour_commands"
            " (command_id, session_id, npc_id, command_sequence, created_at_ms,"
            "  expires_at_ms, payload) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                command.command_id,
                command.session_id,
                command.npc_id,
                command.command_sequence,
                command.created_at_ms,
                command.expires_at_ms,
                json.dumps(command.as_payload()),
            ),
        )
        await connection.commit()

    async def latest_for(self, session_id: str, npc_id: str) -> BehaviourCommand | None:
        """The newest command issued for one NPC, which is the behaviour that can expire."""
        rows = list(
            await self._store.connection.execute_fetchall(
                "SELECT payload FROM behaviour_commands WHERE session_id = ? AND npc_id = ?"
                " ORDER BY command_sequence DESC LIMIT 1",
                (session_id, npc_id),
            )
        )
        if not rows:
            return None
        return _from_payload(json.loads(rows[0][0]))

    async def stored(self, command_id: str) -> BehaviourCommand | None:
        rows = list(
            await self._store.connection.execute_fetchall(
                "SELECT payload FROM behaviour_commands WHERE command_id = ?", (command_id,)
            )
        )
        if not rows:
            return None
        return _from_payload(json.loads(rows[0][0]))


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
