"""Store recent turns and prepare promotion/context handoff.

Owner: Jerome & Richard

Only the turns preceding the trigger are history: the triggering turn itself is carried as the
active trigger and must not appear twice in one context.
"""

from __future__ import annotations

from backend.ingestion.message_validation import ConversationTurn
from backend.ingestion.turn_store import StoredTurn, TurnStore


class ConversationHistory:
    def __init__(self, turns: TurnStore) -> None:
        self._turns = turns

    async def before(self, turn: ConversationTurn, limit: int) -> tuple[StoredTurn, ...]:
        """The newest ``limit`` turns of this conversation that precede ``turn``."""
        return await self._turns.recent(
            turn.session_id,
            turn.conversation_id,
            limit=limit,
            before_turn_index=turn.turn_index,
        )
