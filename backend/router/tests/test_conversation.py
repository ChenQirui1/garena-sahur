"""Router tests: active conversation priority.

Owner: Elson & Daniel
"""

from __future__ import annotations

from backend.router.config import RouterConfig
from backend.router.models import (
    SNAPSHOT_SCHEMA_VERSION,
    SNAPSHOT_TYPE,
    ActiveConversation,
    AttentionTier,
    CandidatePolicy,
    RoutingNpc,
    RoutingSnapshot,
)
from backend.router.router import Router


def npc(npc_id: str, important: bool) -> RoutingNpc:
    return RoutingNpc(
        npc_id=npc_id,
        world_distance_blocks=1.0 if important else 28.0,
        viewport_center_distance=0.0 if important else 1.0,
        inside_viewport=important,
        line_of_sight=important,
        event_relevance=1.0 if important else 0.0,
        event_roles=["actor"] if important else [],
        interaction_recency=1.0 if important else 0.0,
    )


def test_active_conversation_target_is_focused_even_without_other_attention_signals() -> None:
    target = npc("quiet-shopkeeper", important=False)
    visible_actor = npc("visible-actor", important=True)
    snapshot = RoutingSnapshot(
        schema_version=SNAPSHOT_SCHEMA_VERSION,
        snapshot_type=SNAPSHOT_TYPE,
        session_id="demo-01",
        world_id="minecraft-overworld-market",
        sequence=7,
        timestamp_ms=1_786_208_500_200,
        candidate_policy=CandidatePolicy(
            entry_radius_blocks=24.0, exit_radius_blocks=28.0
        ),
        active_event_ids=["market-theft-001"],
        active_conversation=ActiveConversation(
            conversation_id="conversation-07",
            target_npc_id=target.npc_id,
            state="engaged",
            started_at_ms=1_786_208_485_000,
            latest_turn_id="turn-004",
        ),
        candidate_count=2,
        npcs=[visible_actor, target],
        attention_edges=[],
    )

    result = Router(RouterConfig(focused_capacity=1, reactive_capacity=1)).route(snapshot)
    assignments = {assignment.npc_id: assignment for assignment in result.assignments}

    assert assignments[target.npc_id].tier is AttentionTier.FOCUSED
    assert assignments[visible_actor.npc_id].tier is AttentionTier.REACTIVE
    assert "active conversation" in assignments[target.npc_id].reasons
