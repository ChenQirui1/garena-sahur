"""Assign Focused, Reactive and Ambient tiers under hard capacity limits.

Owner: Elson & Daniel
"""

from __future__ import annotations

from collections.abc import Mapping

from backend.router.config import RouterConfig
from backend.router.models import AttentionTier, RoutingAssignment
from backend.router.scoring import ScoredCandidate

_PREVIOUS_TIER_PRIORITY = {
    AttentionTier.FOCUSED: 2,
    AttentionTier.REACTIVE: 1,
    AttentionTier.AMBIENT: 0,
    None: -1,
}


def assign_tiers(
    candidates: tuple[ScoredCandidate, ...],
    active_conversation_target: str | None,
    previous_tiers: Mapping[str, AttentionTier],
    config: RouterConfig,
    ranking_scores: Mapping[str, float] | None = None,
    hysteresis_reasons: Mapping[str, str] | None = None,
) -> tuple[RoutingAssignment, ...]:
    """Rank candidates deterministically, select within caps, and preserve source order."""
    effective_scores = ranking_scores or {}
    sticky_reasons = hysteresis_reasons or {}
    ranked = sorted(
        candidates,
        key=lambda candidate: _rank_key(
            candidate,
            active_conversation_target,
            previous_tiers,
            effective_scores,
        ),
    )
    by_id = {candidate.npc.npc_id: candidate for candidate in candidates}
    eligible = [
        candidate
        for candidate in ranked
        if effective_scores.get(
            candidate.npc.npc_id, candidate.score.direct_score
        )
        > config.minimum_tier_score
        or candidate.npc.npc_id == active_conversation_target
    ]

    focused_ids: set[str] = set()
    if config.focused_capacity > 0 and active_conversation_target in by_id:
        focused_ids.add(active_conversation_target)

    for candidate in eligible:
        if len(focused_ids) >= config.focused_capacity:
            break
        focused_ids.add(candidate.npc.npc_id)

    reactive_ids: set[str] = set()
    for candidate in eligible:
        npc_id = candidate.npc.npc_id
        if npc_id in focused_ids:
            continue
        if len(reactive_ids) >= config.reactive_capacity:
            break
        reactive_ids.add(npc_id)

    assignments: list[RoutingAssignment] = []
    for candidate in candidates:
        npc_id = candidate.npc.npc_id
        if npc_id in focused_ids:
            tier = AttentionTier.FOCUSED
            selection_reason = "selected within Focused capacity"
        elif npc_id in reactive_ids:
            tier = AttentionTier.REACTIVE
            selection_reason = "selected within Reactive capacity"
        else:
            tier = AttentionTier.AMBIENT
            selection_reason = "outside Focused and Reactive selection"

        previous = previous_tiers.get(npc_id)
        hysteresis_reason = sticky_reasons.get(npc_id)
        reasons = (*candidate.score.reasons, selection_reason)
        if hysteresis_reason:
            reasons = (*reasons, hysteresis_reason)
        assignments.append(
            RoutingAssignment(
                npc_id=npc_id,
                tier=tier,
                previous_tier=previous,
                changed=previous is not None and previous is not tier,
                reasons=reasons,
            )
        )

    return tuple(assignments)


def _rank_key(
    candidate: ScoredCandidate,
    active_conversation_target: str | None,
    previous_tiers: Mapping[str, AttentionTier],
    ranking_scores: Mapping[str, float],
) -> tuple[bool, float, int, float, str]:
    npc_id = candidate.npc.npc_id
    previous_priority = _PREVIOUS_TIER_PRIORITY[previous_tiers.get(npc_id)]
    return (
        npc_id != active_conversation_target,
        -ranking_scores.get(npc_id, candidate.score.direct_score),
        -previous_priority,
        candidate.npc.world_distance_blocks,
        npc_id,
    )
