"""One active conversation and one player turn become exactly one behaviour command.

Owner: Jerome & Richard

The whole trace is driven through the HTTP intake boundary so the assertions are about
observable behaviour, not about how validation, storage, policy, context, and publication are
split up behind it.
"""

from __future__ import annotations

from pathlib import Path
from typing import AsyncIterator

import pytest
import pytest_asyncio

from backend.ingestion.message_validation import validate_conversation_turn
from backend.ingestion.tests.canonical_messages import (
    CONVERSATION_ID,
    SESSION_ID,
    SHOPKEEPER,
    THIEF,
    TURN_ID,
    active_conversation,
    conversation_turn,
)
from backend.orchestration.conversation_manager import ConversationState
from backend.orchestration.development_router import AmbientOnlyRouter
from backend.orchestration.observations import (
    GENERATION_SUPPRESSED,
    MISSING_PROFILE,
    UNCONFIRMED_TURN_DISCARDED,
)
from backend.orchestration.tests.harness import Harness, running, settings_for


def counted(fact: dict[str, object], name: str) -> int:
    """One token count off a telemetry record, which carries `object` values by contract."""
    counted = fact[name]
    assert isinstance(counted, int), f"{name} is {counted!r}, not a count"
    return counted


@pytest_asyncio.fixture
async def harness(tmp_path: Path) -> AsyncIterator[Harness]:
    async for started in running(settings_for(tmp_path)):
        yield started


async def test_an_active_conversation_and_one_turn_produce_one_command(
    harness: Harness,
) -> None:
    await harness.snapshot(active_conversation=active_conversation())
    response = await harness.turn()
    await harness.settle()

    assert response.status_code == 202
    assert len(harness.publisher.published) == 1

    command = harness.publisher.published[0]
    assert command.session_id == SESSION_ID
    assert command.npc_id == SHOPKEEPER
    assert command.conversation_id == CONVERSATION_ID
    assert command.turn_id == TURN_ID
    assert command.event_id is None
    assert command.source_sequence == 1842
    assert command.command_sequence == 1
    assert command.created_at_ms == harness.clock.now_ms()
    assert command.expires_at_ms == command.created_at_ms + 15_000
    assert command.dialogue
    assert command.command_id and command.request_id


async def test_the_command_is_stored_before_it_is_published(harness: Harness) -> None:
    await harness.snapshot(active_conversation=active_conversation())
    await harness.turn()
    await harness.settle()

    assert harness.publisher.stored_when_published == [harness.publisher.published[0]]


async def test_the_conversation_reaches_ready_through_the_specified_states(
    harness: Harness,
) -> None:
    observed = [harness.state()]
    harness.publisher.on_publish = lambda: observed.append(harness.state())

    await harness.snapshot(active_conversation=active_conversation())
    observed.append(harness.state())
    await harness.turn()
    await harness.settle()
    observed.append(harness.state())

    assert observed == [
        ConversationState.IDLE,
        ConversationState.ENGAGED,
        ConversationState.AWAITING_RESPONSE,
        ConversationState.READY,
    ]


async def test_a_duplicate_turn_produces_no_second_command_or_model_call(
    harness: Harness,
) -> None:
    await harness.snapshot(active_conversation=active_conversation())
    first = await harness.turn()
    second = await harness.turn()
    third = await harness.turn()
    await harness.settle()

    assert first.status_code == 202
    assert second.status_code == third.status_code == 200
    assert second.json()["outcome"] == "duplicate"
    assert len(harness.publisher.published) == 1
    assert len(harness.telemetry.model_calls) == 1


async def test_an_ambient_target_never_reaches_a_provider(tmp_path: Path) -> None:
    """The turn addresses the active conversation, so only the tier can suppress it."""
    async for ambient in running(settings_for(tmp_path), AmbientOnlyRouter()):
        await ambient.snapshot(active_conversation=active_conversation())
        await ambient.turn()
        await ambient.settle()

        assert ambient.publisher.published == []
        assert ambient.telemetry.model_calls == []
        assert ambient.state() is ConversationState.ENGAGED
        assert ambient.observed(GENERATION_SUPPRESSED) == [
            {
                "session_id": SESSION_ID,
                "turn_id": TURN_ID,
                "npc_id": SHOPKEEPER,
                "reason": "target is ambient",
            }
        ]


async def test_a_turn_that_reaches_generation_twice_still_yields_one_command(
    harness: Harness,
) -> None:
    """Redelivery that gets past intake is stopped by the durable generation claim."""
    await harness.snapshot(active_conversation=active_conversation())
    await harness.turn()

    await harness.settle()

    turn = validate_conversation_turn(conversation_turn())
    await harness.pipeline.generation.on_triggered_turn(turn)
    await harness.settle()

    assert len(harness.publisher.published) == 1
    assert len(harness.telemetry.model_calls) == 1
    assert harness.observed(GENERATION_SUPPRESSED) == [
        {
            "session_id": SESSION_ID,
            "turn_id": TURN_ID,
            "npc_id": SHOPKEEPER,
            "reason": "generation was already claimed",
        }
    ]


async def test_each_command_for_one_npc_takes_the_next_command_sequence(
    harness: Harness,
) -> None:
    await harness.snapshot(active_conversation=active_conversation())
    await harness.turn(turn_id="turn-004", turn_index=4)
    await harness.turn(turn_id="turn-005", turn_index=5)
    await harness.turn(turn_id="turn-006", turn_index=6)
    await harness.settle()

    assert [command.command_sequence for command in harness.publisher.published] == [1, 2, 3]
    assert len({command.command_id for command in harness.publisher.published}) == 3


async def test_a_turn_the_player_did_not_speak_is_stored_but_generates_nothing(
    harness: Harness,
) -> None:
    """Specification #1 allows generation for an accepted player turn (#2 owns the enum)."""
    await harness.snapshot(active_conversation=active_conversation())
    response = await harness.turn(speaker_type="npc", speaker_id=SHOPKEEPER)
    await harness.settle()

    assert response.status_code == 202
    assert harness.publisher.published == []
    assert harness.telemetry.model_calls == []
    assert harness.state() is ConversationState.ENGAGED

    stored = await harness.pipeline.intake.turns.recent(SESSION_ID, CONVERSATION_ID, limit=8)
    assert [one.turn_id for one in stored] == [TURN_ID]


async def test_a_turn_before_its_snapshot_waits_and_then_produces_one_command(
    harness: Harness,
) -> None:
    await harness.snapshot(sequence=1841)
    await harness.turn()
    await harness.settle()

    assert harness.publisher.published == []

    await harness.snapshot(sequence=1842, active_conversation=active_conversation())
    await harness.settle()

    assert len(harness.publisher.published) == 1
    assert harness.publisher.published[0].source_sequence == 1842


async def test_an_unconfirmed_turn_is_discarded_by_a_snapshot_that_does_not_match(
    harness: Harness,
) -> None:
    await harness.snapshot(sequence=1841)
    await harness.turn()
    await harness.snapshot(sequence=1842)
    await harness.settle()

    assert harness.publisher.published == []
    assert harness.observed(UNCONFIRMED_TURN_DISCARDED) == [
        {"session_id": SESSION_ID, "conversation_id": CONVERSATION_ID, "turn_id": TURN_ID}
    ]


async def test_only_one_turn_waits_for_confirmation_at_a_time(harness: Harness) -> None:
    await harness.snapshot(sequence=1841)
    await harness.turn(turn_id="turn-004", turn_index=4)
    await harness.turn(turn_id="turn-005", turn_index=5)
    await harness.snapshot(sequence=1842, active_conversation=active_conversation())
    await harness.settle()

    assert [command.turn_id for command in harness.publisher.published] == ["turn-005"]
    assert harness.observed(UNCONFIRMED_TURN_DISCARDED) == [
        {"session_id": SESSION_ID, "conversation_id": CONVERSATION_ID, "turn_id": "turn-004"}
    ]


async def test_the_world_snapshot_alone_opens_and_closes_the_conversation(
    harness: Harness,
) -> None:
    await harness.turn()
    await harness.settle()
    assert harness.state() is ConversationState.IDLE

    await harness.snapshot(sequence=1843, active_conversation=active_conversation())
    await harness.settle()
    assert harness.state() is ConversationState.READY

    await harness.snapshot(sequence=1844)
    await harness.settle()
    assert harness.state() is ConversationState.IDLE


async def test_a_target_switch_starts_a_new_conversation_and_retains_the_old_history(
    harness: Harness,
) -> None:
    await harness.snapshot(sequence=1842, active_conversation=active_conversation())
    await harness.turn()
    await harness.settle()
    await harness.snapshot(
        sequence=1843,
        active_conversation={"conversation_id": "conversation-08", "target_npc_id": THIEF},
    )
    await harness.turn(
        conversation_id="conversation-08",
        turn_id="turn-100",
        turn_index=0,
        target_npc_id=THIEF,
    )
    await harness.settle()

    published = harness.publisher.published
    assert [command.conversation_id for command in published] == [
        CONVERSATION_ID,
        "conversation-08",
    ]
    retained = await harness.pipeline.intake.turns.recent(SESSION_ID, CONVERSATION_ID, limit=8)
    assert [turn.turn_id for turn in retained] == [TURN_ID]


async def test_the_router_sees_the_conversation_projection_and_its_latest_turn(
    harness: Harness,
) -> None:
    await harness.snapshot(active_conversation=active_conversation())
    await harness.turn()
    await harness.settle()

    routed = harness.routed[-1].active_conversation
    assert routed is not None
    assert routed.conversation_id == CONVERSATION_ID
    assert routed.target_npc_id == SHOPKEEPER
    assert routed.state == "awaiting_npc"
    assert routed.latest_turn_id == TURN_ID


async def test_one_model_call_fact_is_emitted_for_the_generated_command(
    harness: Harness,
) -> None:
    await harness.snapshot(active_conversation=active_conversation())
    await harness.turn()
    await harness.settle()

    fact = harness.telemetry.model_calls[0].as_record()
    command = harness.publisher.published[0]
    assert fact["record_type"] == "model_call"
    assert fact["session_id"] == SESSION_ID
    assert fact["npc_id"] == SHOPKEEPER
    assert fact["tier"] == "focused"
    assert fact["request_id"] == command.request_id
    assert fact["conversation_id"] == CONVERSATION_ID
    assert fact["turn_id"] == TURN_ID
    assert fact["source_sequence"] == 1842
    assert fact["status"] == "success"
    assert fact["fallback_used"] is False
    assert fact["error_code"] is None
    assert counted(fact, "input_tokens") > 0 and counted(fact, "output_tokens") > 0


WITHOUT_THE_SHOPKEEPER = """
{
  "version": 1,
  "profiles": [
    {
      "npc_id": "thief-uuid",
      "name": "Corin",
      "role": "market thief",
      "persona": "Light fingered and always three stalls ahead.",
      "speaking_style": "Clipped and evasive.",
      "relationships": []
    }
  ]
}
"""


async def test_an_unknown_npc_uses_a_generic_profile_and_records_the_gap(
    tmp_path: Path,
) -> None:
    async for harness in running(settings_for(tmp_path, WITHOUT_THE_SHOPKEEPER)):
        await harness.snapshot(active_conversation=active_conversation())
        await harness.turn()
        await harness.settle()

        assert len(harness.publisher.published) == 1
        assert harness.observed(MISSING_PROFILE) == [{"npc_id": SHOPKEEPER}]


async def test_a_malformed_profile_document_fails_readiness(tmp_path: Path) -> None:
    broken = '{"version": 1, "profiles": [{"npc_id": "shopkeeper-uuid"}]}'
    async for harness in running(settings_for(tmp_path, broken)):
        readiness = await harness.client.get("/health/ready")
        liveness = await harness.client.get("/health/live")

        assert readiness.status_code == 503
        assert liveness.status_code == 200


async def test_a_ready_service_reports_ready(harness: Harness) -> None:
    assert (await harness.client.get("/health/ready")).status_code == 200


@pytest.mark.parametrize(
    "missing",
    ["session_id", "conversation_id", "turn_id", "turn_index", "timestamp_ms", "text"],
)
async def test_an_invalid_turn_is_rejected_before_it_is_stored(
    harness: Harness, missing: str
) -> None:
    payload = conversation_turn()
    del payload[missing]
    response = await harness.ingest("conversation.turn", payload)

    assert response.status_code == 422
    assert await harness.pipeline.intake.turns.recent(SESSION_ID, CONVERSATION_ID, limit=8) == ()
