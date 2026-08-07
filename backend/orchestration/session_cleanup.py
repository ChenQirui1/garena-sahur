"""Erase one session's evidence, but only when someone asks for it.

Owner: Jerome & Richard

Specification #1 wants "an explicit cleanup operation rather than automatic history deletion",
which is a rule about what startup must *not* do as much as about what this does. Nothing else in
the backend deletes durable state: a new conversation, a reopened database, and a restart all
leave every row alone, so the only way evidence disappears is a deliberate call to this.

Every durable table names its session in a column, so removal is one comparison per table and
never a match against the shape of a key. Nothing constrains the characters in a session id, and
a pattern would read `_` and `%` in one as wildcards standing for another session's evidence.

It crosses every durable store plus the Router's own per-session state, which is why it is here
rather than on any one of them. Router state matters because previous tiers and accepted
sequences would otherwise outlive the session they describe, and a fresh demo run on the same
session identifier would inherit hysteresis from the last one.
"""

from __future__ import annotations

from dataclasses import dataclass

from backend.ingestion.durable_store import SESSION_TABLES, DurableStore
from backend.ingestion.world_state_store import WorldStateStore
from backend.orchestration.conversation_manager import ConversationManager
from backend.orchestration.observations import SESSION_CLEANED, Observations
from backend.orchestration.router_handoff import RouterHandoff


@dataclass(frozen=True, slots=True)
class Cleaned:
    """How many durable rows the caller asked to be removed, per table."""

    session_id: str
    rows_by_table: dict[str, int]

    @property
    def total_rows(self) -> int:
        return sum(self.rows_by_table.values())


class SessionCleanup:
    def __init__(
        self,
        store: DurableStore,
        world_state: WorldStateStore,
        conversation: ConversationManager,
        handoff: RouterHandoff,
        observations: Observations,
    ) -> None:
        self._store = store
        self._world_state = world_state
        self._conversation = conversation
        self._handoff = handoff
        self._observations = observations

    async def clean(self, session_id: str) -> Cleaned:
        """Remove everything the backend retains about one session, durable and in memory."""
        connection = self._store.connection
        removed: dict[str, int] = {}
        for table in SESSION_TABLES:
            cursor = await connection.execute(
                f"DELETE FROM {table} WHERE session_id = ?", (session_id,)
            )
            removed[table] = cursor.rowcount
        await connection.commit()

        self._world_state.forget(session_id)
        self._conversation.forget(session_id)
        self._handoff.reset_session(session_id)

        cleaned = Cleaned(session_id=session_id, rows_by_table=removed)
        self._observations.note(
            SESSION_CLEANED, session_id=session_id, rows=cleaned.total_rows
        )
        return cleaned
