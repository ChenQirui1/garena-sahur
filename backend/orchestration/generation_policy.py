"""Trigger generation only for a new turn, relevant event, promotion or expiry.

Owner: Jerome & Richard

These four are the whole trigger set. A world snapshot, an unchanged tier, a score change, a
demotion, an Ambient assignment, an event ending, and a promotion an unexpired command already
satisfies are all reasons *not* to call a provider, and each has its own suppression below.

Promotion and expiry are noticed when a world snapshot is routed, not by a timer. That follows
ADR 0007: a wait bounded by arrivals is reproducible, and a wall-clock timer would make the same
sequence of messages produce different results on different runs. The snapshot is the tick; it is
still never a trigger by itself.

The claim key is the team's recommended deduplication key with two additions: an event's revision,
and the trigger name that owns the rest of the key. Specification #1 requires an event claim per
identity, revision, and NPC, and without the revision the first delivery of an event would
permanently suppress every later update of it. Without the trigger name a promotion and a turn for
the same NPC would collide. The key is internal — it reaches no wire, no Router input, and no
telemetry fact — so it stays a backend decision rather than a shared-contract change.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Mapping

from backend.ingestion.message_validation import ConversationTurn, GameEvent
from backend.orchestration.behaviour_command import BehaviourCommand
from backend.orchestration.router_handoff import RoutingOutcome, RoutingStatus
from backend.orchestration.router_port import AttentionTier, RoutingAssignment

PROMPT_VERSION = "1"

# Ambient behaviour runs locally in Minecraft and never reaches a provider.
GENERATING_TIERS = frozenset({AttentionTier.FOCUSED, AttentionTier.REACTIVE})

# Upward means more foreground attention, which is the only direction that can warrant a call.
TIER_RANK = {AttentionTier.AMBIENT: 0, AttentionTier.REACTIVE: 1, AttentionTier.FOCUSED: 2}

# A backend decision taken under silence (ADR 0009), so it says so where it can be seen: an NPC
# that was promoted or whose behaviour expired stays quiet when there is nothing to speak about.
NOTHING_TO_SPEAK_ABOUT = "no active event or conversation requires foreground behaviour"


class Trigger(StrEnum):
    TURN = "turn"
    EVENT = "event"
    PROMOTION = "promotion"
    EXPIRY = "expiry"


@dataclass(frozen=True, slots=True)
class Generation:
    """One unit of pending generation work, whatever asked for it.

    ``trigger`` says why it exists; ``turn`` or ``event`` says what it is about. Promotion and
    expiry are reasons to speak rather than things to speak about, so each carries whichever of
    the two still requires foreground behaviour from this NPC. That is also why they reuse the
    conversation and event context paths instead of introducing a third.
    """

    trigger: Trigger
    session_id: str
    world_id: str
    npc_id: str
    tier: AttentionTier
    source_sequence: int
    claim_key: str

    turn: ConversationTurn | None = None
    event: GameEvent | None = None
    roles: tuple[str, ...] = ()
    expired_command_id: str | None = None


@dataclass(frozen=True, slots=True)
class Focus:
    """What still requires foreground behaviour from one NPC right now."""

    turn: ConversationTurn | None = None
    event: GameEvent | None = None
    roles: tuple[str, ...] = ()

    @property
    def exists(self) -> bool:
        return self.turn is not None or self.event is not None


@dataclass(frozen=True, slots=True)
class PolicyDecision:
    generation: Generation | None = None
    suppressed: str | None = None


@dataclass(frozen=True, slots=True)
class EventPolicyDecision:
    """Who should react to one event revision, and why the rest should not."""

    generations: tuple[Generation, ...] = ()
    suppressed: tuple[tuple[str, str], ...] = ()


def claim_key(
    trigger: Trigger, session_id: str, npc_id: str, *parts: str | int | None
) -> str:
    """What makes this work distinct, so the same work cannot be generated for twice."""
    return "|".join(
        (
            trigger.value,
            session_id,
            npc_id,
            *("" if part is None else str(part) for part in parts),
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
        generation=Generation(
            trigger=Trigger.TURN,
            session_id=turn.session_id,
            world_id=outcome.world_id,
            npc_id=turn.target_npc_id,
            tier=assignment.tier,
            source_sequence=outcome.sequence,
            claim_key=claim_key(
                Trigger.TURN, turn.session_id, turn.target_npc_id, turn.conversation_id, turn.turn_id
            ),
            turn=turn,
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
    generations: list[Generation] = []
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
            Generation(
                trigger=Trigger.EVENT,
                session_id=event.session_id,
                world_id=outcome.world_id,
                npc_id=npc_id,
                tier=assignment.tier,
                source_sequence=outcome.sequence,
                claim_key=claim_key(
                    Trigger.EVENT,
                    event.session_id,
                    npc_id,
                    event.event_id,
                    event.event_revision,
                ),
                event=event,
                roles=roles,
            )
        )

    return EventPolicyDecision(tuple(generations), tuple(suppressed))


@dataclass(frozen=True, slots=True)
class CurrentFacts:
    """What the stores say right now about the reason a piece of work exists."""

    routed: bool
    assignment: RoutingAssignment | None
    stored_event: GameEvent | None = None
    latest_behaviour: BehaviourCommand | None = None


def is_still_current(work: Generation, facts: CurrentFacts) -> str | None:
    """Why this work is no longer worth doing, or ``None`` while it still is.

    Deciding whether to generate and deciding whether to *still* generate are the same question
    asked at two different times, so both live here. The caller gathers the facts, because only
    it can reach the stores.
    """
    if not facts.routed:
        return "no current routing result"
    if facts.assignment is None:
        return "npc is no longer a routed candidate"
    if facts.assignment.tier not in GENERATING_TIERS:
        return f"npc is {facts.assignment.tier.value}"

    if work.event is not None:
        if facts.stored_event is None:
            return "the event is no longer stored"
        if facts.stored_event.is_terminal:
            return f"event {facts.stored_event.status}"
        if (
            work.trigger is Trigger.EVENT
            and facts.stored_event.event_revision != work.event.event_revision
        ):
            return "a newer revision superseded this one"

    if work.trigger is Trigger.EXPIRY and facts.latest_behaviour is not None:
        if facts.latest_behaviour.command_id != work.expired_command_id:
            return "newer behaviour replaced the expired command"

    return None


def decide_for_promotion(
    assignment: RoutingAssignment,
    outcome: RoutingOutcome,
    focus: Focus,
    current_behaviour: BehaviourCommand | None,
    now_ms: int,
) -> PolicyDecision:
    """An upward promotion warrants generation only while nothing current already covers it.

    "Lacking suitable current behaviour" is not defined by any source. The backend cannot judge
    whether existing dialogue suits a new tier, so it reads the phrase as the one thing it can
    observe: the NPC has no unexpired command.

    An empty decision means this was never a promotion; a suppressed one means it was, and says
    why it produced nothing. Only the second is worth reporting — the first happens for every
    unchanged candidate on every snapshot.
    """
    if assignment.tier not in GENERATING_TIERS:
        return PolicyDecision()
    if not assignment.changed or assignment.previous_tier is None:
        return PolicyDecision()
    if TIER_RANK[assignment.tier] <= TIER_RANK[assignment.previous_tier]:
        return PolicyDecision()
    if current_behaviour is not None and now_ms < current_behaviour.expires_at_ms:
        return PolicyDecision(suppressed="current behaviour already satisfies the promotion")
    if not focus.exists:
        return PolicyDecision(suppressed=NOTHING_TO_SPEAK_ABOUT)

    return PolicyDecision(
        generation=Generation(
            trigger=Trigger.PROMOTION,
            session_id=outcome.session_id,
            world_id=outcome.world_id,
            npc_id=assignment.npc_id,
            tier=assignment.tier,
            source_sequence=outcome.sequence,
            claim_key=claim_key(
                Trigger.PROMOTION, outcome.session_id, assignment.npc_id, outcome.sequence
            ),
            turn=focus.turn,
            event=focus.event,
            roles=focus.roles,
        )
    )


def decide_for_expiry(
    assignment: RoutingAssignment,
    outcome: RoutingOutcome,
    focus: Focus,
    latest_behaviour: BehaviourCommand | None,
    now_ms: int,
) -> PolicyDecision:
    """Expired behaviour warrants generation only while something still needs foreground work."""
    if assignment.tier not in GENERATING_TIERS:
        return PolicyDecision()
    if latest_behaviour is None or now_ms < latest_behaviour.expires_at_ms:
        return PolicyDecision()
    if not focus.exists:
        return PolicyDecision(suppressed=NOTHING_TO_SPEAK_ABOUT)

    return PolicyDecision(
        generation=Generation(
            trigger=Trigger.EXPIRY,
            session_id=outcome.session_id,
            world_id=outcome.world_id,
            npc_id=assignment.npc_id,
            tier=assignment.tier,
            source_sequence=outcome.sequence,
            claim_key=claim_key(
                Trigger.EXPIRY,
                outcome.session_id,
                assignment.npc_id,
                latest_behaviour.command_id,
            ),
            turn=focus.turn,
            event=focus.event,
            roles=focus.roles,
            expired_command_id=latest_behaviour.command_id,
        )
    )
