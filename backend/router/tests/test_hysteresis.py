"""Router tests: fast promotion, delayed demotion, no tier flicker.

Owner: Elson & Daniel
"""

from __future__ import annotations

from collections import Counter

from backend.router.config import RouterConfig
from backend.router.models import (
    SNAPSHOT_SCHEMA_VERSION,
    SNAPSHOT_TYPE,
    ActiveConversation,
    AttentionTier,
    CandidatePolicy,
    RoutingAssignment,
    RoutingNpc,
    RoutingResult,
    RoutingSnapshot,
)
from backend.router.router import Router

START_MS = 10_000


def config(**overrides: object) -> RouterConfig:
    fields: dict[str, object] = {
        "focused_capacity": 1,
        "reactive_capacity": 1,
        "viewport_weight": 0.0,
        "proximity_weight": 0.0,
        "event_relevance_weight": 1.0,
        "interaction_recency_weight": 0.0,
        "focused_hold_ms": 1_000,
        "reactive_hold_ms": 1_000,
        "focused_sticky_bonus": 0.20,
        "reactive_sticky_bonus": 0.10,
    }
    return RouterConfig(**(fields | overrides))  # type: ignore[arg-type]


def npc(npc_id: str, score: float) -> RoutingNpc:
    return RoutingNpc(
        npc_id=npc_id,
        world_distance_blocks=5.0,
        viewport_center_distance=1.0,
        inside_viewport=False,
        line_of_sight=False,
        event_relevance=score,
        event_roles=[],
        interaction_recency=0.0,
    )


def snapshot(
    sequence: int,
    timestamp_ms: int,
    *npcs: RoutingNpc,
    conversation_target: str | None = None,
) -> RoutingSnapshot:
    conversation = None
    if conversation_target is not None:
        conversation = ActiveConversation(
            conversation_id="conversation-01",
            target_npc_id=conversation_target,
            state="engaged",
            started_at_ms=timestamp_ms - 1,
            latest_turn_id="turn-01",
        )

    return RoutingSnapshot(
        schema_version=SNAPSHOT_SCHEMA_VERSION,
        snapshot_type=SNAPSHOT_TYPE,
        session_id="hysteresis-session",
        world_id="market",
        sequence=sequence,
        timestamp_ms=timestamp_ms,
        candidate_policy=CandidatePolicy(
            entry_radius_blocks=24.0, exit_radius_blocks=28.0
        ),
        active_event_ids=[],
        active_conversation=conversation,
        candidate_count=len(npcs),
        npcs=list(npcs),
        attention_edges=[],
    )


def by_id(result: RoutingResult) -> dict[str, RoutingAssignment]:
    return {assignment.npc_id: assignment for assignment in result.assignments}


def test_newly_important_npc_is_promoted_immediately() -> None:
    router = Router(config(focused_capacity=2, reactive_capacity=0))
    router.route(
        snapshot(
            1,
            START_MS,
            npc("incumbent", 0.9),
            npc("departing", 0.8),
            npc("new", 0.1),
        )
    )

    result = router.route(
        snapshot(2, START_MS + 100, npc("incumbent", 0.9), npc("new", 0.7))
    )
    assignments = by_id(result)

    assert assignments["new"].tier is AttentionTier.FOCUSED
    assert assignments["new"].previous_tier is AttentionTier.AMBIENT
    assert assignments["new"].changed is True


def test_focused_npc_survives_small_drop_inside_hold_window() -> None:
    router = Router(config())
    router.route(snapshot(1, START_MS, npc("incumbent", 0.8), npc("challenger", 0.7)))

    result = router.route(
        snapshot(2, START_MS + 500, npc("incumbent", 0.65), npc("challenger", 0.7))
    )
    assignments = by_id(result)

    assert assignments["incumbent"].tier is AttentionTier.FOCUSED
    assert assignments["incumbent"].previous_tier is AttentionTier.FOCUSED
    assert assignments["incumbent"].changed is False


def test_focused_npc_can_be_demoted_after_hold_expires() -> None:
    router = Router(config())
    router.route(snapshot(1, START_MS, npc("incumbent", 0.8), npc("challenger", 0.7)))

    result = router.route(
        snapshot(
            2,
            START_MS + 1_001,
            npc("incumbent", 0.65),
            npc("challenger", 0.7),
        )
    )
    assignments = by_id(result)

    assert assignments["incumbent"].tier is AttentionTier.REACTIVE
    assert assignments["incumbent"].previous_tier is AttentionTier.FOCUSED
    assert assignments["incumbent"].changed is True
    assert assignments["challenger"].tier is AttentionTier.FOCUSED


def test_reactive_npc_receives_its_separate_hold_behaviour() -> None:
    router = Router(config(focused_hold_ms=100, reactive_hold_ms=1_000))
    router.route(
        snapshot(
            1,
            START_MS,
            npc("leader", 0.9),
            npc("reactive", 0.7),
            npc("challenger", 0.65),
        )
    )

    result = router.route(
        snapshot(
            2,
            START_MS + 500,
            npc("leader", 0.9),
            npc("reactive", 0.6),
            npc("challenger", 0.65),
        )
    )
    assignments = by_id(result)

    assert assignments["reactive"].tier is AttentionTier.REACTIVE
    assert assignments["reactive"].previous_tier is AttentionTier.REACTIVE
    assert assignments["reactive"].changed is False
    assert assignments["challenger"].tier is AttentionTier.AMBIENT


def test_much_stronger_challenger_preempts_sticky_incumbent() -> None:
    router = Router(config(reactive_capacity=0))
    router.route(snapshot(1, START_MS, npc("incumbent", 0.9), npc("challenger", 0.1)))

    result = router.route(
        snapshot(2, START_MS + 100, npc("incumbent", 0.4), npc("challenger", 1.0))
    )
    assignments = by_id(result)

    assert assignments["challenger"].tier is AttentionTier.FOCUSED
    assert assignments["challenger"].previous_tier is AttentionTier.AMBIENT
    assert assignments["challenger"].changed is True
    assert assignments["incumbent"].tier is AttentionTier.AMBIENT


def test_active_conversation_preempts_sticky_focused_npc() -> None:
    router = Router(config(reactive_capacity=0))
    router.route(snapshot(1, START_MS, npc("incumbent", 0.9), npc("target", 0.0)))

    result = router.route(
        snapshot(
            2,
            START_MS + 100,
            npc("incumbent", 0.9),
            npc("target", 0.0),
            conversation_target="target",
        )
    )
    assignments = by_id(result)

    assert assignments["target"].tier is AttentionTier.FOCUSED
    assert assignments["target"].previous_tier is AttentionTier.AMBIENT
    assert assignments["target"].changed is True
    assert "active conversation" in assignments["target"].reasons
    assert assignments["incumbent"].tier is AttentionTier.AMBIENT


def test_hysteresis_never_exceeds_hard_capacities() -> None:
    router = Router(config(focused_capacity=2, reactive_capacity=2))
    candidates = tuple(npc(f"npc-{index}", 0.95 - index * 0.05) for index in range(7))
    router.route(snapshot(1, START_MS, *candidates))

    reversed_scores = tuple(
        npc(candidate.npc_id, 0.65 + index * 0.05)
        for index, candidate in enumerate(candidates)
    )
    result = router.route(snapshot(2, START_MS + 100, *reversed_scores))
    counts = Counter(assignment.tier for assignment in result.assignments)

    assert counts[AttentionTier.FOCUSED] <= 2
    assert counts[AttentionTier.REACTIVE] <= 2
    assert sum(counts.values()) == len(candidates)


def test_same_tier_snapshots_do_not_restart_original_hold_timer() -> None:
    router = Router(config())
    router.route(snapshot(1, START_MS, npc("incumbent", 0.8), npc("challenger", 0.7)))
    router.route(
        snapshot(2, START_MS + 500, npc("incumbent", 0.8), npc("challenger", 0.7))
    )

    result = router.route(
        snapshot(
            3,
            START_MS + 1_001,
            npc("incumbent", 0.65),
            npc("challenger", 0.7),
        )
    )
    assignments = by_id(result)

    assert assignments["challenger"].tier is AttentionTier.FOCUSED
    assert assignments["challenger"].previous_tier is AttentionTier.REACTIVE
    assert assignments["challenger"].changed is True
    assert assignments["incumbent"].tier is AttentionTier.REACTIVE


def test_reset_session_removes_old_hysteresis_advantage() -> None:
    router = Router(config())
    router.route(snapshot(1, START_MS, npc("incumbent", 0.8), npc("challenger", 0.7)))

    router.reset_session("hysteresis-session")
    result = router.route(
        snapshot(1, START_MS + 100, npc("incumbent", 0.65), npc("challenger", 0.7))
    )
    assignments = by_id(result)

    assert assignments["challenger"].tier is AttentionTier.FOCUSED
    assert assignments["challenger"].previous_tier is None
    assert assignments["challenger"].changed is False
    assert assignments["incumbent"].tier is AttentionTier.REACTIVE
