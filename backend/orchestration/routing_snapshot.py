"""Build the enriched routing snapshot handed to the Router.

Owner: Jerome & Richard

The shape is fixed by `docs/message_schemas.md` §4 and must not vary between calls, so every
documented field is present on every build even when the value is empty. What the backend adds
to Minecraft's raw observation is exactly two per-NPC signals and the list of events that
produced them; attention edges are carried through untouched because their weight is Router-owned.
"""

from __future__ import annotations

from backend.ingestion.event_store import EventStore, StoredEvent
from backend.ingestion.message_validation import NpcObservation, WorldSnapshot
from backend.orchestration.event_relevance import EventRadii, enrichment_for
from backend.orchestration.interaction_recency import InteractionRecency
from backend.orchestration.router_port import (
    SNAPSHOT_SCHEMA_VERSION,
    SNAPSHOT_TYPE,
    ActiveConversation,
    AttentionEdge,
    CandidatePolicy,
    RoutingNpc,
    RoutingSnapshot,
)


class RoutingSnapshots:
    """Assembles the Router's input from current world state and owned durable state."""

    def __init__(
        self, events: EventStore, recency: InteractionRecency, radii: EventRadii
    ) -> None:
        self._events = events
        self._recency = recency
        self._radii = radii

    async def build(
        self, snapshot: WorldSnapshot, active_conversation: ActiveConversation | None
    ) -> RoutingSnapshot:
        """Enrich one validated world snapshot, preserving its source sequence and timestamp."""
        active_events = await self._events.active(snapshot.session_id)
        target = active_conversation.target_npc_id if active_conversation else None
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
            active_event_ids=[stored.event.event_id for stored in active_events],
            active_conversation=active_conversation,
            candidate_count=len(snapshot.npcs),
            npcs=[
                self._routing_npc(observation, active_events, snapshot.session_id, target)
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

    def _routing_npc(
        self,
        observation: NpcObservation,
        active_events: tuple[StoredEvent, ...],
        session_id: str,
        active_target: str | None,
    ) -> RoutingNpc:
        enrichment = enrichment_for(
            observation.npc_id, observation.position, active_events, self._radii
        )
        return RoutingNpc(
            npc_id=observation.npc_id,
            world_distance_blocks=observation.world_distance_blocks,
            viewport_center_distance=observation.viewport_center_distance,
            inside_viewport=observation.inside_viewport,
            line_of_sight=observation.line_of_sight,
            event_relevance=enrichment.event_relevance,
            event_roles=enrichment.event_roles,
            interaction_recency=self._recency.value_for(
                session_id, observation.npc_id, active_target
            ),
        )
