"""Reopening the same database resumes the work, and never buys a second model call.

Owner: Jerome & Richard

Each test runs two services over one database file, the way a restart does. The publisher and
the clock are carried across so the second process publishes into the same recorder the first
one would have, and so a command's 15-second lifetime keeps running rather than starting again.
"""

from __future__ import annotations

from pathlib import Path

from backend.config import Settings
from backend.ingestion.tests.canonical_messages import (
    CONVERSATION_ID,
    EVENT_ID,
    SESSION_ID,
    SHOPKEEPER,
    TURN_ID,
    active_conversation,
)
from backend.models.mock_provider import MODEL_FOR_TIER, PROVIDER
from backend.orchestration.conversation_manager import ConversationState
from backend.orchestration.router_port import AttentionTier
from backend.orchestration.deduplication import ATTEMPTED, FAILED
from backend.orchestration.observations import (
    PROVIDER_OUTCOME_UNKNOWN,
    RECOVERED_COMMAND_REPUBLISHED,
)
from backend.orchestration.recovery import UNKNOWN_OUTCOME_ERROR_CODE
from backend.orchestration.telemetry_port import STATUS_ERROR
from backend.orchestration.tests.fake_routers import TierScriptRouter
from backend.orchestration.tests.fakes import ManualClock, RecordingPublisher
from backend.orchestration.tests.harness import Harness, running, settings_for


async def rows(harness: Harness, query: str, *parameters: object) -> list[tuple[object, ...]]:
    return [
        tuple(row)
        for row in await harness.pipeline.store.connection.execute_fetchall(query, parameters)
    ]


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
        await first.provider.started_after(1)

        attempts = await rows(
            first, "SELECT outcome FROM provider_attempts WHERE session_id = ?", SESSION_ID
        )
        assert attempts == [(ATTEMPTED,)], "the attempt must be committed before the call"
        assert first.state() is ConversationState.AWAITING_RESPONSE
        assert await rows(
            first, "SELECT state FROM conversation_sessions WHERE session_id = ?", SESSION_ID
        ) == [(ConversationState.AWAITING_RESPONSE.value,)]


async def test_durable_state_survives_reopening_the_same_database(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    publisher = RecordingPublisher()
    clock = ManualClock()

    await crash_during_generation(settings, publisher, clock, with_event=True)

    async for second in running(settings, publisher=publisher, clock=clock):
        await second.settle()

        assert await rows(second, "SELECT turn_id FROM conversation_turns") == [(TURN_ID,)]
        assert await rows(second, "SELECT event_id FROM game_events") == [(EVENT_ID,)]
        assert await rows(second, "SELECT COUNT(*) FROM generation_claims") == [(1,)]
        assert await rows(second, "SELECT COUNT(*) FROM provider_attempts") == [(1,)]
        assert await rows(second, "SELECT COUNT(*) FROM behaviour_commands") == [(1,)]
        assert await rows(
            second, "SELECT conversation_id FROM conversation_threads"
        ) == [(CONVERSATION_ID,)]


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
        assert await rows(second, "SELECT outcome FROM provider_attempts") == [(FAILED,)]

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
        await first.provider.started_after(1)
        for _ in range(50):
            if publisher.attempts:
                break
            await first.settle_routing()
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
        await first.provider.started_after(1)
        for _ in range(50):
            if publisher.attempts:
                break
            await first.settle_routing()
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
        before = await rows(
            first,
            "SELECT (SELECT COUNT(*) FROM conversation_turns),"
            " (SELECT COUNT(*) FROM game_events),"
            " (SELECT COUNT(*) FROM behaviour_commands),"
            " (SELECT COUNT(*) FROM generation_claims)",
        )

    async for second in running(settings, publisher=publisher, clock=clock):
        after = await rows(
            second,
            "SELECT (SELECT COUNT(*) FROM conversation_turns),"
            " (SELECT COUNT(*) FROM game_events),"
            " (SELECT COUNT(*) FROM behaviour_commands),"
            " (SELECT COUNT(*) FROM generation_claims)",
        )

    assert after == before
    assert before[0][0] == 1 and before[0][2] == 1


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
        await first.provider.started_after(1)
        for _ in range(50):
            if publisher.attempts:
                break
            await first.settle_routing()
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
