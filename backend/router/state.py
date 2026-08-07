"""Previous tiers, session state, sequence tracking and state cleanup.

Owner: Elson & Daniel
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from backend.router.models import (
    AttentionTier,
    RoutingAssignment,
    RoutingSnapshot,
)


@dataclass(frozen=True, slots=True)
class NpcRoutingState:
    """Routing history retained for one currently observed NPC."""

    tier: AttentionTier
    tier_entered_at_ms: int
    last_seen_at_ms: int
    last_focused_at_ms: int | None
    last_reactive_at_ms: int | None


class RouterState:
    """In-memory routing state isolated by session and world."""

    def __init__(self) -> None:
        self._last_sequences: dict[tuple[str, str], int] = {}
        self._npc_states: dict[
            tuple[str, str], dict[str, NpcRoutingState]
        ] = {}

    def last_sequence(self, session_id: str, world_id: str) -> int | None:
        """Return the most recently recorded sequence for a session/world."""
        return self._last_sequences.get((session_id, world_id))

    def npc_states(
        self,
        session_id: str,
        world_id: str,
    ) -> Mapping[str, NpcRoutingState]:
        """Return a read-only snapshot of current candidate state."""
        states = self._npc_states.get((session_id, world_id), {})
        return MappingProxyType(states.copy())

    def record(
        self,
        snapshot: RoutingSnapshot,
        assignments: tuple[RoutingAssignment, ...],
    ) -> None:
        """Record one complete routing decision using the snapshot's source time."""
        key = (snapshot.session_id, snapshot.world_id)
        timestamp_ms = snapshot.timestamp_ms
        previous_states = self._npc_states.get(key, {})
        assignments_by_npc = {
            assignment.npc_id: assignment for assignment in assignments
        }

        current_states: dict[str, NpcRoutingState] = {}
        for npc in snapshot.npcs:
            assignment = assignments_by_npc[npc.npc_id]
            previous = previous_states.get(npc.npc_id)
            if previous is not None and previous.tier == assignment.tier:
                tier_entered_at_ms = previous.tier_entered_at_ms
            else:
                tier_entered_at_ms = timestamp_ms

            current_states[npc.npc_id] = NpcRoutingState(
                tier=assignment.tier,
                tier_entered_at_ms=tier_entered_at_ms,
                last_seen_at_ms=timestamp_ms,
                last_focused_at_ms=(
                    timestamp_ms
                    if assignment.tier == AttentionTier.FOCUSED
                    else previous.last_focused_at_ms if previous is not None else None
                ),
                last_reactive_at_ms=(
                    timestamp_ms
                    if assignment.tier == AttentionTier.REACTIVE
                    else previous.last_reactive_at_ms if previous is not None else None
                ),
            )

        self._last_sequences[key] = snapshot.sequence
        self._npc_states[key] = current_states

    def reset_session(self, session_id: str) -> None:
        """Remove every world's routing state for one session."""
        keys = [key for key in self._last_sequences if key[0] == session_id]
        for key in keys:
            self._last_sequences.pop(key, None)
            self._npc_states.pop(key, None)
