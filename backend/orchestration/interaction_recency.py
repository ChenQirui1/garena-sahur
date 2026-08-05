"""How recently the player interacted with an NPC, as a bounded Router input.

Owner: Jerome & Richard

This exists so an NPC does not become completely unimportant the instant a conversation ends.
It is a signal, not a trigger: nothing here asks for generation, and the Router still treats the
active conversation target as its own stronger priority.

Time is monotonic rather than wall-clock. The stamps are only ever compared with each other, and
a clock correction mid-demo must not make a recent interaction look twenty seconds old.
"""

from __future__ import annotations

from backend.orchestration.clock import Clock

ACTIVE_RECENCY = 1.0
NO_INTERACTION = 0.0

# The documented stepped decay: each entry is the exclusive upper edge of a band, in
# milliseconds, and the value an interaction inside that band reports.
RECENCY_BANDS = (
    (2_000, 0.9),
    (5_000, 0.7),
    (10_000, 0.4),
    (20_000, 0.2),
)


class InteractionRecency:
    """Per-session, per-NPC interaction stamps and the decay read off them.

    In-memory like world state: recency describes the live scene, and a backend that has just
    restarted has no basis for claiming the player spoke to anyone recently.
    """

    def __init__(self, clock: Clock) -> None:
        self._clock = clock
        self._interacted_at_ms: dict[tuple[str, str], int] = {}

    def note_interaction(self, session_id: str, npc_id: str) -> None:
        self._interacted_at_ms[(session_id, npc_id)] = self._clock.monotonic_ms()

    def value_for(self, session_id: str, npc_id: str, active_target: str | None) -> float:
        """The decayed value, or 1.0 while this NPC is the one the player is talking to."""
        if npc_id == active_target:
            return ACTIVE_RECENCY

        interacted_at_ms = self._interacted_at_ms.get((session_id, npc_id))
        if interacted_at_ms is None:
            return NO_INTERACTION
        return _decayed(self._clock.monotonic_ms() - interacted_at_ms)


def _decayed(elapsed_ms: int) -> float:
    return next(
        (value for edge_ms, value in RECENCY_BANDS if elapsed_ms < edge_ms), NO_INTERACTION
    )
