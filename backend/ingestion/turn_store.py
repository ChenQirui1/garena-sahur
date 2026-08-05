"""Retain conversation turns durably and deduplicate them by turn identity.

Owner: Jerome & Richard

Turns are never deleted: a session keeps the histories of conversations that are no longer
active, and cleanup is an explicit operation rather than a side effect of a new conversation.
"""

from __future__ import annotations

from dataclasses import dataclass

from backend.ingestion.durable_store import DurableStore
from backend.ingestion.message_validation import ConversationTurn

COLUMNS = (
    "turn_id, session_id, conversation_id, turn_index, timestamp_ms, "
    "speaker_type, speaker_id, target_npc_id, text"
)


@dataclass(frozen=True, slots=True)
class StoredTurn:
    turn_id: str
    session_id: str
    conversation_id: str
    turn_index: int
    timestamp_ms: int
    speaker_type: str
    speaker_id: str
    target_npc_id: str
    text: str


class TurnStore:
    def __init__(self, store: DurableStore) -> None:
        self._store = store

    async def record(self, turn: ConversationTurn) -> bool:
        """Commit ``turn``; report ``False`` when this turn identity was already stored."""
        connection = self._store.connection
        cursor = await connection.execute(
            f"INSERT OR IGNORE INTO conversation_turns ({COLUMNS})"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                turn.turn_id,
                turn.session_id,
                turn.conversation_id,
                turn.turn_index,
                turn.timestamp_ms,
                turn.speaker_type,
                turn.speaker_id,
                turn.target_npc_id,
                turn.text,
            ),
        )
        await connection.commit()
        return cursor.rowcount == 1

    async def recent(
        self,
        session_id: str,
        conversation_id: str,
        limit: int,
        before_turn_index: int | None = None,
    ) -> tuple[StoredTurn, ...]:
        """The newest ``limit`` turns of one conversation, oldest first."""
        if limit <= 0:
            return ()
        rows = await self._store.connection.execute_fetchall(
            f"SELECT {COLUMNS} FROM conversation_turns"
            " WHERE session_id = ? AND conversation_id = ?"
            "   AND (? IS NULL OR turn_index < ?)"
            " ORDER BY turn_index DESC, turn_id DESC LIMIT ?",
            (session_id, conversation_id, before_turn_index, before_turn_index, limit),
        )
        return tuple(StoredTurn(*row) for row in reversed(list(rows)))
