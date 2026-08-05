"""Manage IDLE, ENGAGED, AWAITING_RESPONSE and READY states.

Owner: Jerome & Richard

Only the router-facing projection exists so far. Issue #6 ingests conversation turns and
brings the internal state machine with it; until then the world snapshot is the sole
authority for which conversation is active, and no turn is pending.
"""

from __future__ import annotations

from backend.ingestion.message_validation import WorldSnapshot
from backend.orchestration.router_port import ActiveConversation

# An active conversation with no pending response is `engaged` in the handoff projection.
# The other three states need the turn stream that arrives with #6.
STATE_WITHOUT_PENDING_RESPONSE = "engaged"


class ActiveConversationProjection:
    """Project Minecraft's active-conversation reference into the Router-facing object."""

    def __init__(self) -> None:
        self._started_at_ms: dict[tuple[str, str], int] = {}

    def observe(self, snapshot: WorldSnapshot) -> ActiveConversation | None:
        """Read the active conversation out of ``snapshot``, keeping its first-seen start."""
        reference = snapshot.active_conversation
        if reference is None:
            return None

        started_at_ms = self._started_at_ms.setdefault(
            (snapshot.session_id, reference.conversation_id), snapshot.timestamp_ms
        )
        return ActiveConversation(
            conversation_id=reference.conversation_id,
            target_npc_id=reference.target_npc_id,
            state=STATE_WITHOUT_PENDING_RESPONSE,
            started_at_ms=started_at_ms,
            latest_turn_id=None,
        )
