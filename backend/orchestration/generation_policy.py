"""Trigger generation only for a new turn, relevant event, promotion or expiry.

Owner: Jerome & Richard

This slice implements the conversation-turn trigger. Relevant events arrive with issue #7, and
promotion and expiry with issue #8; each is a separate reason to generate and none of them is a
world snapshot, an unchanged tier, or a routing refresh.

The claim key is the team's recommended deduplication key, so the same turn cannot be generated
for twice however it is redelivered.
"""

from __future__ import annotations

from dataclasses import dataclass

from backend.ingestion.message_validation import ConversationTurn
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
class PolicyDecision:
    generation: TurnGeneration | None = None
    suppressed: str | None = None


def claim_key(
    session_id: str,
    npc_id: str,
    event_id: str | None,
    conversation_id: str | None,
    turn_id: str | None,
) -> str:
    return "|".join(
        (session_id, npc_id, event_id or "", conversation_id or "", turn_id or "", PROMPT_VERSION)
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
                turn.conversation_id,
                turn.turn_id,
            ),
        )
    )
