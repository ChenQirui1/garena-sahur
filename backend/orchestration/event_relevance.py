"""Translate active events into the bounded per-NPC signals the Router consumes.

Owner: Jerome & Richard

The Router must not read an event narrative, so the backend turns each active event into two
small facts per NPC: readable roles and one number. Everything past that number — weighting it,
propagating it, turning it into a tier — is Elson & Daniel's.

Roles come from the event's own actor, target, and responder arrays plus two positional roles.
A witness is settled once, when the event starts, because "who saw this happen" cannot be
recovered from a later snapshot; being merely nearby is re-read from the current snapshot, so an
NPC that wanders into the square is nearby but never becomes a witness after the fact.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import dist

from backend.ingestion.event_store import StoredEvent
from backend.ingestion.message_validation import GameEvent, NpcObservation, Vector3

ROLE_ACTOR = "actor"
ROLE_TARGET = "target"
ROLE_RESPONDER = "responder"
ROLE_WITNESS = "witness"
ROLE_NEARBY = "nearby"

# The handoff contract's recommended starting table, kept in one place because it says to keep
# the values configurable. `unrelated` is absent by design: it is expressed as no roles at all.
EVENT_RELEVANCE_BY_ROLE = {
    ROLE_ACTOR: 1.0,
    ROLE_TARGET: 1.0,
    ROLE_RESPONDER: 0.8,
    ROLE_WITNESS: 0.4,
    ROLE_NEARBY: 0.2,
}
UNRELATED_RELEVANCE = 0.0

# The order roles are reported in, so one NPC's roles read the same way on every call.
ROLE_ORDER = (ROLE_ACTOR, ROLE_TARGET, ROLE_RESPONDER, ROLE_WITNESS, ROLE_NEARBY)


@dataclass(frozen=True, slots=True)
class EventRadii:
    """How close an NPC must be to have witnessed an event, and to count as nearby."""

    witness_blocks: float
    nearby_blocks: float


@dataclass(frozen=True, slots=True)
class NpcEventEnrichment:
    event_relevance: float
    event_roles: list[str]


def witnesses_at_start(
    event: GameEvent, npcs: tuple[NpcObservation, ...], radii: EventRadii
) -> frozenset[str]:
    """The candidates close enough to have seen ``event`` when it started.

    Called once per event. With no world state yet there are no candidates and the set is
    empty, which is correct and permanent: nothing later can establish who was present.
    """
    return frozenset(
        observation.npc_id
        for observation in npcs
        if _blocks_between(observation.position, event.position) <= radii.witness_blocks
    )


def enrichment_for(
    npc_id: str,
    position: Vector3,
    active: tuple[StoredEvent, ...],
    radii: EventRadii,
) -> NpcEventEnrichment:
    """One relevance and one role list for an NPC across every active event.

    Relevance is the maximum over roles and then over events, so several weak involvements
    never add up to a strong one. Roles are the union, because they stay readable evidence for
    why the number is what it is.
    """
    roles = {role for event in active for role in roles_in(npc_id, position, event, radii)}
    return NpcEventEnrichment(
        event_relevance=relevance_of(roles),
        event_roles=[role for role in ROLE_ORDER if role in roles],
    )


def roles_in(
    npc_id: str, position: Vector3, stored: StoredEvent, radii: EventRadii
) -> frozenset[str]:
    """Every part ``npc_id`` currently plays in one active event."""
    event = stored.event
    roles = set()
    if npc_id in event.actor_npc_ids:
        roles.add(ROLE_ACTOR)
    if npc_id in event.target_npc_ids:
        roles.add(ROLE_TARGET)
    if npc_id in event.responder_npc_ids:
        roles.add(ROLE_RESPONDER)
    if npc_id in stored.witnesses:
        roles.add(ROLE_WITNESS)

    if not roles and _blocks_between(position, event.position) <= radii.nearby_blocks:
        roles.add(ROLE_NEARBY)
    return frozenset(roles)


def relevance_of(roles: frozenset[str] | set[str]) -> float:
    """The strongest role wins; no role at all is what `unrelated` means."""
    return max(
        (EVENT_RELEVANCE_BY_ROLE[role] for role in roles if role in EVENT_RELEVANCE_BY_ROLE),
        default=UNRELATED_RELEVANCE,
    )


def _blocks_between(one: Vector3, other: Vector3) -> float:
    return dist((one.x, one.y, one.z), (other.x, other.y, other.z))
