"""Router tests: seeded stress runs at 10, 25, 50 and 100 NPCs.

Owner: Elson & Daniel
"""

from __future__ import annotations

import random
from collections import Counter

import pytest

from backend.router.config import RouterConfig
from backend.router.models import (
    SNAPSHOT_SCHEMA_VERSION,
    SNAPSHOT_TYPE,
    AttentionEdge,
    AttentionTier,
    CandidatePolicy,
    RoutingNpc,
    RoutingSnapshot,
)
from backend.router.router import Router


def make_snapshot(count: int, seed: int) -> RoutingSnapshot:
    rng = random.Random(seed)
    npcs = [
        RoutingNpc(
            npc_id=f"npc-{index:03d}",
            world_distance_blocks=rng.uniform(0.0, 28.0),
            viewport_center_distance=rng.random(),
            inside_viewport=rng.choice([True, False]),
            line_of_sight=rng.choice([True, False]),
            event_relevance=rng.random(),
            event_roles=[rng.choice(["actor", "target", "responder", "witness"])],
            interaction_recency=rng.random(),
        )
        for index in range(count)
    ]
    edges: list[AttentionEdge] = []
    for _ in range(count * 2):
        source = rng.randrange(count)
        target = rng.randrange(count)
        edges.append(
            AttentionEdge(
                source_npc_id=f"npc-{source:03d}",
                target_npc_id=f"npc-{target:03d}",
                kind="gaze" if rng.random() < 0.8 else "unknown",
                active=rng.random() < 0.9,
            )
        )
    return RoutingSnapshot(
        schema_version=SNAPSHOT_SCHEMA_VERSION,
        snapshot_type=SNAPSHOT_TYPE,
        session_id=f"stress-{count}",
        world_id="market",
        sequence=1,
        timestamp_ms=10_000,
        candidate_policy=CandidatePolicy(entry_radius_blocks=24.0, exit_radius_blocks=28.0),
        active_event_ids=["event-1"],
        active_conversation=None,
        candidate_count=count,
        npcs=npcs,
        attention_edges=edges,
    )


@pytest.mark.parametrize("count", [10, 25, 50, 100])
def test_seeded_graph_routing_is_deterministic_and_capacity_safe(count: int) -> None:
    config = RouterConfig(focused_capacity=2, reactive_capacity=6)
    snapshot = make_snapshot(count, seed=2026 + count)
    first = Router(config).route(snapshot)
    second = Router(config).route(snapshot)

    assert first.assignments == second.assignments
    assert [assignment.npc_id for assignment in first.assignments] == [
        npc.npc_id for npc in snapshot.npcs
    ]

    counts = Counter(assignment.tier for assignment in first.assignments)
    assert counts[AttentionTier.FOCUSED] <= config.focused_capacity
    assert counts[AttentionTier.REACTIVE] <= config.reactive_capacity
    assert sum(counts.values()) == count
    assert all(assignment.direct_score is not None for assignment in first.assignments)
    assert all(assignment.propagated_score is not None for assignment in first.assignments)
    assert all(assignment.final_score is not None for assignment in first.assignments)
    assert first.counts is not None
    assert first.counts.focused == counts[AttentionTier.FOCUSED]
    assert first.counts.reactive == counts[AttentionTier.REACTIVE]
    assert first.counts.ambient == counts[AttentionTier.AMBIENT]


def state_snapshot(
    session_id: str,
    sequence: int,
    timestamp_ms: int,
    first_score: float,
    second_score: float,
) -> RoutingSnapshot:
    npcs = [
        RoutingNpc(
            npc_id="first",
            world_distance_blocks=5.0,
            viewport_center_distance=1.0,
            inside_viewport=False,
            line_of_sight=False,
            event_relevance=first_score,
            event_roles=[],
            interaction_recency=0.0,
        ),
        RoutingNpc(
            npc_id="second",
            world_distance_blocks=5.0,
            viewport_center_distance=1.0,
            inside_viewport=False,
            line_of_sight=False,
            event_relevance=second_score,
            event_roles=[],
            interaction_recency=0.0,
        ),
    ]
    return RoutingSnapshot(
        schema_version=SNAPSHOT_SCHEMA_VERSION,
        snapshot_type=SNAPSHOT_TYPE,
        session_id=session_id,
        world_id="market",
        sequence=sequence,
        timestamp_ms=timestamp_ms,
        candidate_policy=CandidatePolicy(
            entry_radius_blocks=24.0,
            exit_radius_blocks=28.0,
        ),
        active_event_ids=[],
        active_conversation=None,
        candidate_count=len(npcs),
        npcs=npcs,
        attention_edges=[],
    )


def test_hysteresis_state_is_isolated_by_session_and_removed_on_reset() -> None:
    config = RouterConfig(
        focused_capacity=1,
        reactive_capacity=0,
        viewport_weight=0.0,
        proximity_weight=0.0,
        event_relevance_weight=1.0,
        interaction_recency_weight=0.0,
        focused_hold_ms=10_000,
        focused_sticky_bonus=1.0,
    )
    router = Router(config)
    router.route(state_snapshot("session-a", 1, 10_000, 0.9, 0.1))

    session_b = router.route(
        state_snapshot("session-b", 1, 10_100, 0.1, 0.9)
    )
    assignments_b = {one.npc_id: one for one in session_b.assignments}
    assert assignments_b["second"].tier is AttentionTier.FOCUSED
    assert assignments_b["second"].previous_tier is None

    router.reset_session("session-a")
    reset_a = router.route(
        state_snapshot("session-a", 1, 10_200, 0.1, 0.9)
    )
    assignments_a = {one.npc_id: one for one in reset_a.assignments}
    assert assignments_a["second"].tier is AttentionTier.FOCUSED
    assert assignments_a["second"].previous_tier is None
