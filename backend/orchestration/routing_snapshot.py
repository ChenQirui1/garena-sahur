"""Build the enriched routing snapshot handed to the Router.

Owner: Jerome & Richard
"""

from __future__ import annotations

from backend.ingestion.message_validation import WorldSnapshot
from backend.orchestration.router_port import (
    SNAPSHOT_SCHEMA_VERSION,
    SNAPSHOT_TYPE,
    ActiveConversation,
    AttentionEdge,
    CandidatePolicy,
    RoutingNpc,
    RoutingSnapshot,
)

# Event relevance, event roles, and interaction recency are derived by #6 and #7. The Router
# input must keep the same shape between calls, so they carry their documented empty values
# rather than being omitted: no role, no relevance, no interaction.
NO_EVENT_RELEVANCE = 0.0
NO_INTERACTION_RECENCY = 0.0


def build_routing_snapshot(
    snapshot: WorldSnapshot, active_conversation: ActiveConversation | None
) -> RoutingSnapshot:
    """Enrich one validated world snapshot, preserving its source sequence and timestamp."""
    return RoutingSnapshot(
        schema_version=SNAPSHOT_SCHEMA_VERSION,
        snapshot_type=SNAPSHOT_TYPE,
        session_id=snapshot.session_id,
        world_id=snapshot.world_id,
        sequence=snapshot.sequence,
        timestamp_ms=snapshot.timestamp_ms,
        candidate_policy=CandidatePolicy(
            entry_radius_blocks=snapshot.candidate_policy.entry_radius_blocks,
            exit_radius_blocks=snapshot.candidate_policy.exit_radius_blocks,
        ),
        active_event_ids=[],
        active_conversation=active_conversation,
        candidate_count=len(snapshot.npcs),
        npcs=[
            RoutingNpc(
                npc_id=observation.npc_id,
                world_distance_blocks=observation.world_distance_blocks,
                viewport_center_distance=observation.viewport_center_distance,
                inside_viewport=observation.inside_viewport,
                line_of_sight=observation.line_of_sight,
                event_relevance=NO_EVENT_RELEVANCE,
                event_roles=[],
                interaction_recency=NO_INTERACTION_RECENCY,
            )
            for observation in snapshot.npcs
        ],
        attention_edges=[
            AttentionEdge(
                source_npc_id=edge.source_npc_id,
                target_npc_id=edge.target_npc_id,
                kind=edge.kind,
                active=edge.active,
            )
            for edge in snapshot.attention_edges
        ],
    )
