"""Router tests: hard Focused and Reactive capacity limits.

Owner: Elson & Daniel
"""

from __future__ import annotations

from collections import Counter

from backend.router.config import RouterConfig
from backend.router.models import (
    SNAPSHOT_SCHEMA_VERSION,
    SNAPSHOT_TYPE,
    AttentionTier,
    CandidatePolicy,
    RoutingNpc,
    RoutingSnapshot,
)
from backend.router.router import Router


def npc(npc_id: str, distance: float = 5.0) -> RoutingNpc:
    return RoutingNpc(
        npc_id=npc_id,
        world_distance_blocks=distance,
        viewport_center_distance=0.2,
        inside_viewport=True,
        line_of_sight=True,
        event_relevance=0.0,
        event_roles=[],
        interaction_recency=0.0,
    )


def snapshot(*npcs: RoutingNpc) -> RoutingSnapshot:
    return RoutingSnapshot(
        schema_version=SNAPSHOT_SCHEMA_VERSION,
        snapshot_type=SNAPSHOT_TYPE,
        session_id="demo-01",
        world_id="minecraft-overworld-market",
        sequence=1,
        timestamp_ms=1_786_208_500_123,
        candidate_policy=CandidatePolicy(
            entry_radius_blocks=24.0, exit_radius_blocks=28.0
        ),
        active_event_ids=[],
        active_conversation=None,
        candidate_count=len(npcs),
        npcs=list(npcs),
        attention_edges=[],
    )


def test_router_enforces_both_hard_capacity_limits_and_assigns_every_candidate() -> None:
    candidates = tuple(npc(f"npc-{index:02d}", index + 1.0) for index in range(12))
    result = Router().route(snapshot(*candidates))
    counts = Counter(assignment.tier for assignment in result.assignments)

    assert counts == {
        AttentionTier.FOCUSED: 2,
        AttentionTier.REACTIVE: 6,
        AttentionTier.AMBIENT: 4,
    }
    assert [assignment.npc_id for assignment in result.assignments] == [
        candidate.npc_id for candidate in candidates
    ]


def test_equal_scores_use_the_stable_npc_id_tie_breaker() -> None:
    router = Router(RouterConfig(focused_capacity=1, reactive_capacity=0))
    result = router.route(snapshot(npc("npc-z"), npc("npc-a")))
    tiers = {assignment.npc_id: assignment.tier for assignment in result.assignments}

    assert tiers == {
        "npc-z": AttentionTier.AMBIENT,
        "npc-a": AttentionTier.FOCUSED,
    }
