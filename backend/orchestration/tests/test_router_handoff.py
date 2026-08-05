"""Owner: Jerome & Richard"""

from __future__ import annotations

from typing import AsyncIterator

import pytest
import pytest_asyncio

from backend.orchestration.router_handoff import RouterHandoff, RoutingStatus
from backend.orchestration.router_port import (
    AttentionTier,
    RoutingAssignment,
    RoutingCandidate,
    RoutingSnapshot,
)
from backend.orchestration.tests.fake_routers import RaisingRouter, RecordingRouter, ScriptedRouter

SESSION_ID = "demo-01"
WORLD_ID = "overworld"
SHOPKEEPER = "shopkeeper-uuid"


def routing_snapshot(source_sequence: int, active_npc_id: str | None = None) -> RoutingSnapshot:
    return RoutingSnapshot(
        session_id=SESSION_ID,
        world_id=WORLD_ID,
        source_sequence=source_sequence,
        observed_at_ms=1_786_208_500_123,
        candidates=(
            RoutingCandidate(
                npc_id=SHOPKEEPER,
                world_distance=3.4,
                viewport_center_distance=0.07,
                visible=True,
                line_of_sight=True,
                in_active_conversation=active_npc_id == SHOPKEEPER,
            ),
        ),
        active_conversation_npc_id=active_npc_id,
        attention_edges=(),
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


async def test_a_routed_snapshot_produces_assignments_for_its_candidates(
    running_handoff: tuple[RouterHandoff, RecordingRouter],
) -> None:
    handoff, router = running_handoff

    await route(handoff, routing_snapshot(1, active_npc_id=SHOPKEEPER))

    outcome = handoff.latest_outcome(SESSION_ID, WORLD_ID)
    assert outcome is not None
    assert outcome.status is RoutingStatus.ROUTED
    assert outcome.source_sequence == 1
    assert outcome.assignments == (
        RoutingAssignment(npc_id=SHOPKEEPER, tier=AttentionTier.FOCUSED),
    )
    assert router.routed[0].active_conversation_npc_id == SHOPKEEPER


async def test_one_persistent_router_serves_every_snapshot(
    running_handoff: tuple[RouterHandoff, RecordingRouter],
) -> None:
    handoff, router = running_handoff

    await route(handoff, routing_snapshot(1))
    await route(handoff, routing_snapshot(2))

    assert [snapshot.source_sequence for snapshot in router.routed] == [1, 2]


async def test_pending_snapshot_work_is_coalesced_to_the_newest_sequence(
    running_handoff: tuple[RouterHandoff, RecordingRouter],
) -> None:
    handoff, router = running_handoff

    await route(handoff, routing_snapshot(4), routing_snapshot(5), routing_snapshot(6))

    assert [snapshot.source_sequence for snapshot in router.routed] == [6]
    assert handoff.latest_outcome(SESSION_ID, WORLD_ID).source_sequence == 6


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


@pytest.mark.parametrize(
    "result",
    [
        "focused",
        [RoutingAssignment(npc_id="stranger", tier=AttentionTier.FOCUSED)],
        [
            RoutingAssignment(npc_id=SHOPKEEPER, tier=AttentionTier.FOCUSED),
            RoutingAssignment(npc_id=SHOPKEEPER, tier=AttentionTier.AMBIENT),
        ],
        [{"npc_id": SHOPKEEPER, "tier": "focused"}],
        [RoutingAssignment(npc_id=SHOPKEEPER, tier="platinum")],
    ],
)
async def test_an_invalid_router_result_fails_closed_without_inventing_tiers(
    result: object,
) -> None:
    handoff = RouterHandoff(ScriptedRouter(result))
    await handoff.start()
    try:
        await route(handoff, routing_snapshot(1))
    finally:
        await handoff.stop()

    outcome = handoff.latest_outcome(SESSION_ID, WORLD_ID)
    assert outcome is not None
    assert outcome.status is RoutingStatus.INVALID_RESULT
    assert outcome.assignments == ()


async def test_a_stopped_handoff_reports_that_it_is_not_running() -> None:
    handoff = RouterHandoff(RecordingRouter())
    assert handoff.is_running is False

    await handoff.start()
    assert handoff.is_running is True

    await handoff.stop()
    assert handoff.is_running is False
