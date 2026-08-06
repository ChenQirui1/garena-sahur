"""Keep conversation state across a restart.

Owner: Jerome & Richard

The state machine itself stays in `conversation_manager`; this is only its durable record, in
the same one-table-one-store shape the event, turn, and command stores already use.

Memory is the working copy and SQLite is the record, so reads stay synchronous for the callers
that already have the answer in hand and only mutations pay for a commit. A session reopened
from disk therefore behaves as though it had never stopped: a player left waiting for an answer
is still waiting for it, rather than silently becoming idle.

A write that cannot land is observed rather than raised. Minecraft's world snapshot is
authoritative for conversation state and is already applied in memory by the time it gets here,
so refusing it because the backend could not write its own projection would discard a fact the
game has already moved past. This is the same rule `intake_service` applies when enrichment
cannot read its events.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from backend.ingestion.durable_store import DurableStore, StorageUnavailable
from backend.ingestion.message_validation import ConversationTurn, validate_conversation_turn
from backend.orchestration.observations import (
    CONVERSATION_STATE_NOT_PERSISTED,
    Observations,
)


@dataclass(frozen=True, slots=True)
class StoredSession:
    session_id: str
    state: str
    active_conversation_id: str | None
    active_target_npc_id: str | None
    unconfirmed_turn: ConversationTurn | None


@dataclass(frozen=True, slots=True)
class StoredThread:
    session_id: str
    conversation_id: str
    started_at_ms: int
    latest_turn_id: str | None


class ConversationStore:
    def __init__(self, store: DurableStore, observations: Observations) -> None:
        self._store = store
        self._observations = observations

    async def save_session(
        self,
        session_id: str,
        state: str,
        active_conversation_id: str | None,
        active_target_npc_id: str | None,
        unconfirmed_turn: ConversationTurn | None,
    ) -> None:
        try:
            connection = self._store.connection
        except StorageUnavailable as unavailable:
            self._not_persisted(session_id, unavailable)
            return
        await connection.execute(
            "INSERT INTO conversation_sessions"
            " (session_id, state, active_conversation_id, active_target_npc_id,"
            "  unconfirmed_turn) VALUES (?, ?, ?, ?, ?)"
            " ON CONFLICT (session_id) DO UPDATE SET"
            "  state = excluded.state,"
            "  active_conversation_id = excluded.active_conversation_id,"
            "  active_target_npc_id = excluded.active_target_npc_id,"
            "  unconfirmed_turn = excluded.unconfirmed_turn",
            (
                session_id,
                state,
                active_conversation_id,
                active_target_npc_id,
                None if unconfirmed_turn is None else unconfirmed_turn.model_dump_json(),
            ),
        )
        await connection.commit()

    async def save_thread(
        self,
        session_id: str,
        conversation_id: str,
        started_at_ms: int,
        latest_turn_id: str | None,
    ) -> None:
        try:
            connection = self._store.connection
        except StorageUnavailable as unavailable:
            self._not_persisted(session_id, unavailable)
            return
        await connection.execute(
            "INSERT INTO conversation_threads"
            " (session_id, conversation_id, started_at_ms, latest_turn_id)"
            " VALUES (?, ?, ?, ?)"
            " ON CONFLICT (session_id, conversation_id) DO UPDATE SET"
            "  latest_turn_id = excluded.latest_turn_id",
            (session_id, conversation_id, started_at_ms, latest_turn_id),
        )
        await connection.commit()

    async def sessions(self) -> tuple[StoredSession, ...]:
        rows = await self._store.connection.execute_fetchall(
            "SELECT session_id, state, active_conversation_id, active_target_npc_id,"
            " unconfirmed_turn FROM conversation_sessions"
        )
        return tuple(_session(row) for row in rows)

    async def threads(self) -> tuple[StoredThread, ...]:
        rows = await self._store.connection.execute_fetchall(
            "SELECT session_id, conversation_id, started_at_ms, latest_turn_id"
            " FROM conversation_threads"
        )
        return tuple(StoredThread(*row) for row in rows)

    def _not_persisted(self, session_id: str, unavailable: StorageUnavailable) -> None:
        self._observations.note(
            CONVERSATION_STATE_NOT_PERSISTED,
            session_id=session_id,
            reason=str(unavailable),
        )


def _session(row: Any) -> StoredSession:
    session_id, state, conversation_id, target_npc_id, unconfirmed = row
    return StoredSession(
        session_id=session_id,
        state=state,
        active_conversation_id=conversation_id,
        active_target_npc_id=target_npc_id,
        unconfirmed_turn=(
            None if unconfirmed is None else validate_conversation_turn(json.loads(unconfirmed))
        ),
    )
