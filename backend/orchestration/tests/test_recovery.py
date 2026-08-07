"""Reopening the same database resumes the work, and never buys a second model call.

Owner: Jerome & Richard

Each test runs two services over one database file, the way a restart does. The publisher and
the clock are carried across so the second process publishes into the same recorder the first
one would have, and so a command's 15-second lifetime keeps running rather than starting again.
"""

from __future__ import annotations

from pathlib import Path

from backend.config import Settings
from backend.ingestion.event_store import EventStore
from backend.ingestion.tests.canonical_messages import (
    CONVERSATION_ID,
    EVENT_ID,
    SESSION_ID,
    SHOPKEEPER,
    TURN_ID,
    active_conversation,
)
from backend.ingestion.turn_store import TurnStore
from backend.models.mock_provider import MODEL_FOR_TIER, PROVIDER
from backend.orchestration.conversation_manager import ConversationState
from backend.orchestration.conversation_store import ConversationStore
from backend.orchestration.deduplication import ATTEMPTED, FAILED
from backend.orchestration.router_port import AttentionTier
from backend.orchestration.observations import (
    PROVIDER_OUTCOME_UNKNOWN,
    RECOVERED_COMMAND_REPUBLISHED,
)
from backend.orchestration.recovery import UNKNOWN_OUTCOME_ERROR_CODE
from backend.orchestration.telemetry_port import STATUS_ERROR
from backend.orchestration.tests.fake_routers import TierScriptRouter
from backend.orchestration.tests.fakes import ManualClock, RecordingPublisher
from backend.orchestration.tests.harness import Harness, running, settings_for


def conversation_store(harness: Harness) -> ConversationStore:
    """Read persisted conversation rows the way the manager's own restore reads them."""
    return ConversationStore(harness.pipeline.store, harness.pipeline.observations)


async def stored_state(harness: Harness, session_id: str) -> ConversationState:
    sessions = await conversation_store(harness).sessions()
    return ConversationState(
        next(one.state for one in sessions if one.session_id == session_id)
    )


async def attempt_outcomes(harness: Harness) -> list[str]:
    """Every attempt's outcome, closed ones included.

    `ProviderAttempts.unresolved` deliberately reports only attempts still open, so a closed
    outcome has no public reader. Naming the column keeps the assertion about which outcome was
    recorded rather than about a row's shape.
    """
    rows = await harness.pipeline.store.connection.execute_fetchall(
        "SELECT outcome FROM provider_attempts ORDER BY started_at_ms, claim_key"
    )
    return [str(row[0]) for row in rows]


async def crash_during_generation(
    settings: Settings,
    publisher: RecordingPublisher,
    clock: ManualClock,
    with_event: bool = False,
) -> None:
    """Start the service, get provider calls in flight, then stop without answering them.

    `with_event` adds a durable event row for the test that is about what survives. It does not
    add a second attempt: one request is in flight per NPC, so the event reaction holds the slot
    and the turn stays queued behind it.
    """
    async for first in running(settings, gated=True, publisher=publisher, clock=clock):
        await first.snapshot(active_conversation=active_conversation())
        if with_event:
            await first.event()
        await first.turn()
        await first.provider.wait_for_started(1)

        open_attempts = await first.pipeline.generation.attempts.unresolved()
        assert [(one.session_id, one.outcome) for one in open_attempts] == [
            (SESSION_ID, ATTEMPTED)
        ], "the attempt must be committed before the call"
        assert first.state() is ConversationState.AWAITING_RESPONSE
        assert await stored_state(first, SESSION_ID) == ConversationState.AWAITING_RESPONSE


async def test_durable_state_survives_reopening_the_same_database(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    publisher = RecordingPublisher()
    clock = ManualClock()

    await crash_during_generation(settings, publisher, clock, with_event=True)

    async for second in running(settings, publisher=publisher, clock=clock):
        await second.settle()

        turns = await TurnStore(second.pipeline.store).recent(
            SESSION_ID, CONVERSATION_ID, limit=10
        )
        assert [one.turn_id for one in turns] == [TURN_ID]

        event = await EventStore(second.pipeline.store).latest(SESSION_ID, EVENT_ID)
        assert event is not None and event.event.event_id == EVENT_ID

        threads = await conversation_store(second).threads()
        assert [one.conversation_id for one in threads] == [CONVERSATION_ID]

        counts = await second.durable_counts()
        assert (counts.claims, counts.attempts, counts.commands) == (1, 1, 1)


async def test_conversation_state_is_restored_rather_than_reset_to_idle(
    tmp_path: Path,
) -> None:
    """READY is only reachable if the active conversation came back.

    `note_published` reads the restored active conversation to choose between READY and IDLE,
    so a manager that started empty would leave this session IDLE with the same code path.
    """
    settings = settings_for(tmp_path)
    publisher = RecordingPublisher()
    clock = ManualClock()

    await crash_during_generation(settings, publisher, clock)

    async for second in running(settings, publisher=publisher, clock=clock):
        await second.settle()

        conversation = second.pipeline.intake.conversation
        assert conversation.active_conversation(SESSION_ID) is not None
        assert conversation.latest_turn_id(SESSION_ID, CONVERSATION_ID) == TURN_ID
        assert second.state() is ConversationState.READY


async def test_an_unknown_provider_outcome_recovers_through_fallback(
    tmp_path: Path,
) -> None:
    settings = settings_for(tmp_path)
    publisher = RecordingPublisher()
    clock = ManualClock()

    await crash_during_generation(settings, publisher, clock)

    async for second in running(settings, publisher=publisher, clock=clock):
        await second.settle()

        assert second.provider.started == [], "recovery must never call the provider"
        assert second.pipeline.recovery.last.answered_attempts == 1
        assert len(publisher.published) == 1

        command = publisher.published[0]
        assert command.npc_id == SHOPKEEPER
        assert command.fallback_used is True
        assert command.dialogue == "Cached for Mira's turn."
        assert second.observed(PROVIDER_OUTCOME_UNKNOWN)


async def test_the_recovered_attempt_is_reported_once_and_then_closed(
    tmp_path: Path,
) -> None:
    settings = settings_for(tmp_path)
    publisher = RecordingPublisher()
    clock = ManualClock()

    await crash_during_generation(settings, publisher, clock)

    async for second in running(settings, publisher=publisher, clock=clock):
        await second.settle()

        assert [one.outcome for one in await second.pipeline.generation.attempts.unresolved()] == []
        assert await attempt_outcomes(second) == [FAILED]

        facts = [
            fact for fact in second.telemetry.model_calls
            if fact.error_code == UNKNOWN_OUTCOME_ERROR_CODE
        ]
        assert len(facts) == 1
        assert facts[0].status == STATUS_ERROR
        assert facts[0].fallback_used is True
        # The call really was made before the process died, and Elson & Daniel count attempts
        # per provider, so the recovered fact names the provider from the attempt row.
        assert facts[0].provider == PROVIDER
        assert facts[0].model == MODEL_FOR_TIER[AttentionTier.FOCUSED]


async def test_a_third_start_finds_nothing_left_to_recover(tmp_path: Path) -> None:
    """Recovery is idempotent: a restart after a recovered restart must publish nothing."""
    settings = settings_for(tmp_path)
    publisher = RecordingPublisher()
    clock = ManualClock()

    await crash_during_generation(settings, publisher, clock)
    async for second in running(settings, publisher=publisher, clock=clock):
        await second.settle()
    published_after_recovery = len(publisher.published)

    async for third in running(settings, publisher=publisher, clock=clock):
        await third.settle()

        assert third.pipeline.recovery.last.answered_attempts == 0
        assert third.pipeline.recovery.last.republished_commands == 0
        assert len(publisher.published) == published_after_recovery
        assert third.provider.started == []


async def test_a_stored_but_unsent_command_is_republished_with_identical_bytes(
    tmp_path: Path,
) -> None:
    settings = settings_for(tmp_path)
    publisher = RecordingPublisher()
    clock = ManualClock()

    async for first in running(settings, publisher=publisher, clock=clock):
        publisher.hold()
        await first.snapshot(active_conversation=active_conversation())
        await first.turn()
        await publisher.wait_for_attempt(1)
        stored = await first.pipeline.commands.unpublished()
        assert len(stored) == 1, "the command must be committed before it is sent"

    publisher.release()
    async for second in running(settings, publisher=publisher, clock=clock):
        await second.settle()

        assert second.pipeline.recovery.last.republished_commands == 1
        assert second.provider.started == [], "a stored result is never regenerated"
        assert len(publisher.published) == 1
        assert publisher.sent_bytes[-1] == stored[0].serialized
        assert second.observed(RECOVERED_COMMAND_REPUBLISHED)


async def test_a_command_whose_lifetime_lapsed_while_down_expires_instead(
    tmp_path: Path,
) -> None:
    settings = settings_for(tmp_path)
    publisher = RecordingPublisher()
    clock = ManualClock()

    async for first in running(settings, publisher=publisher, clock=clock):
        publisher.hold()
        await first.snapshot(active_conversation=active_conversation())
        await first.turn()
        await publisher.wait_for_attempt(1)
        assert len(await first.pipeline.commands.unpublished()) == 1

    publisher.release()
    clock.advance(16_000)

    async for second in running(settings, publisher=publisher, clock=clock):
        await second.settle()

        assert publisher.published == [], "an expired command must not reach Minecraft"
        assert await second.pipeline.commands.unpublished() == ()
        assert second.state() is ConversationState.ENGAGED


async def test_starting_up_deletes_no_durable_evidence(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    publisher = RecordingPublisher()
    clock = ManualClock()

    async for first in running(settings, publisher=publisher, clock=clock):
        await first.snapshot(active_conversation=active_conversation())
        await first.turn()
        await first.settle()
        before = await first.durable_counts()

    async for second in running(settings, publisher=publisher, clock=clock):
        after = await second.durable_counts()

    assert after == before
    assert before.turns == 1 and before.commands == 1


async def promote_onto_a_waiting_turn(harness: Harness, router: TierScriptRouter) -> None:
    """Reach the one command that carries a turn identity without a turn having triggered it.

    The player speaks while the shopkeeper is Ambient, so the turn itself generates nothing and
    the conversation falls back to ENGAGED. The promotion that follows finds that same turn
    still waiting for an answer, so the command it produces carries the turn — which is the
    case where deciding from the trigger and deciding from the command disagree.
    """
    await harness.snapshot(sequence=1, active_conversation=active_conversation())
    await harness.turn()
    await harness.settle()
    assert harness.published_for(SHOPKEEPER) == []
    assert harness.state() is ConversationState.ENGAGED

    router.tiers[SHOPKEEPER] = AttentionTier.FOCUSED
    await harness.snapshot(sequence=2, active_conversation=active_conversation())


async def test_a_promotion_carrying_a_turn_moves_the_conversation_the_same_way_either_side_of_a_restart(
    tmp_path: Path,
) -> None:
    """One rule decides this, so where the command was published cannot change the answer."""
    live_path = tmp_path / "live"
    live_path.mkdir()
    live_settings = settings_for(live_path)
    async for live in running(
        live_settings, router=TierScriptRouter({SHOPKEEPER: AttentionTier.AMBIENT})
    ):
        router = live.router
        assert isinstance(router, TierScriptRouter)
        await promote_onto_a_waiting_turn(live, router)
        await live.settle()

        assert [command.turn_id for command in live.published_for(SHOPKEEPER)] == [TURN_ID]
        live_state = live.state()

    restarted_path = tmp_path / "restarted"
    restarted_path.mkdir()
    settings = settings_for(restarted_path)
    publisher = RecordingPublisher()
    clock = ManualClock()

    async for first in running(
        settings,
        router=TierScriptRouter({SHOPKEEPER: AttentionTier.AMBIENT}),
        publisher=publisher,
        clock=clock,
    ):
        router = first.router
        assert isinstance(router, TierScriptRouter)
        publisher.hold()
        await promote_onto_a_waiting_turn(first, router)
        await publisher.wait_for_attempt(1)
        assert len(await first.pipeline.commands.unpublished()) == 1

    publisher.release()
    async for second in running(settings, publisher=publisher, clock=clock):
        await second.settle()

        assert second.pipeline.recovery.last.republished_commands == 1
        assert [command.turn_id for command in publisher.published] == [TURN_ID]
        assert second.state() == live_state
        assert live_state is ConversationState.READY


async def test_a_redelivered_turn_after_a_restart_buys_no_second_call(
    tmp_path: Path,
) -> None:
    settings = settings_for(tmp_path)
    publisher = RecordingPublisher()
    clock = ManualClock()

    async for first in running(settings, publisher=publisher, clock=clock):
        await first.snapshot(active_conversation=active_conversation())
        await first.turn()
        await first.settle()
        assert len(first.provider.started) == 1

    async for second in running(settings, publisher=publisher, clock=clock):
        redelivered = await second.turn()
        await second.settle()

        assert redelivered.status_code == 200
        assert second.provider.started == []
        assert len(publisher.published) == 1
