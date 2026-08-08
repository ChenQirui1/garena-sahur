"""Temporary directed graph and deterministic one-hop attention propagation.

Owner: Elson & Daniel
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from backend.router.models import AttentionEdge
from backend.router.scoring import ScoredCandidate


@dataclass(frozen=True, slots=True)
class PropagatedCandidate:
    """A directly scored candidate plus its strongest incoming graph signal."""

    candidate: ScoredCandidate
    propagated_score: float
    final_score: float
    propagation_source_npc_id: str | None
    reason: str | None


def propagate_attention(
    candidates: tuple[ScoredCandidate, ...],
    edges: list[AttentionEdge],
    edge_weights: Mapping[str, float],
    graph_decay: float,
) -> tuple[PropagatedCandidate, ...]:
    """Apply deterministic, directed, one-hop propagation in candidate order.

    Every contribution uses the source NPC's direct score. A propagated score is never reused
    as another edge's source, which makes a chain such as A -> B -> C remain exactly one hop.
    """
    candidates_by_id = {
        candidate.npc.npc_id: candidate for candidate in candidates
    }
    strongest_by_target: dict[str, tuple[float, str]] = {}

    if graph_decay > 0:
        for edge in edges:
            if not edge.active or edge.source_npc_id == edge.target_npc_id:
                continue

            source = candidates_by_id.get(edge.source_npc_id)
            target = candidates_by_id.get(edge.target_npc_id)
            if source is None or target is None:
                continue

            edge_weight = edge_weights.get(edge.kind)
            if edge_weight is None or edge_weight <= 0:
                continue

            contribution = (
                min(1.0, source.score.direct_score)
                * edge_weight
                * graph_decay
            )
            if contribution <= 0:
                continue

            current = strongest_by_target.get(edge.target_npc_id)
            if (
                current is None
                or contribution > current[0]
                or (
                    contribution == current[0]
                    and edge.source_npc_id < current[1]
                )
            ):
                strongest_by_target[edge.target_npc_id] = (
                    contribution,
                    edge.source_npc_id,
                )

    propagated: list[PropagatedCandidate] = []
    for candidate in candidates:
        direct_score = candidate.score.direct_score
        strongest = strongest_by_target.get(candidate.npc.npc_id)

        if strongest is None:
            propagated_score = 0.0
            source_npc_id = None
        else:
            propagated_score, source_npc_id = strongest

        final_score = max(direct_score, propagated_score)
        reason = None
        if source_npc_id is not None and propagated_score > direct_score:
            reason = f"one-hop attention from {source_npc_id}"

        propagated.append(
            PropagatedCandidate(
                candidate=candidate,
                propagated_score=propagated_score,
                final_score=final_score,
                propagation_source_npc_id=source_npc_id,
                reason=reason,
            )
        )

    return tuple(propagated)
