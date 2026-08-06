"""Trigger generation only for a new turn, relevant event, promotion or expiry.

Owner: Jerome & Richard

This slice implements the conversation-turn and relevant-event triggers. Promotion and expiry
arrive with issue #8; each is a separate reason to generate and none of them is a world snapshot,
an unchanged tier, or a routing refresh.

The claim key is the team's recommended deduplication key with one addition: an event's revision.
Specification #1 requires an event claim per identity, revision, and NPC, and without the
revision the first delivery of an event would permanently suppress every later update of it. The
key is internal — it reaches no wire, no Router input, and no telemetry fact — so it stays a
backend decision rather than a shared-contract change.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from backend.ingestion.message_validation import ConversationTurn, GameEvent
from backend.orchestration.router_handoff import RoutingOutcome, RoutingStatus
from backend.orchestration.router_port import AttentionTier

PROMPT_VERSION = "1"

# Ambient behaviour runs locally in Minecraft and never reaches a provider.
GENERATING_TIERS = frozenset({AttentionTier.FOCUSED, AttentionTier.REACTIVE})


@dataclass(frozen=True, slots=True)
class TurnGeneration:
    turn: ConversationTurn
    tier: AttentionTier
    source_sequence: int
    claim_key: str


@dataclass(frozen=True, slots=True)
class EventGeneration:
    event: GameEvent
    npc_id: str
    roles: tuple[str, ...]
    tier: AttentionTier
    source_sequence: int
    claim_key: str


@dataclass(frozen=True, slots=True)
class PolicyDecision:
    generation: TurnGeneration | None = None
    suppressed: str | None = None


@dataclass(frozen=True, slots=True)
class EventPolicyDecision:
    """Who should react to one event revision, and why the rest should not."""

    generations: tuple[EventGeneration, ...] = ()
    suppressed: tuple[tuple[str, str], ...] = ()


def claim_key(
    session_id: str,
    npc_id: str,
    event_id: str | None,
    event_revision: int | None,
    conversation_id: str | None,
    turn_id: str | None,
) -> str:
    return "|".join(
        (
            session_id,
            npc_id,
            event_id or "",
            str(event_revision) if event_revision is not None else "",
            conversation_id or "",
            turn_id or "",
            PROMPT_VERSION,
        )
    )


def decide_for_turn(turn: ConversationTurn, outcome: RoutingOutcome) -> PolicyDecision:
    """Decide whether an accepted player turn warrants one generated behaviour."""
    if outcome.status is not RoutingStatus.ROUTED:
        return PolicyDecision(suppressed=f"routing {outcome.status.value}")

    assignment = next(
        (one for one in outcome.assignments if one.npc_id == turn.target_npc_id), None
    )
    if assignment is None:
        return PolicyDecision(suppressed="target was not a routed candidate")
    if assignment.tier not in GENERATING_TIERS:
        return PolicyDecision(suppressed=f"target is {assignment.tier.value}")

    return PolicyDecision(
        generation=TurnGeneration(
            turn=turn,
            tier=assignment.tier,
            source_sequence=outcome.sequence,
            claim_key=claim_key(
                turn.session_id,
                turn.target_npc_id,
                None,
                None,
                turn.conversation_id,
                turn.turn_id,
            ),
        )
    )


def decide_for_event(
    event: GameEvent,
    outcome: RoutingOutcome,
    roles_by_npc: Mapping[str, tuple[str, ...]],
) -> EventPolicyDecision:
    """Decide which NPCs one event revision warrants a generated reaction from.

    ``roles_by_npc`` holds only the NPCs with a part in *this* event. An NPC relevant to some
    other active event is not a reason to react to this one, so relevance is read per event
    rather than from the aggregate the Router was handed.
    """
    if event.is_terminal:
        return EventPolicyDecision(
            suppressed=tuple(
                (npc_id, f"event {event.status}") for npc_id in sorted(roles_by_npc)
            )
        )
    if outcome.status is not RoutingStatus.ROUTED:
        return EventPolicyDecision(
            suppressed=tuple(
                (npc_id, f"routing {outcome.status.value}") for npc_id in sorted(roles_by_npc)
            )
        )

    assignments = {one.npc_id: one for one in outcome.assignments}
    generations: list[EventGeneration] = []
    suppressed: list[tuple[str, str]] = []
    for npc_id, roles in roles_by_npc.items():
        assignment = assignments.get(npc_id)
        if assignment is None:
            suppressed.append((npc_id, "not a routed candidate"))
            continue
        if assignment.tier not in GENERATING_TIERS:
            suppressed.append((npc_id, f"npc is {assignment.tier.value}"))
            continue
        generations.append(
            EventGeneration(
                event=event,
                npc_id=npc_id,
                roles=roles,
                tier=assignment.tier,
                source_sequence=outcome.sequence,
                claim_key=claim_key(
                    event.session_id,
                    npc_id,
                    event.event_id,
                    event.event_revision,
                    None,
                    None,
                ),
            )
        )

    return EventPolicyDecision(tuple(generations), tuple(suppressed))
