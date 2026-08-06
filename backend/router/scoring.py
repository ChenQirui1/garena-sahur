"""Calculate transparent direct-attention scores for Router candidates.

Owner: Elson & Daniel
"""

from __future__ import annotations

from dataclasses import dataclass

from backend.router.config import RouterConfig
from backend.router.models import RoutingNpc, RoutingSnapshot


@dataclass(frozen=True, slots=True)
class ScoreBreakdown:
    """The normalized signals and weighted direct score for one NPC."""

    viewport: float
    proximity: float
    event_relevance: float
    interaction_recency: float
    active_conversation: bool
    direct_score: float
    reasons: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ScoredCandidate:
    """A Router candidate paired with its direct scoring evidence."""

    npc: RoutingNpc
    score: ScoreBreakdown


def viewport_score(npc: RoutingNpc) -> float:
    """Return player-view attention, gated by viewport membership and line of sight."""
    if not npc.inside_viewport or not npc.line_of_sight:
        return 0.0
    return max(0.0, 1.0 - npc.viewport_center_distance)


def proximity_score(npc: RoutingNpc, max_distance_blocks: float) -> float:
    """Normalize distance to the configured maximum relevant range."""
    return max(0.0, 1.0 - npc.world_distance_blocks / max_distance_blocks)


def score_candidate(
    npc: RoutingNpc,
    active_conversation_target: str | None,
    config: RouterConfig,
) -> ScoredCandidate:
    """Calculate the documented deterministic score and readable signal reasons."""
    view = viewport_score(npc)
    proximity = proximity_score(npc, config.max_relevant_distance_blocks)
    is_active_target = npc.npc_id == active_conversation_target

    direct_score = (
        config.viewport_weight * view
        + config.proximity_weight * proximity
        + config.event_relevance_weight * npc.event_relevance
        + config.interaction_recency_weight * npc.interaction_recency
        + (config.active_conversation_bonus if is_active_target else 0.0)
    )

    reasons: list[str] = []
    if is_active_target:
        reasons.append("active conversation")
    if view > 0:
        reasons.append("near viewport centre")
    if proximity > 0:
        reasons.append("within relevant distance")
    if npc.event_relevance > 0:
        reasons.append(_event_reason(npc.event_roles))
    if npc.interaction_recency > 0:
        reasons.append("recent interaction")

    return ScoredCandidate(
        npc=npc,
        score=ScoreBreakdown(
            viewport=view,
            proximity=proximity,
            event_relevance=npc.event_relevance,
            interaction_recency=npc.interaction_recency,
            active_conversation=is_active_target,
            direct_score=direct_score,
            reasons=tuple(reasons),
        ),
    )


def score_snapshot(
    snapshot: RoutingSnapshot, config: RouterConfig
) -> tuple[ScoredCandidate, ...]:
    """Score every candidate in source order."""
    conversation = snapshot.active_conversation
    target = conversation.target_npc_id if conversation else None
    return tuple(score_candidate(npc, target, config) for npc in snapshot.npcs)


def _event_reason(roles: list[str]) -> str:
    normalized = {role.lower() for role in roles}
    if "target" in normalized:
        return "direct event target"
    if "actor" in normalized:
        return "direct event actor"
    if "responder" in normalized:
        return "event responder"
    if "witness" in normalized:
        return "event witness"
    return "relevant event"
