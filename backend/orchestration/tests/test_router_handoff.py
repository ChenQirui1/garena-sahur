"""Owner: Jerome & Richard"""

from __future__ import annotations

from typing import AsyncIterator

import pytest
import pytest_asyncio
from pydantic import ValidationError

from backend.orchestration.router_handoff import (
    RouterHandoff,
    RoutingOutcome,
    RoutingStatus,
)
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
    StatefulRouter,
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


@pytest.mark.parametrize(
    "out_of_range",
    [
        {"event_relevance": 1.5},
        {"event_relevance": -0.1},
        {"interaction_recency": 1.5},
        {"viewport_center_distance": 1.5},
        {"world_distance_blocks": -1.0},
    ],
)
def test_the_router_never_receives_a_signal_outside_its_documented_range(
    out_of_range: dict[str, float],
) -> None:
    inside_range = {
        "npc_id": SHOPKEEPER,
        "world_distance_blocks": 3.4,
        "viewport_center_distance": 0.07,
        "inside_viewport": True,
        "line_of_sight": True,
        "event_relevance": 0.0,
        "event_roles": [],
        "interaction_recency": 0.0,
    }

    with pytest.raises(ValidationError):
        RoutingNpc(**(inside_range | out_of_range))


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


class Listener:
    """Stands in for the coordinator's routing listener, which promotion and expiry hang off."""

    def __init__(self) -> None:
        self.outcomes: list[RoutingOutcome] = []

    async def __call__(self, outcome: RoutingOutcome) -> None:
        self.outcomes.append(outcome)


async def test_a_trigger_routing_outcome_reaches_the_listener_like_any_other() -> None:
    handoff = RouterHandoff(StatefulRouter({SHOPKEEPER: AttentionTier.FOCUSED}))
    listener = Listener()
    handoff.listen(listener)

    outcome = await handoff.route_now(routing_snapshot(1842, target_npc_id=SHOPKEEPER))

    assert outcome.status is RoutingStatus.ROUTED
    assert listener.outcomes == [outcome]


async def test_a_snapshot_superseded_during_enrichment_never_reaches_the_router() -> None:
    """The trigger loses the race, so it reads the newer outcome instead of failing closed."""
    router = StatefulRouter({SHOPKEEPER: AttentionTier.FOCUSED})
    handoff = RouterHandoff(router)
    await handoff.start()
    try:
        await route(handoff, routing_snapshot(8, target_npc_id=SHOPKEEPER))
        outcome = await handoff.route_now(routing_snapshot(7, target_npc_id=SHOPKEEPER))
    finally:
        await handoff.stop()

    assert [snapshot.sequence for snapshot in router.routed] == [8]
    assert outcome.status is RoutingStatus.ROUTED
    assert outcome.sequence == 8


async def test_a_superseded_trigger_does_not_re_notify_the_listener() -> None:
    """Nothing was routed, so there is no new outcome for promotion or expiry to read."""
    handoff = RouterHandoff(StatefulRouter({SHOPKEEPER: AttentionTier.FOCUSED}))
    listener = Listener()
    handoff.listen(listener)
    await handoff.start()
    try:
        await route(handoff, routing_snapshot(8, target_npc_id=SHOPKEEPER))
        await handoff.route_now(routing_snapshot(7, target_npc_id=SHOPKEEPER))
    finally:
        await handoff.stop()

    assert [outcome.sequence for outcome in listener.outcomes] == [8]


async def test_a_newer_pending_snapshot_survives_a_trigger_routing() -> None:
    """Routing a trigger discards only what it overtook, so nothing waiting goes unobserved."""
    router = StatefulRouter({SHOPKEEPER: AttentionTier.FOCUSED})
    handoff = RouterHandoff(router)
    listener = Listener()
    handoff.listen(listener)
    await handoff.start()
    try:
        handoff.submit(routing_snapshot(9, target_npc_id=SHOPKEEPER))
        await handoff.route_now(routing_snapshot(7, target_npc_id=SHOPKEEPER))
        await handoff.wait_until_idle()
    finally:
        await handoff.stop()

    assert [snapshot.sequence for snapshot in router.routed] == [7, 9]
    assert [outcome.sequence for outcome in listener.outcomes] == [7, 9]


async def test_a_pending_snapshot_the_trigger_overtook_is_never_routed() -> None:
    """Routing it would hand the Router an older sequence and fail the session closed."""
    router = StatefulRouter({SHOPKEEPER: AttentionTier.FOCUSED})
    handoff = RouterHandoff(router)
    await handoff.start()
    try:
        handoff.submit(routing_snapshot(5, target_npc_id=SHOPKEEPER))
        await handoff.route_now(routing_snapshot(7, target_npc_id=SHOPKEEPER))
        await handoff.wait_until_idle()
    finally:
        await handoff.stop()

    assert [snapshot.sequence for snapshot in router.routed] == [7]
    assert handoff.latest_outcome(SESSION_ID, WORLD_ID).status is RoutingStatus.ROUTED


async def test_a_router_failure_on_the_trigger_path_still_fails_closed() -> None:
    """Supersession is not the only reason a trigger produces nothing; a failure still shows."""
    handoff = RouterHandoff(RaisingRouter(RuntimeError("router exploded")))

    outcome = await handoff.route_now(routing_snapshot(1842, target_npc_id=SHOPKEEPER))

    assert outcome.status is RoutingStatus.ROUTER_FAILED
    assert outcome.assignments == ()
    assert "router exploded" in outcome.failure_reason


async def test_a_reset_session_lets_the_next_snapshot_route_from_scratch() -> None:
    """Sequence memory is per session, so a restarted session is not permanently superseded."""
    router = StatefulRouter({SHOPKEEPER: AttentionTier.FOCUSED})
    handoff = RouterHandoff(router)

    await handoff.route_now(routing_snapshot(8, target_npc_id=SHOPKEEPER))
    handoff.reset_session(SESSION_ID)
    outcome = await handoff.route_now(routing_snapshot(1, target_npc_id=SHOPKEEPER))

    assert [snapshot.sequence for snapshot in router.routed] == [8, 1]
    assert outcome.status is RoutingStatus.ROUTED
    assert outcome.sequence == 1


async def test_a_stopped_handoff_reports_that_it_is_not_running() -> None:
    handoff = RouterHandoff(RecordingRouter())
    assert handoff.is_running is False

    await handoff.start()
    assert handoff.is_running is True

    await handoff.stop()
    assert handoff.is_running is False
