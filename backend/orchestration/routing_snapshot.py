"""Build the enriched routing snapshot handed to the Router.

Owner: Jerome & Richard
"""

from __future__ import annotations

from backend.ingestion.message_validation import WorldSnapshot
from backend.orchestration.router_port import (
    RoutingAttentionEdge,
    RoutingCandidate,
    RoutingSnapshot,
)


def build_routing_snapshot(snapshot: WorldSnapshot) -> RoutingSnapshot:
    active_npc_id = snapshot.active_conversation.npc_id if snapshot.active_conversation else None
    return RoutingSnapshot(
        session_id=snapshot.session_id,
        world_id=snapshot.world_id,
        source_sequence=snapshot.sequence,
        observed_at_ms=snapshot.observed_at_ms,
        candidates=tuple(
            RoutingCandidate(
                npc_id=candidate.npc_id,
                world_distance=candidate.world_distance,
                viewport_center_distance=candidate.viewport_center_distance,
                visible=candidate.visible,
                line_of_sight=candidate.line_of_sight,
                in_active_conversation=candidate.npc_id == active_npc_id,
            )
            for candidate in snapshot.candidates
        ),
        active_conversation_npc_id=active_npc_id,
        attention_edges=tuple(
            RoutingAttentionEdge(
                source_npc_id=edge.source_npc_id,
                target_npc_id=edge.target_npc_id,
                relation=edge.relation,
            )
            for edge in snapshot.attention_edges
        ),
    )
