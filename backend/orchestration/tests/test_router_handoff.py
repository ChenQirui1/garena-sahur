"""Owner: Jerome & Richard"""

from __future__ import annotations

import json
import re
from dataclasses import fields
from pathlib import Path
from typing import Any, AsyncIterator

import pytest
import pytest_asyncio
from pydantic import ValidationError

from backend.orchestration.observations import ROUTING_FAILED_CLOSED, Observations
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
    RoutingDiagnostics,
    RoutingNpc,
    RoutingResult,
    RoutingSnapshot,
    TierCounts,
)
from backend.orchestration.tests.fake_routers import (
    OmittingRouter,
    RaisingRouter,
    RecordingRouter,
    ScriptedRouter,
    StatefulRouter,
    TierScriptRouter,
    result_for,
)

SESSION_ID = "demo-01"
WORLD_ID = "minecraft-overworld-market"
SHOPKEEPER = "shopkeeper-uuid"
THIEF = "thief-uuid"
TIMESTAMP_MS = 1_786_208_500_123
MESSAGE_SCHEMAS = Path(__file__).resolve().parents[3] / "docs" / "message_schemas.md"


def routing_snapshot(
    sequence: int,
    target_npc_id: str | None = None,
    npc_ids: tuple[str, ...] = (SHOPKEEPER,),
) -> RoutingSnapshot:
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
        candidate_count=len(npc_ids),
        npcs=[
            RoutingNpc(
                npc_id=npc_id,
                world_distance_blocks=3.4,
                viewport_center_distance=0.07,
                inside_viewport=True,
                line_of_sight=True,
                event_relevance=0.0,
                event_roles=[],
                interaction_recency=0.0,
            )
            for npc_id in npc_ids
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


def handoff_for(router: object, observations: Observations | None = None) -> RouterHandoff:
    return RouterHandoff(router, observations or Observations())  # type: ignore[arg-type]


@pytest_asyncio.fixture
async def running_handoff() -> AsyncIterator[tuple[RouterHandoff, RecordingRouter]]:
    router = RecordingRouter()
    handoff = handoff_for(router)
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
    handoff = handoff_for(router)
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
    handoff = handoff_for(RaisingRouter(RuntimeError("router exploded")))
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


async def test_a_result_that_omits_a_candidate_fails_closed() -> None:
    """`docs/message_schemas.md` §5: every candidate appears exactly once.

    The omission is only visible against the routed snapshot — the result is internally
    consistent — so accepting it would let a Router defect become an ordinary routing outcome.
    """
    snapshot = routing_snapshot(1842, npc_ids=(SHOPKEEPER, THIEF))
    router = OmittingRouter(TierScriptRouter(), omitted=THIEF)
    handoff = handoff_for(router)

    outcome = await handoff.route_now(snapshot)

    assert outcome.status is RoutingStatus.INVALID_RESULT
    assert outcome.assignments == ()
    # Named exactly, so the reason cannot be confused with the non-candidate rejection that
    # also mentions one NPC, nor with the shopkeeper, which this result assigned correctly.
    assert outcome.failure_reason == f"no tier was assigned for {THIEF}"


async def test_a_result_that_assigns_every_candidate_is_routed() -> None:
    """The opposite of the rule above, so completeness cannot pass by rejecting everything."""
    snapshot = routing_snapshot(1842, npc_ids=(SHOPKEEPER, THIEF))
    handoff = handoff_for(OmittingRouter(TierScriptRouter()))

    outcome = await handoff.route_now(snapshot)

    assert outcome.status is RoutingStatus.ROUTED
    assert {one.npc_id for one in outcome.assignments} == {SHOPKEEPER, THIEF}


async def test_a_result_whose_timestamp_does_not_correspond_fails_closed() -> None:
    """`docs/message_schemas.md` §5: session, world, sequence and timestamp correspond.

    The sequence still matches, so nothing else in the check can catch this one.
    """
    snapshot = routing_snapshot(1842)
    result = result_for(
        snapshot, (assignment(SHOPKEEPER),), timestamp_ms=TIMESTAMP_MS + 1
    )
    handoff = handoff_for(ScriptedRouter(result))

    outcome = await handoff.route_now(snapshot)

    assert outcome.status is RoutingStatus.INVALID_RESULT
    assert outcome.assignments == ()
    assert outcome.failure_reason == (
        f"result carries timestamp_ms {TIMESTAMP_MS + 1}, not {TIMESTAMP_MS}"
    )


async def test_a_rejected_result_is_observed_apart_from_a_demotion() -> None:
    """A Router defect and a demotion are the same downstream silence without this.

    The coordinator cancels queued work for an NPC absent from the assignments under a demotion
    reason, so the rejection has to say for itself that no assignment was ever produced.
    """
    snapshot = routing_snapshot(1842, npc_ids=(SHOPKEEPER, THIEF))
    observations = Observations()
    handoff = handoff_for(OmittingRouter(TierScriptRouter(), omitted=THIEF), observations)

    outcome = await handoff.route_now(snapshot)

    rejected = [one for one in observations.recorded if one.name == ROUTING_FAILED_CLOSED]
    assert [one.fields["status"] for one in rejected] == [RoutingStatus.INVALID_RESULT]
    assert rejected[0].fields["reason"] == outcome.failure_reason
    assert rejected[0].fields["sequence"] == 1842
    assert rejected[0].fields["session_id"] == SESSION_ID


async def test_a_routed_result_is_not_observed_as_rejected() -> None:
    snapshot = routing_snapshot(1842)
    observations = Observations()
    handoff = handoff_for(RecordingRouter(), observations)

    await handoff.route_now(snapshot)

    assert [one for one in observations.recorded if one.name == ROUTING_FAILED_CLOSED] == []


class Listener:
    """Stands in for the coordinator's routing listener, which promotion and expiry hang off."""

    def __init__(self) -> None:
        self.outcomes: list[RoutingOutcome] = []

    async def __call__(self, outcome: RoutingOutcome) -> None:
        self.outcomes.append(outcome)


async def test_a_trigger_routing_outcome_reaches_the_listener_like_any_other() -> None:
    handoff = handoff_for(StatefulRouter({SHOPKEEPER: AttentionTier.FOCUSED}))
    listener = Listener()
    handoff.listen(listener)

    outcome = await handoff.route_now(routing_snapshot(1842, target_npc_id=SHOPKEEPER))

    assert outcome.status is RoutingStatus.ROUTED
    assert listener.outcomes == [outcome]


async def test_a_snapshot_superseded_during_enrichment_never_reaches_the_router() -> None:
    """The trigger loses the race, so it reads the newer outcome instead of failing closed."""
    router = StatefulRouter({SHOPKEEPER: AttentionTier.FOCUSED})
    handoff = handoff_for(router)
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
    handoff = handoff_for(StatefulRouter({SHOPKEEPER: AttentionTier.FOCUSED}))
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
    handoff = handoff_for(router)
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
    handoff = handoff_for(router)
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
    handoff = handoff_for(RaisingRouter(RuntimeError("router exploded")))

    outcome = await handoff.route_now(routing_snapshot(1842, target_npc_id=SHOPKEEPER))

    assert outcome.status is RoutingStatus.ROUTER_FAILED
    assert outcome.assignments == ()
    assert "router exploded" in outcome.failure_reason


async def test_a_reset_session_lets_the_next_snapshot_route_from_scratch() -> None:
    """Sequence memory is per session, so a restarted session is not permanently superseded."""
    router = StatefulRouter({SHOPKEEPER: AttentionTier.FOCUSED})
    handoff = handoff_for(router)

    await handoff.route_now(routing_snapshot(8, target_npc_id=SHOPKEEPER))
    handoff.reset_session(SESSION_ID)
    outcome = await handoff.route_now(routing_snapshot(1, target_npc_id=SHOPKEEPER))

    assert [snapshot.sequence for snapshot in router.routed] == [8, 1]
    assert outcome.status is RoutingStatus.ROUTED
    assert outcome.sequence == 1


async def test_a_stopped_handoff_reports_that_it_is_not_running() -> None:
    handoff = handoff_for(RecordingRouter())
    assert handoff.is_running is False

    await handoff.start()
    assert handoff.is_running is True

    await handoff.stop()
    assert handoff.is_running is False


def documented_result() -> dict[str, Any]:
    """The `routing_result` the tracked contract publishes, parsed rather than transcribed."""
    section = MESSAGE_SCHEMAS.read_text().split("## 5. Router `routing_result`")[1]
    block = re.search(r"```json\n(.*?)\n```", section, re.DOTALL)
    assert block is not None, "section 5 no longer publishes a routing_result example"
    return json.loads(block.group(1))


def documented_assignments() -> tuple[RoutingAssignment, ...]:
    return tuple(
        RoutingAssignment(
            npc_id=documented["npc_id"],
            tier=AttentionTier(documented["tier"]),
            previous_tier=(
                AttentionTier(documented["previous_tier"])
                if documented["previous_tier"] is not None
                else None
            ),
            changed=documented["changed"],
            reasons=tuple(documented["reasons"]),
            direct_score=documented["direct_score"],
            propagated_score=documented["propagated_score"],
            final_score=documented["final_score"],
        )
        for documented in documented_result()["assignments"]
    )


def diagnostics(**overrides: Any) -> RoutingDiagnostics:
    documented: dict[str, Any] = documented_result()["diagnostics"]
    return RoutingDiagnostics(**(documented | overrides))


def test_the_owned_result_models_carry_every_field_section_5_documents() -> None:
    """Diff the models against the tracked JSON, so a dropped field fails instead of surviving."""
    documented = documented_result()

    assert {field.name for field in fields(RoutingResult)} == set(documented)
    assert {field.name for field in fields(RoutingAssignment)} == set(
        documented["assignments"][0]
    )
    assert {field.name for field in fields(TierCounts)} == set(documented["counts"])
    assert {field.name for field in fields(RoutingDiagnostics)} == set(
        documented["diagnostics"]
    )


async def test_a_result_carrying_the_documented_scores_counts_and_diagnostics_is_routed() -> None:
    """The whole section 5 example survives the port, values included."""
    snapshot = routing_snapshot(1842, npc_ids=(SHOPKEEPER, THIEF))
    documented = documented_result()
    result = result_for(
        snapshot,
        documented_assignments(),
        counts=TierCounts(**documented["counts"]),
        diagnostics=RoutingDiagnostics(**documented["diagnostics"]),
    )
    handoff = handoff_for(ScriptedRouter(result))

    outcome = await handoff.route_now(snapshot)

    assert outcome.status is RoutingStatus.ROUTED
    assert outcome.counts == TierCounts(focused=1, reactive=0, ambient=1)
    assert outcome.diagnostics == RoutingDiagnostics(
        focused_capacity=2, reactive_capacity=6, candidate_count=2, routing_time_ms=0.31
    )
    assert [assignment.final_score for assignment in outcome.assignments] == [10.91, 0.37]
    assert [assignment.direct_score for assignment in outcome.assignments] == [10.91, 0.37]
    assert [assignment.propagated_score for assignment in outcome.assignments] == [0.0, 0.0]


async def test_a_router_that_sends_none_of_the_optional_fields_is_still_routed() -> None:
    """Today's Router populates none of them; the absence is visible, not treated as valid."""
    handoff = handoff_for(RecordingRouter())

    outcome = await handoff.route_now(routing_snapshot(1842, target_npc_id=SHOPKEEPER))

    assert outcome.status is RoutingStatus.ROUTED
    assert outcome.counts is None
    assert outcome.diagnostics is None
    assert outcome.assignments[0].direct_score is None
    assert outcome.assignments[0].propagated_score is None
    assert outcome.assignments[0].final_score is None


async def test_counts_at_capacity_are_accepted_because_the_limit_is_a_maximum() -> None:
    snapshot = routing_snapshot(1842, npc_ids=(SHOPKEEPER, THIEF))
    result = result_for(
        snapshot,
        (assignment(SHOPKEEPER), assignment(THIEF)),
        counts=TierCounts(focused=2, reactive=0, ambient=0),
        diagnostics=diagnostics(focused_capacity=2),
    )
    handoff = handoff_for(ScriptedRouter(result))

    assert (await handoff.route_now(snapshot)).status is RoutingStatus.ROUTED


@pytest.mark.parametrize(
    "make_result",
    [
        lambda snapshot: result_for(
            snapshot,
            (assignment(SHOPKEEPER), assignment(THIEF, tier=AttentionTier.AMBIENT)),
            counts=TierCounts(focused=2, reactive=0, ambient=0),
        ),
        lambda snapshot: result_for(
            snapshot,
            (assignment(SHOPKEEPER), assignment(THIEF, tier=AttentionTier.AMBIENT)),
            counts=TierCounts(focused=1, reactive=0, ambient=1),
            diagnostics=diagnostics(candidate_count=3),
        ),
        lambda snapshot: result_for(
            snapshot,
            (assignment(SHOPKEEPER), assignment(THIEF)),
            counts=TierCounts(focused=2, reactive=0, ambient=0),
            diagnostics=diagnostics(focused_capacity=1),
        ),
        lambda snapshot: result_for(
            snapshot,
            (
                assignment(SHOPKEEPER, tier=AttentionTier.REACTIVE),
                assignment(THIEF, tier=AttentionTier.REACTIVE),
            ),
            counts=TierCounts(focused=0, reactive=2, ambient=0),
            diagnostics=diagnostics(reactive_capacity=1),
        ),
        lambda snapshot: result_for(
            snapshot,
            (assignment(SHOPKEEPER), assignment(THIEF)),
            diagnostics=diagnostics(focused_capacity=1),
        ),
        lambda snapshot: result_for(
            snapshot,
            (assignment(SHOPKEEPER), assignment(THIEF, tier=AttentionTier.AMBIENT)),
            counts={"focused": 1, "reactive": 0, "ambient": 1},
        ),
        lambda snapshot: result_for(
            snapshot,
            (assignment(SHOPKEEPER), assignment(THIEF, tier=AttentionTier.AMBIENT)),
            diagnostics={"focused_capacity": 2},
        ),
    ],
    ids=[
        "counts-contradict-the-assignments",
        "counts-do-not-sum-to-candidate-count",
        "focused-count-exceeds-focused-capacity",
        "reactive-count-exceeds-reactive-capacity",
        "capacity-exceeded-with-counts-absent",
        "counts-is-not-a-tier-count",
        "diagnostics-is-not-routing-diagnostics",
    ],
)
async def test_a_result_that_breaks_a_documented_invariant_fails_closed(
    make_result: object,
) -> None:
    snapshot = routing_snapshot(1842, npc_ids=(SHOPKEEPER, THIEF))
    router = ScriptedRouter(make_result(snapshot))  # type: ignore[operator]
    handoff = handoff_for(router)

    outcome = await handoff.route_now(snapshot)

    assert outcome.status is RoutingStatus.INVALID_RESULT
    assert outcome.assignments == ()
    assert outcome.counts is None
    assert outcome.diagnostics is None
