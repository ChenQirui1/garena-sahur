"""What the queue between a trigger and a provider call must guarantee.

Owner: Jerome & Richard

Issue #8. Every assertion here is about observable output — which provider requests started, in
what order, how many ran at once, and which commands reached the publisher — never about how the
queue stores its work. Passing proves the owned pipeline against contract fakes only.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, AsyncIterator

import pytest_asyncio

from backend.ingestion.tests.canonical_messages import (
    CONVERSATION_ID,
    EVENT_ID,
    GUARD,
    SHOPKEEPER,
    THIEF,
    TURN_ID,
    active_conversation,
    npc,
    world_snapshot,
)
from backend.orchestration.conversation_manager import ConversationState
from backend.orchestration.generation_policy import NOTHING_TO_SPEAK_ABOUT
from backend.orchestration.observations import (
    GENERATION_SUPPRESSED,
    ROUTING_FAILED_CLOSED,
    TRIGGER_SUPPRESSED,
    WORK_CANCELLED,
    WORK_FAILED,
    WORK_SUPERSEDED,
)
from backend.orchestration.router_port import AttentionTier
from backend.orchestration.tests.fake_routers import (
    EventAwareRouter,
    FlakyRouter,
    OmittingRouter,
    StatefulRouter,
    TierScriptRouter,
)
from backend.orchestration.tests.harness import Harness, running, settings_for

FOCUSED = AttentionTier.FOCUSED
REACTIVE = AttentionTier.REACTIVE
AMBIENT = AttentionTier.AMBIENT

CROWD = tuple(f"crowd-{index:02d}" for index in range(12))


def crowd_snapshot(members: tuple[str, ...] = CROWD, **overrides: Any) -> dict[str, Any]:
    """A snapshot whose candidates are large enough — and ordered — for scheduling tests.

    Candidate order is the order reactions are queued in, so a test that cares about arrival
    order states it here rather than relying on how the event happens to name its actors.
    """
    return world_snapshot(
        npcs=[npc(npc_id) for npc_id in members],
        candidate_count=len(members),
        **overrides,
    )


@pytest_asyncio.fixture
async def conversation(tmp_path: Path) -> AsyncIterator[Harness]:
    """The default scene: the shopkeeper is the Focused conversation target."""
    async for harness in running(settings_for(tmp_path)):
        yield harness


async def test_a_snapshot_that_changes_nothing_never_calls_a_provider(
    conversation: Harness,
) -> None:
    await conversation.snapshot(sequence=1, active_conversation=active_conversation())
    await conversation.snapshot(sequence=2, active_conversation=active_conversation())
    await conversation.snapshot(sequence=3, active_conversation=active_conversation())
    await conversation.settle()

    assert conversation.telemetry.model_calls == []
    assert conversation.publisher.published == []


async def test_an_unchanged_assignment_never_calls_a_provider(tmp_path: Path) -> None:
    router = TierScriptRouter({SHOPKEEPER: REACTIVE})
    async for harness in running(settings_for(tmp_path), router=router):
        await harness.snapshot(sequence=1)
        await harness.settle()
        await harness.snapshot(sequence=2)
        await harness.settle()

        assert harness.telemetry.model_calls == []


async def test_a_demotion_never_calls_a_provider(tmp_path: Path) -> None:
    router = TierScriptRouter({SHOPKEEPER: FOCUSED})
    async for harness in running(settings_for(tmp_path), router=router):
        await harness.snapshot(sequence=1)
        await harness.settle()
        harness.telemetry.model_calls.clear()

        router.tiers[SHOPKEEPER] = REACTIVE
        await harness.snapshot(sequence=2)
        await harness.settle()

        assert harness.telemetry.model_calls == []


async def test_an_upward_promotion_without_current_behaviour_generates_once(
    tmp_path: Path,
) -> None:
    router = TierScriptRouter({SHOPKEEPER: AMBIENT})
    async for harness in running(settings_for(tmp_path), router=router):
        # An event the shopkeeper is part of, which it is too Ambient to react to.
        await harness.snapshot(sequence=1)
        await harness.settle()
        await harness.event(actor_npc_ids=[THIEF], target_npc_ids=[SHOPKEEPER])
        await harness.settle()
        assert harness.telemetry.model_calls == []

        # The event revision is long past; the promotion alone is what generates now.
        router.tiers[SHOPKEEPER] = FOCUSED
        await harness.snapshot(sequence=2)
        await harness.settle()

        assert [call.npc_id for call in harness.telemetry.model_calls] == [SHOPKEEPER]
        assert len(harness.published_for(SHOPKEEPER)) == 1


async def test_a_promotion_visible_only_on_the_trigger_path_still_generates(
    tmp_path: Path,
) -> None:
    """The turn's own routing is the only place the thief's promotion is ever visible.

    A persistent Router overwrites its previous-tier record on every call, so a transition the
    trigger path routes past is not merely unobserved — the next snapshot reports it unchanged.
    """
    router = StatefulRouter({SHOPKEEPER: FOCUSED})
    async for harness in running(settings_for(tmp_path), router=router):
        await harness.snapshot(sequence=1, active_conversation=active_conversation())
        await harness.event()
        await harness.settle()
        assert harness.published_for(THIEF) == []

        router.tiers[THIEF] = REACTIVE
        await harness.turn()
        await harness.settle()

        assert [command.event_id for command in harness.published_for(THIEF)] == [EVENT_ID]


async def test_a_promotion_already_satisfied_by_unexpired_behaviour_never_calls_a_provider(
    tmp_path: Path,
) -> None:
    router = TierScriptRouter({SHOPKEEPER: AMBIENT})
    async for harness in running(settings_for(tmp_path), router=router):
        await harness.snapshot(sequence=1)
        await harness.settle()
        await harness.event(actor_npc_ids=[THIEF], target_npc_ids=[SHOPKEEPER])
        await harness.settle()

        # No behaviour yet, so the first promotion generates.
        router.tiers[SHOPKEEPER] = FOCUSED
        await harness.snapshot(sequence=2)
        await harness.settle()
        assert len(harness.published_for(SHOPKEEPER)) == 1

        # Promoted again while that command is still live: nothing to generate.
        router.tiers[SHOPKEEPER] = AMBIENT
        await harness.snapshot(sequence=3)
        await harness.settle()
        router.tiers[SHOPKEEPER] = FOCUSED
        await harness.snapshot(sequence=4)
        await harness.settle()

        assert len(harness.published_for(SHOPKEEPER)) == 1
        assert [
            one["reason"] for one in harness.observed(TRIGGER_SUPPRESSED)
            if one["trigger"] == "promotion"
        ] == ["current behaviour already satisfies the promotion"]


async def test_an_expiry_answering_the_waiting_player_moves_the_conversation_too(
    conversation: Harness,
) -> None:
    """One predicate decides this, so expiry is not a special case of it either.

    The regenerated command answers the same waiting turn the expired one did, which is the
    whole reason expiry borrowed the conversation path. Reading the trigger instead would leave
    the conversation behind exactly here.
    """
    await conversation.snapshot(sequence=1, active_conversation=active_conversation())
    await conversation.turn()
    await conversation.settle()

    conversation.clock.advance(15_001)
    await conversation.snapshot(sequence=2, active_conversation=active_conversation())
    await conversation.settle()

    published = conversation.published_for(SHOPKEEPER)
    assert [command.turn_id for command in published] == [TURN_ID, TURN_ID]
    assert conversation.state() is ConversationState.READY


async def test_expired_behaviour_during_an_active_conversation_generates_again(
    conversation: Harness,
) -> None:
    await conversation.snapshot(sequence=1, active_conversation=active_conversation())
    await conversation.turn()
    await conversation.settle()
    assert len(conversation.published_for(SHOPKEEPER)) == 1

    conversation.clock.advance(15_001)
    await conversation.snapshot(sequence=2, active_conversation=active_conversation())
    await conversation.settle()

    assert len(conversation.published_for(SHOPKEEPER)) == 2


async def test_expired_behaviour_during_an_active_event_generates_again(
    tmp_path: Path,
) -> None:
    """Expiry is eligible on an active event, not only on an active conversation."""
    router = TierScriptRouter({SHOPKEEPER: REACTIVE, THIEF: REACTIVE})
    async for harness in running(settings_for(tmp_path), router=router):
        await harness.snapshot(sequence=1)
        await harness.settle()
        await harness.event(actor_npc_ids=[THIEF], target_npc_ids=[SHOPKEEPER])
        await harness.settle()
        assert len(harness.published_for(SHOPKEEPER)) == 1

        harness.clock.advance(15_001)
        await harness.snapshot(sequence=2)
        await harness.settle()

        assert len(harness.published_for(SHOPKEEPER)) == 2
        assert harness.observed(WORK_FAILED) == []


async def test_expiry_with_nothing_left_to_speak_about_never_calls_a_provider(
    tmp_path: Path,
) -> None:
    """The NPC keeps its tier, so only the missing focus can suppress this one.

    Holding the tier is the whole point: if the router demoted the shopkeeper when the
    conversation ended, the tier check would stop the work and this test would pass without
    ever reaching the rule it names.
    """
    router = TierScriptRouter({SHOPKEEPER: FOCUSED})
    async for harness in running(settings_for(tmp_path), router=router):
        await harness.snapshot(sequence=1, active_conversation=active_conversation())
        await harness.turn()
        await harness.settle()
        assert len(harness.published_for(SHOPKEEPER)) == 1

        # The conversation ends and no event is active, so nothing requires foreground work.
        harness.clock.advance(15_001)
        await harness.snapshot(sequence=2, active_conversation=None)
        await harness.settle()

        assert len(harness.published_for(SHOPKEEPER)) == 1
        assert harness.telemetry.model_calls[-1].turn_id == "turn-004"
        assert harness.observed(WORK_FAILED) == []
        # The silence is a recorded backend decision, not an unexplained gap (ADR 0009).
        assert [
            one["reason"] for one in harness.observed(TRIGGER_SUPPRESSED)
            if one["trigger"] == "expiry"
        ] == [NOTHING_TO_SPEAK_ABOUT]


async def test_a_router_failure_does_not_cancel_work_already_queued(
    tmp_path: Path,
) -> None:
    """Failing closed means inventing no assignment, not discarding queued work."""
    router = FlakyRouter(TierScriptRouter({SHOPKEEPER: REACTIVE, THIEF: REACTIVE}))
    async for harness in running(
        settings_for(tmp_path, focused_concurrency=1, reactive_concurrency=1, total_concurrency=1),
        router=router,
        gated=True,
    ):
        await harness.snapshot(sequence=1)
        await harness.settle_routing()

        await harness.event(actor_npc_ids=[THIEF], target_npc_ids=[SHOPKEEPER])
        await harness.provider.wait_for_started(1)
        queued = THIEF if harness.provider.started[0].npc_id == SHOPKEEPER else SHOPKEEPER
        assert harness.pending_generation_count() == 1

        router.failing = True
        await harness.snapshot(sequence=2)
        await harness.settle_routing()
        assert harness.observed(WORK_CANCELLED) == []
        assert harness.pending_generation_count() == 1

        router.failing = False
        await harness.snapshot(sequence=3)
        await harness.settle_routing()
        harness.provider.release_all()
        await harness.settle()

    assert len(harness.published_for(queued)) == 1


async def test_a_result_that_omits_a_candidate_does_not_cancel_its_queued_work(
    tmp_path: Path,
) -> None:
    """An omitted candidate is a Router defect, and it must not read as a demotion.

    The omission leaves an internally consistent result, so before it was rejected the NPC was
    simply absent from the assignments — indistinguishable downstream from having been demoted
    out of a generating tier, and its queued line was cancelled for that reason.
    """
    inner = TierScriptRouter({SHOPKEEPER: REACTIVE, THIEF: REACTIVE})
    router = OmittingRouter(inner)
    async for harness in running(
        settings_for(tmp_path, focused_concurrency=1, reactive_concurrency=1, total_concurrency=1),
        router=router,
        gated=True,
    ):
        await harness.snapshot(sequence=1)
        await harness.settle_routing()

        await harness.event(actor_npc_ids=[THIEF], target_npc_ids=[SHOPKEEPER])
        await harness.provider.wait_for_started(1)
        queued = THIEF if harness.provider.started[0].npc_id == SHOPKEEPER else SHOPKEEPER
        assert harness.pending_generation_count() == 1

        router.omitted = queued
        await harness.snapshot(sequence=2)
        await harness.settle_routing()
        assert harness.observed(WORK_CANCELLED) == []
        assert harness.pending_generation_count() == 1

        router.omitted = None
        await harness.snapshot(sequence=3)
        await harness.settle_routing()
        harness.provider.release_all()
        await harness.settle()

    # The defect is recorded as its own thing, not as the NPC losing a generating tier.
    rejected = harness.observed(ROUTING_FAILED_CLOSED)
    assert [one["sequence"] for one in rejected] == [2]
    assert queued in str(rejected[0]["reason"])
    assert len(harness.published_for(queued)) == 1


async def test_a_newer_turn_supersedes_pending_event_work_for_the_same_npc(
    tmp_path: Path,
) -> None:
    async for harness in running(
        settings_for(tmp_path, focused_concurrency=1, reactive_concurrency=1, total_concurrency=1),
        router=EventAwareRouter(),
        gated=True,
    ):
        await harness.snapshot(sequence=1, active_conversation=active_conversation())
        await harness.settle_routing()

        # One event reaction occupies the single provider slot; the shopkeeper's queues behind.
        await harness.event(actor_npc_ids=[THIEF], target_npc_ids=[GUARD])
        await harness.event(
            revision=2, actor_npc_ids=[THIEF], target_npc_ids=[SHOPKEEPER, GUARD]
        )
        await harness.provider.wait_for_started(1)

        await harness.turn()
        superseded = harness.observed(WORK_SUPERSEDED)

        harness.provider.release_all()
        await harness.settle()

    # Revision 2 restated revision 1 for the others; only the shopkeeper's was outranked by
    # the turn, which is the case the criterion names.
    by_turn = [one for one in superseded if one["superseded_by"] == "turn"]
    assert [one["npc_id"] for one in by_turn] == [SHOPKEEPER]
    assert [one["trigger"] for one in by_turn] == ["event"]
    # Whatever the shopkeeper had queued, the turn is the only thing it ends up saying.
    assert [command.turn_id for command in harness.published_for(SHOPKEEPER)] == ["turn-004"]


async def test_demotion_to_ambient_cancels_queued_work(tmp_path: Path) -> None:
    router = TierScriptRouter({SHOPKEEPER: REACTIVE, THIEF: REACTIVE})
    async for harness in running(
        settings_for(tmp_path, focused_concurrency=1, reactive_concurrency=1, total_concurrency=1),
        router=router,
        gated=True,
    ):
        await harness.snapshot(sequence=1)
        await harness.settle_routing()

        await harness.event(actor_npc_ids=[THIEF], target_npc_ids=[SHOPKEEPER])
        await harness.provider.wait_for_started(1)
        in_flight = harness.provider.started[0].npc_id
        queued = THIEF if in_flight == SHOPKEEPER else SHOPKEEPER

        router.tiers[queued] = AMBIENT
        await harness.snapshot(sequence=2)
        await harness.settle_routing()

        harness.provider.release_all()
        await harness.settle()

    assert [one["npc_id"] for one in harness.observed(WORK_CANCELLED)] == [queued]
    assert harness.published_for(queued) == []
    assert len(harness.published_for(in_flight)) == 1


async def test_demotion_to_ambient_discards_late_provider_output(tmp_path: Path) -> None:
    router = TierScriptRouter({SHOPKEEPER: REACTIVE})
    async for harness in running(settings_for(tmp_path), router=router, gated=True):
        await harness.snapshot(sequence=1)
        await harness.settle_routing()

        await harness.event(actor_npc_ids=[THIEF], target_npc_ids=[SHOPKEEPER])
        await harness.provider.wait_for_started(1)

        router.tiers[SHOPKEEPER] = AMBIENT
        await harness.snapshot(sequence=2)
        await harness.settle_routing()

        harness.provider.release_all()
        await harness.settle()

    # The model call was spent and is reported; the behaviour it produced never reaches Minecraft.
    assert [call.npc_id for call in harness.telemetry.model_calls] == [SHOPKEEPER]
    assert harness.published_for(SHOPKEEPER) == []


async def test_a_terminal_revision_cancels_work_claimed_for_an_earlier_revision(
    tmp_path: Path,
) -> None:
    async for harness in running(
        settings_for(tmp_path, focused_concurrency=1, reactive_concurrency=1, total_concurrency=1),
        router=EventAwareRouter(),
        gated=True,
    ):
        await harness.snapshot(sequence=1)
        await harness.settle_routing()

        await harness.event(actor_npc_ids=[THIEF], target_npc_ids=[SHOPKEEPER, GUARD])
        await harness.provider.wait_for_started(1)
        started = harness.provider.started[0].npc_id

        await harness.event(
            revision=2,
            status="ended",
            actor_npc_ids=[THIEF],
            target_npc_ids=[SHOPKEEPER, GUARD],
        )

        harness.provider.release_all()
        await harness.settle()

    cancelled = [one["npc_id"] for one in harness.observed(WORK_CANCELLED)]
    assert started not in cancelled
    assert set(cancelled) == {SHOPKEEPER, THIEF} - {started}
    # The one that had already started spent its model call and is discarded before publication.
    assert harness.publisher.published == []


async def test_a_terminal_revision_discards_late_provider_output(tmp_path: Path) -> None:
    async for harness in running(
        settings_for(tmp_path), router=EventAwareRouter(), gated=True
    ):
        await harness.snapshot(sequence=1)
        await harness.settle_routing()

        await harness.event(actor_npc_ids=[THIEF], target_npc_ids=[SHOPKEEPER])
        await harness.provider.wait_for_started(1)

        await harness.event(
            revision=2, status="cancelled", actor_npc_ids=[THIEF], target_npc_ids=[SHOPKEEPER]
        )

        harness.provider.release_all()
        await harness.settle()

    # Both relevant NPCs spent a model call, and neither reached Minecraft.
    assert {call.npc_id for call in harness.telemetry.model_calls} == {SHOPKEEPER, THIEF}
    assert harness.publisher.published == []


async def test_provider_concurrency_is_bounded_per_tier_and_in_total(
    tmp_path: Path,
) -> None:
    router = TierScriptRouter(
        {CROWD[0]: FOCUSED, CROWD[1]: FOCUSED, CROWD[2]: FOCUSED}, default=REACTIVE
    )
    async for harness in running(settings_for(tmp_path), router=router, gated=True):
        await harness.ingest("world.snapshot", crowd_snapshot(sequence=1))
        await harness.settle_routing()

        await harness.event(
            actor_npc_ids=list(CROWD[:6]), target_npc_ids=list(CROWD[6:]), responder_npc_ids=[]
        )
        await harness.provider.wait_for_started(8)

        assert harness.provider.peak_in_flight == 8
        assert harness.provider.peak_in_flight_for(FOCUSED) == 2
        assert harness.provider.peak_in_flight_for(REACTIVE) == 6

        harness.provider.release_all()
        await harness.settle()

    assert harness.provider.peak_in_flight == 8


async def test_the_total_limit_binds_while_every_tier_still_has_room(
    tmp_path: Path,
) -> None:
    """Eight total is its own rule, and the default cannot show it.

    Specification #1 caps concurrency at two Focused, six Reactive *and* eight total, but two
    plus six is eight: under the defaults, enforcing the total and not enforcing it give the
    same answer, so the case above passes with the total limit deleted. Binding the total below
    the tier sum is the only scene where the rule is visible on its own. Every candidate here
    is Reactive and its tier is nowhere near full, so nothing but the total can hold the fifth
    call back.
    """
    router = TierScriptRouter({}, default=REACTIVE)
    async for harness in running(
        settings_for(tmp_path, total_concurrency=4), router=router, gated=True
    ):
        await harness.ingest("world.snapshot", crowd_snapshot(sequence=1))
        await harness.settle_routing()

        await harness.event(
            actor_npc_ids=list(CROWD[:6]), target_npc_ids=list(CROWD[6:]), responder_npc_ids=[]
        )
        await harness.provider.wait_for_started(4)

        assert harness.provider.peak_in_flight_for(REACTIVE) == 4, "below the Reactive six"
        assert harness.pending_generation_count() > 0, "the rest are held by the total alone"

        harness.provider.release_all()
        await harness.settle()

    assert harness.provider.peak_in_flight == 4


async def test_one_request_is_in_flight_per_npc(conversation: Harness) -> None:
    """The second turn arrives once the first is already in flight, which is the only scene
    where the per-NPC slot is what serialises them.

    Sending both before either starts does not test this rule: the second turn is still
    unconfirmed when the first is admitted, so it is not queued yet and nothing about the
    in-flight slot decides anything. Here the second turn is genuinely waiting with a free
    Focused slot and a free total slot, so its own NPC being busy is the only thing left.
    """
    await conversation.snapshot(sequence=1, active_conversation=active_conversation())
    await conversation.settle_routing()
    conversation.provider.gate()

    await conversation.turn()
    await conversation.provider.wait_for_started(1)
    await conversation.turn(turn_id="turn-005", turn_index=5, text="And after that?")

    assert conversation.pending_generation_count() == 1, "the second turn must be waiting"
    assert conversation.provider.peak_in_flight_for_npc(SHOPKEEPER) == 1

    conversation.provider.release_all()
    await conversation.settle()

    assert len(conversation.provider.started) == 2, "both turns are answered, one at a time"
    assert conversation.provider.peak_in_flight_for_npc(SHOPKEEPER) == 1


async def test_focused_work_is_dispatched_before_earlier_reactive_work(
    tmp_path: Path,
) -> None:
    reactive_first = (CROWD[0], CROWD[1], CROWD[2])
    router = TierScriptRouter({CROWD[0]: REACTIVE, CROWD[1]: REACTIVE, CROWD[2]: FOCUSED})
    async for harness in running(
        settings_for(tmp_path, focused_concurrency=1, reactive_concurrency=1, total_concurrency=1),
        router=router,
        gated=True,
    ):
        await harness.ingest(
            "world.snapshot", crowd_snapshot(reactive_first, sequence=1)
        )
        await harness.settle_routing()

        # The two Reactive NPCs are candidates first, so arrival order alone would dispatch
        # them before the Focused one. Tier priority must outrank arrival order.
        await harness.event(
            actor_npc_ids=[CROWD[0], CROWD[1]], target_npc_ids=[CROWD[2]], responder_npc_ids=[]
        )
        await harness.provider.wait_for_started(1)

        assert [request.npc_id for request in harness.provider.started] == [CROWD[2]]

        harness.provider.release_all()
        await harness.settle()

    assert harness.provider.started[0].npc_id == CROWD[2]


async def test_work_within_one_tier_is_dispatched_first_in_first_out(
    tmp_path: Path,
) -> None:
    # Arrival order is deliberately neither alphabetical nor reversed, so first-in-first-out
    # gives a visibly different answer from any ordering by identity.
    arrival = (CROWD[3], CROWD[1], CROWD[0], CROWD[2])
    router = TierScriptRouter({}, default=REACTIVE)
    async for harness in running(
        settings_for(tmp_path, focused_concurrency=1, reactive_concurrency=1, total_concurrency=1),
        router=router,
        gated=True,
    ):
        await harness.ingest("world.snapshot", crowd_snapshot(arrival, sequence=1))
        await harness.settle_routing()

        await harness.event(
            actor_npc_ids=list(arrival), target_npc_ids=[], responder_npc_ids=[]
        )
        await harness.provider.wait_for_started(1)

        harness.provider.release_all()
        await harness.settle()

    assert tuple(request.npc_id for request in harness.provider.started) == arrival


async def test_high_frequency_restatements_do_not_grow_the_generation_queue(
    tmp_path: Path,
) -> None:
    """Twenty revisions of one event leave one pending item per NPC, not twenty."""
    async for harness in running(
        settings_for(tmp_path, focused_concurrency=1, reactive_concurrency=1, total_concurrency=1),
        router=EventAwareRouter(),
        gated=True,
    ):
        await harness.snapshot(sequence=1)
        await harness.settle_routing()

        await harness.event(revision=1)
        await harness.provider.wait_for_started(1)

        for revision in range(2, 22):
            await harness.event(revision=revision, status="updated")
            assert harness.pending_generation_count() <= 2

        assert harness.pending_generation_count() <= 2

        harness.provider.release_all()
        await harness.settle()

    assert harness.pending_generation_count() == 0


async def test_a_demotion_on_the_trigger_path_cancels_work_queued_behind_a_provider(
    tmp_path: Path,
) -> None:
    """A turn routes on the spot, and its assignments run the same cancellation pass.

    They did not before #22: work queued behind a busy provider could be demoted by a turn's
    own routing with nothing cancelling it, leaving only the revalidation before invocation.
    """
    router = TierScriptRouter({SHOPKEEPER: REACTIVE, THIEF: REACTIVE, GUARD: FOCUSED})
    async for harness in running(
        settings_for(tmp_path, focused_concurrency=1, reactive_concurrency=1, total_concurrency=1),
        router=router,
        gated=True,
    ):
        await harness.ingest(
            "world.snapshot",
            crowd_snapshot(
                (SHOPKEEPER, THIEF, GUARD),
                sequence=1,
                active_conversation=active_conversation(GUARD),
            ),
        )
        await harness.settle_routing()

        await harness.event(actor_npc_ids=[THIEF], target_npc_ids=[SHOPKEEPER])
        await harness.provider.wait_for_started(1)
        queued = THIEF if harness.provider.started[0].npc_id == SHOPKEEPER else SHOPKEEPER

        router.tiers[queued] = AMBIENT
        await harness.turn(target_npc_id=GUARD, conversation_id=CONVERSATION_ID)

        harness.provider.release_all()
        await harness.settle()

    assert [one["npc_id"] for one in harness.observed(WORK_CANCELLED)] == [queued]
    assert queued not in {call.npc_id for call in harness.telemetry.model_calls}
    assert harness.published_for(queued) == []


async def test_concurrent_deliveries_leave_one_claim_per_trigger_identity(
    conversation: Harness,
) -> None:
    await conversation.snapshot(sequence=1, active_conversation=active_conversation())
    await conversation.settle_routing()

    await asyncio.gather(
        conversation.turn(),
        conversation.turn(),
        conversation.snapshot(sequence=2, active_conversation=active_conversation()),
        conversation.event(actor_npc_ids=[THIEF], target_npc_ids=[SHOPKEEPER]),
    )
    await conversation.settle()

    turn_calls = [
        call for call in conversation.telemetry.model_calls if call.turn_id == "turn-004"
    ]
    assert len(turn_calls) == 1
    assert conversation.state() in {ConversationState.READY, ConversationState.ENGAGED}


async def test_a_cancelled_turn_returns_the_conversation_to_engaged(
    tmp_path: Path,
) -> None:
    router = TierScriptRouter({SHOPKEEPER: FOCUSED})
    async for harness in running(settings_for(tmp_path), router=router, gated=True):
        await harness.snapshot(sequence=1, active_conversation=active_conversation())
        await harness.settle_routing()

        await harness.turn()
        await harness.provider.wait_for_started(1)
        assert harness.state() is ConversationState.AWAITING_RESPONSE

        router.tiers[SHOPKEEPER] = AMBIENT
        await harness.snapshot(sequence=2, active_conversation=active_conversation())
        await harness.settle_routing()

        harness.provider.release_all()
        await harness.settle()

    assert harness.published_for(SHOPKEEPER) == []
    assert harness.state() is ConversationState.ENGAGED
    assert [one["reason"] for one in harness.observed(GENERATION_SUPPRESSED)] != []
