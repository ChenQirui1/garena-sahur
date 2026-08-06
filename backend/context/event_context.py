"""Convert structured game events into relevant prompt context.

Owner: Jerome & Richard

The Router gets a number; the model gets a sentence. Both come from the same structured event,
and neither is written by a model: the text below is assembled deterministically so mock mode,
the component suite, and demo rehearsal all produce the same prompt from the same event.

The NPC's own roles lead the description, because what the NPC should say depends far more on
whether it was robbed than on what the event is called.
"""

from __future__ import annotations

from backend.ingestion.event_store import EventStore
from backend.ingestion.message_validation import (
    EVENT_STATUS_STARTED,
    EVENT_STATUS_UPDATED,
    GameEvent,
    Vector3,
)
from backend.orchestration.event_relevance import (
    ROLE_ACTOR,
    ROLE_NEARBY,
    ROLE_RESPONDER,
    ROLE_TARGET,
    ROLE_WITNESS,
    EventRadii,
    strongest_involvement,
)

INVOLVEMENT_FOR_ROLE = {
    ROLE_ACTOR: "You are the one doing it.",
    ROLE_TARGET: "It is happening to you.",
    ROLE_RESPONDER: "You have been called to deal with it.",
    ROLE_WITNESS: "You saw it happen.",
    ROLE_NEARBY: "You are close enough to notice.",
}

STATUS_FOR_EVENT = {
    EVENT_STATUS_STARTED: "has just begun",
    EVENT_STATUS_UPDATED: "is still going on and has changed",
}


class ActiveEvents:
    """The one event an NPC is most caught up in, told from that NPC's side.

    A conversation can begin at any point during an event, so the turn path has to find its own
    relevant event rather than being handed one the way a reaction is. Several events can be
    running at once and only one slot is available, so the NPC's strongest involvement wins:
    being robbed outranks having watched a brawl, however recent the brawl.
    """

    def __init__(self, events: EventStore, radii: EventRadii) -> None:
        self._events = events
        self._radii = radii

    async def description_for(
        self, session_id: str, npc_id: str, position: Vector3 | None
    ) -> str:
        """One event described, or nothing when none involves this NPC."""
        involvement = strongest_involvement(
            npc_id, position, await self._events.active(session_id), self._radii
        )
        if involvement is None:
            return ""
        stored, roles = involvement
        return describe_event(stored.event, roles)


def describe_event(event: GameEvent, roles: tuple[str, ...]) -> str:
    """One short, bounded account of an event from one NPC's point of view."""
    happening = STATUS_FOR_EVENT.get(event.status, f"is {event.status}")
    lines = [f"A {_readable(event.event_type)} {happening} nearby."]
    lines.extend(
        INVOLVEMENT_FOR_ROLE[role] for role in roles if role in INVOLVEMENT_FOR_ROLE
    )
    return " ".join(lines)


def _readable(event_type: str) -> str:
    return event_type.replace("_", " ")
