"""Owner: Jerome & Richard"""

from __future__ import annotations

from typing import AsyncIterator

import pytest
import pytest_asyncio

from backend.orchestration.router_handoff import RouterHandoff, RoutingStatus
from backend.orchestration.router_port import (
    SNAPSHOT_SCHEMA_VERSION,
    SNAPSHOT_TYPE,
    ActiveConversation,
    AttentionTier,
    CandidatePolicy,
    RoutingAssignment,
    RoutingNpc,
    RoutingSnapshot,
)
from backend.orchestration.tests.fake_routers import (
    RaisingRouter,
    RecordingRouter,
    ScriptedRouter,
    result_for,
)

SESSION_ID = "demo-01"
WORLD_ID = "minecraft-overworld-market"
SHOPKEEPER = "shopkeeper-uuid"
TIMESTAMP_MS = 1_786_208_500_123


def routing_snapshot(sequence: int, target_npc_id: str | None = None) -> RoutingSnapshot:
    return RoutingSnapshot(
        schema_version=SNAPSHOT_SCHEMA_VERSION,
        snapshot_type=SNAPSHOT_TYPE,
        session_id=SESSION_ID,
        world_id=WORLD_ID,
        sequence=sequence,
        timestamp_ms=TIMESTAMP_MS,
        candidate_policy=CandidatePolicy(entry_radius_blocks=24.0, exit_radius_blocks=28.0),
        active_event_ids=[],
        active_conversation=(
            None
            if target_npc_id is None
            else ActiveConversation(
                conversation_id="conversation-07",
                target_npc_id=target_npc_id,
                state="engaged",
                started_at_ms=TIMESTAMP_MS,
                latest_turn_id=None,
            )
        ),
        candidate_count=1,
        npcs=[
            RoutingNpc(
                npc_id=SHOPKEEPER,
                world_distance_blocks=3.4,
                viewport_center_distance=0.07,
                inside_viewport=True,
                line_of_sight=True,
                event_relevance=0.0,
                event_roles=[],
                interaction_recency=0.0,
            )
        ],
        attention_edges=[],
    )


def assignment(npc_id: str, tier: object = AttentionTier.FOCUSED) -> RoutingAssignment:
    """``tier`` stays untyped so a Router result can be scripted with an unknown tier."""
    return RoutingAssignment(
        npc_id=npc_id, tier=tier, previous_tier=None, changed=True  # type: ignore[arg-type]
    )


@pytest_asyncio.fixture
async def running_handoff() -> AsyncIterator[tuple[RouterHandoff, RecordingRouter]]:
    router = RecordingRouter()
    handoff = RouterHandoff(router)
    await handoff.start()
    try:
        yield handoff, router
    finally:
        await handoff.stop()


async def route(handoff: RouterHandoff, *snapshots: RoutingSnapshot) -> None:
    for snapshot in snapshots:
        handoff.submit(snapshot)
    await handoff.wait_until_idle()


async def routed_by(router: object, snapshot: RoutingSnapshot) -> RoutingStatus:
    handoff = RouterHandoff(router)  # type: ignore[arg-type]
    await handoff.start()
    try:
        await route(handoff, snapshot)
    finally:
        await handoff.stop()
    outcome = handoff.latest_outcome(SESSION_ID, WORLD_ID)
    assert outcome is not None
    assert outcome.assignments == ()
    return outcome.status


async def test_a_routed_snapshot_produces_assignments_for_its_candidates(
    running_handoff: tuple[RouterHandoff, RecordingRouter],
) -> None:
    handoff, router = running_handoff

    await route(handoff, routing_snapshot(1842, target_npc_id=SHOPKEEPER))

    outcome = handoff.latest_outcome(SESSION_ID, WORLD_ID)
    assert outcome is not None
    assert outcome.status is RoutingStatus.ROUTED
    assert outcome.sequence == 1842
    assert outcome.assignments == (
        RoutingAssignment(
            npc_id=SHOPKEEPER,
            tier=AttentionTier.FOCUSED,
            previous_tier=None,
            changed=True,
        ),
    )
    assert router.routed[0].active_conversation.target_npc_id == SHOPKEEPER


async def test_one_persistent_router_serves_every_snapshot(
    running_handoff: tuple[RouterHandoff, RecordingRouter],
) -> None:
    handoff, router = running_handoff

    await route(handoff, routing_snapshot(1))
    await route(handoff, routing_snapshot(2))

    assert [snapshot.sequence for snapshot in router.routed] == [1, 2]


async def test_pending_snapshot_work_is_coalesced_to_the_newest_sequence(
    running_handoff: tuple[RouterHandoff, RecordingRouter],
) -> None:
    handoff, router = running_handoff

    await route(handoff, routing_snapshot(4), routing_snapshot(5), routing_snapshot(6))

    assert [snapshot.sequence for snapshot in router.routed] == [6]
    assert handoff.latest_outcome(SESSION_ID, WORLD_ID).sequence == 6


async def test_a_router_exception_fails_closed_and_is_observable() -> None:
    handoff = RouterHandoff(RaisingRouter(RuntimeError("router exploded")))
    await handoff.start()
    try:
        await route(handoff, routing_snapshot(1))
    finally:
        await handoff.stop()

    outcome = handoff.latest_outcome(SESSION_ID, WORLD_ID)
    assert outcome is not None
    assert outcome.status is RoutingStatus.ROUTER_FAILED
    assert outcome.assignments == ()
    assert "router exploded" in outcome.failure_reason


async def test_a_result_for_another_sequence_is_rejected_as_stale() -> None:
    snapshot = routing_snapshot(1842)
    superseded = result_for(snapshot, (), sequence=1841)

    assert await routed_by(ScriptedRouter(superseded), snapshot) is RoutingStatus.STALE_RESULT


@pytest.mark.parametrize(
    "make_result",
    [
        lambda snapshot: "focused",
        lambda snapshot: [{"npc_id": SHOPKEEPER, "tier": "focused"}],
        lambda snapshot: result_for(snapshot, "focused"),
        lambda snapshot: result_for(snapshot, (assignment("stranger"),)),
        lambda snapshot: result_for(
            snapshot,
            (assignment(SHOPKEEPER), assignment(SHOPKEEPER, tier=AttentionTier.AMBIENT)),
        ),
        lambda snapshot: result_for(snapshot, (assignment(SHOPKEEPER, tier="platinum"),)),
        lambda snapshot: result_for(snapshot, (), schema_version="2.0"),
        lambda snapshot: result_for(snapshot, (), result_type="world_snapshot"),
        lambda snapshot: result_for(snapshot, (), session_id="demo-02"),
        lambda snapshot: result_for(snapshot, (), world_id="nether"),
    ],
    ids=[
        "not-a-result",
        "list-of-dicts",
        "assignments-not-a-sequence",
        "unknown-npc",
        "npc-assigned-twice",
        "unknown-tier",
        "unsupported-schema-version",
        "wrong-result-type",
        "other-session",
        "other-world",
    ],
)
async def test_an_invalid_router_result_fails_closed_without_inventing_tiers(
    make_result: object,
) -> None:
    snapshot = routing_snapshot(1842)
    router = ScriptedRouter(make_result(snapshot))  # type: ignore[operator]

    assert await routed_by(router, snapshot) is RoutingStatus.INVALID_RESULT


async def test_a_stopped_handoff_reports_that_it_is_not_running() -> None:
    handoff = RouterHandoff(RecordingRouter())
    assert handoff.is_running is False

    await handoff.start()
    assert handoff.is_running is True

    await handoff.stop()
    assert handoff.is_running is False
